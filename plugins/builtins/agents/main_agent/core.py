"""main_agent/core.py — 信息流 + 思考 + 处理器"""

import json
import re
import time
import asyncio
import tempfile
import uuid
import os
from pathlib import Path
from typing import Dict, Any, List, Callable, Optional, Awaitable
from datetime import datetime

import core
from runtime._llm import is_llm_error_string
from .utils import Logger, _log, _now
from .prompts import PromptManager


# ============================================================
# 常量
# ============================================================

PROJECT = Path(__file__).parent.name
MEMORY_DIR = Path("data/memories/main_agent")
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
EXPERIENCE_DIR = MEMORY_DIR / "experiences"
EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)

MAX_RECURSION_DEPTH = 40
MAX_ERROR_MESSAGE_LENGTH = 500




# ============================================================
# 异常类型
# ============================================================

class ContentSafetyBlockedError(RuntimeError):
    """LLM 调用失败（内容安全拦截 / 网络错误 / 超时等）"""
    pass


class InterruptError(RuntimeError):
    """用户中断"""
    pass
# ============================================================
# 经验背包读写
# ============================================================

def _get_experience_file(session_id: str) -> Path:
    return EXPERIENCE_DIR / f"{session_id}_backpack.txt"


def _load_experience_backpack(session_id: str) -> str:
    file = _get_experience_file(session_id)
    if file.exists():
        return file.read_text(encoding="utf-8").strip()
    return ""


def _save_experience_backpack(session_id: str, content: str):
    _get_experience_file(session_id).write_text(content, encoding="utf-8")


# ============================================================
# SmartRetryPolicy — 智能重试
# ============================================================

class SmartRetryPolicy:
    RETRYABLE_CALLS = {
        "use_tool": {"max_retries": 1, "delay": 0.5},
        "check_environment": {"max_retries": 1, "delay": 0.5},
    }

    TRANSIENT_ERRORS = [
        "timeout", "connection", "temporarily",
        "超时", "连接", "unavailable", "timed out"
    ]

    LLM_FIX_PATTERNS = [
        "缺少 target 参数", "缺少 packages 参数",
        "缺少", "参数格式错误", "Plugin not found"
    ]

    @classmethod
    def should_retry_directly(cls, call: str, error_msg: str) -> bool:
        if call not in cls.RETRYABLE_CALLS:
            return False
        return any(e in error_msg.lower() for e in cls.TRANSIENT_ERRORS)

    @classmethod
    def should_ask_llm_to_fix(cls, error_msg: str) -> bool:
        return any(pattern in error_msg for pattern in cls.LLM_FIX_PATTERNS)

    @classmethod
    def get_retry_config(cls, call: str) -> dict:
        return cls.RETRYABLE_CALLS.get(call, {"max_retries": 0, "delay": 0})


# ============================================================
# FastValidator — LLM 输出即时验证
# ============================================================

class FastValidator:
    # ★ 只允许两种格式
    VALID_CALLS = {"use_tool", "reply"}

    @classmethod
    def validate(cls, output: dict) -> dict:

        if output.get("call") == "_retry":
            return output

        # ★★★ 检测是否被标记为多个 JSON ★★★
        if output.get("_multiple_json_detected"):
            json_count = output.get("_json_count", 2)
            output["_validation_failed"] = True
            output["_validation_reason"] = f"❌ 检测到 {json_count} 个 JSON 对象！每轮只能输出 1 个 JSON。"
            return output

        # ★ 如果有 use_tool 字段（新格式兼容）
        if "use_tool" in output:
            target = output.get("use_tool")
            if not target:
                output["_validation_failed"] = True
                output["_validation_reason"] = "缺少 use_tool 目标"
                return output
            # 转换为标准格式
            params = output.get("参数", {})
            output["call"] = "use_tool"
            output["args"] = {
                "target": target,
                "params": params
            }
            output.pop("use_tool", None)
            output.pop("参数", None)

        # ★ 检查 call 字段
        call = output.get("call", "")

        # 如果没有 call，报错
        if not call:
            output["_validation_failed"] = True
            output["_validation_reason"] = "缺少 call 字段，必须为 use_tool 或 reply"
            return output

        # 只允许 use_tool 和 reply
        if call not in cls.VALID_CALLS:
            output["_validation_failed"] = True
            output["_validation_reason"] = f"未知 call '{call}'，只允许 use_tool 或 reply"
            return output

        # ★ reply 检查
        if call == "reply":
            content = output.get("content", "")
            if not content:
                output["content"] = "好的，已处理"
                Logger.info("FastValidator: 补充空 content")
            return output

        # ★ use_tool 检查
        if call == "use_tool":
            args = output.get("args", {})

            if not args:
                output["_validation_failed"] = True
                output["_validation_reason"] = "缺少 args"
                return output

            target = args.get("target")
            if not target:
                output["_validation_failed"] = True
                output["_validation_reason"] = "缺少 target"
                return output

            # ★ 处理 main_agent 调用
            if target == "builtins/agents/main_agent":
                action = args.get("action", "")
                params = args.get("params", {})
                if not isinstance(params, dict):
                    params = {}
                content = params.get("content", "")

                # 情况 A：想回复用户 → 自动转成 call=reply
                if action == "reply" and content:
                    Logger.info("FastValidator: use_tool+main_agent.reply 自动转成 call=reply")
                    output["call"] = "reply"
                    output["content"] = content
                    output["args"] = {}
                    output.pop("_validation_failed", None)
                    output.pop("_validation_reason", None)
                    return output

                # 情况 B：其他 main_agent 调用 → 拦住
                output["_validation_failed"] = True
                output["_validation_reason"] = (
                    "禁止 use_tool 直接调用 builtins/agents/main_agent。\n"
                    "要开分身做子任务，请用 task_manager.create。\n"
                    "要回复用户，直接输出纯文本（call=reply），不要包一层 use_tool。"
                )
                return output

            # ★ 自动修正 reply 路径
            if target == "reply" or target == "builtins/agents/reply":
                args["target"] = "builtins/agents/main_agent/reply"
                output["args"] = args
                Logger.info(f"FastValidator: 自动修正 target → builtins/agents/main_agent/reply")

            # ★ 确保 params 存在
            if "params" not in args:
                args["params"] = {}
                output["args"] = args

        # ★ 校验通过
        output.pop("_validation_failed", None)
        output.pop("_validation_reason", None)
        return output


# ============================================================
# AsyncTaskManager — 异步任务管理
# ============================================================

class AsyncTaskManager:
    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}

    def submit(self, session_id: str, coro: Awaitable):
        task = asyncio.create_task(coro)
        task.add_done_callback(lambda t: self._on_task_done(session_id, t))
        self._tasks[session_id] = task

    def _on_task_done(self, session_id: str, task: asyncio.Task):
        try:
            if task.exception():
                Logger.error(f"异步任务异常 [{session_id}]: {task.exception()}", task.exception())
        finally:
            self._tasks.pop(session_id, None)


async_task_manager = AsyncTaskManager()


# ============================================================
# 信息流
# ============================================================

MAX_FLOW_ENTRIES = 500


class InformationFlow:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.memories_dir = MEMORY_DIR
        self.memories_dir.mkdir(parents=True, exist_ok=True)

        # ★★★ 改为按日期分目录 ★★★
        date_key = datetime.now().strftime("%Y-%m-%d")
        flow_dir = self.memories_dir / "flows" / date_key
        flow_dir.mkdir(parents=True, exist_ok=True)
        self.flow_file = flow_dir / f"{session_id}.json"
        self.backup_file = flow_dir / f"{session_id}_backup.json"

        self._flow: List[dict] = []
        self._load()

    def _load(self):
        """从根目录加载完整信息流"""
        root_file = self.memories_dir / f"{self.session_id}_flow.json"
        if root_file.exists():
            try:
                data = json.loads(root_file.read_text(encoding="utf-8"))
                self._flow = data[-MAX_FLOW_ENTRIES:] if len(data) > MAX_FLOW_ENTRIES else data
                Logger.debug(f"加载信息流: {len(self._flow)} 条")
            except Exception as e:
                Logger.error(f"加载信息流失败: {e}")
                self._flow = []
        else:
            self._flow = []
            Logger.debug(f"新建信息流: {self.session_id}")

    async def _save_locked(self):
        """原子保存：根目录全量 + 日期目录增量"""
        content = json.dumps(self._flow, ensure_ascii=False, indent=2, default=str)
        
        # ============================================================
        # 1. 写入根目录（全量，用于重启恢复）
        # ============================================================
        root_file = self.memories_dir / f"{self.session_id}_flow.json"
        
        temp_fd, temp_path = tempfile.mkstemp(
            dir=str(self.memories_dir),
            prefix=f".{root_file.name}_",
            suffix=".tmp"
        )
        try:
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            
            # ★ os.replace 原子操作，直接覆盖，不需要 backup
            os.replace(temp_path, str(root_file))
        except Exception as e:
            Logger.error(f"保存根目录信息流失败: {e}")
            try:
                os.close(temp_fd)
                os.unlink(temp_path)
            except:
                pass

        # ============================================================
        # 2. 追加写入日期目录（只有今天的条目）
        # ============================================================
        date_key = datetime.now().strftime("%Y-%m-%d")
        flow_dir = self.memories_dir / "flows" / date_key
        flow_dir.mkdir(parents=True, exist_ok=True)
        date_file = flow_dir / f"{self.session_id}.json"
        
        # ★★★ 只取今天的条目 ★★★
        today = datetime.now().date()
        today_entries = [
            e for e in self._flow
            if datetime.fromisoformat(e.get("timestamp", "")).date() == today
        ]
        
        if not today_entries:
            return  # 今天没有新条目，跳过
        
        today_content = json.dumps(today_entries, ensure_ascii=False, indent=2, default=str)
        
        temp_fd2, temp_path2 = tempfile.mkstemp(
            dir=str(flow_dir),
            prefix=f".{date_file.name}_",
            suffix=".tmp"
        )
        try:
            with os.fdopen(temp_fd2, 'w', encoding='utf-8') as f:
                f.write(today_content)
                f.flush()
                os.fsync(f.fileno())
            
            # ★ os.replace 原子操作，直接覆盖
            os.replace(temp_path2, str(date_file))
        except Exception as e:
            Logger.error(f"保存日期归档失败: {e}")
            try:
                os.close(temp_fd2)
                os.unlink(temp_path2)
            except:
                pass

    async def append_user(self, content: str):
        """追加用户消息"""
        self._flow.append({
            "timestamp": datetime.now().isoformat(),
            "from": "user",
            "content": content
        })
        await self._save_locked()

    async def append_ai(self, think: str, output: dict):
        """追加 AI 消息"""
        valid_calls = {"reply", "ask_user", "use_tool", "create_tool", "fix_tool", 
                       "remove_tool", "query_plugin", "aicp_chat", "add_experience"}
        if output.get("call") not in valid_calls:
            return

        # ★ 检测 LLM 错误包装，不写 flow
        content = output.get("content", "")
        if is_llm_error_string(content):
            Logger.warn(f"append_ai: 检测到 LLM 错误包装，跳过写入 flow: {content[:100]}")
            return

        self._flow.append({
            "timestamp": datetime.now().isoformat(),
            "from": "ai",
            "think": think,
            "call": output.get("call"),
            "content": output.get("content"),
            "args": output.get("args"),
            "retain": output.get("retain", 0)
        })
        await self._save_locked()

    async def append_system(self, action: str, result_think: str, detail: dict = None,
                            extra: dict = None, retain: int = 1):
        """追加系统消息"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "from": "system",
            "action": action,
            "result": result_think,
            "detail": detail or {},
            "retain": retain,
        }
        if extra:
            entry["extra"] = extra
        self._flow.append(entry)
        await self._save_locked()

    def get_context(self, limit: int = 20) -> str:
        if not self._flow:
            return "（暂无对话记录）"

        entries = self._flow[-limit:]
        lines = []
        total = len(entries)

        ARGS_MAX_OUT = 300
        PARAMS_MAX_OUT = 200
        RESULT_MAX_OUT = 200

        # ★ 缩进单位
        IND = "  "

        SYSTEM_NOISE_KEYWORDS = [
            "抱歉，处理过程中出现错误",
            "请稍后重试",
            "暂时不可用",
        ]

        def is_reply_noise(text: str) -> bool:
            if not text:
                return True
            return any(k in text for k in SYSTEM_NOISE_KEYWORDS)

        def shrink(text: str, max_len: int) -> str:
            if not text:
                return ""
            if len(text) <= max_len:
                return text
            return text[:max_len] + f"... (已收缩，共 {len(text)} 字)"

        # ★ 缩进辅助：多行内容每行都加缩进
        def indent_block(text: str, level: int) -> str:
            pad = IND * level
            return "\n".join(pad + line if line else line for line in text.split("\n"))

        for idx, entry in enumerate(entries):
            ts_full = entry.get("timestamp", "")
            ts = ts_full[5:19] if ts_full else ""
            from_user = entry.get("from", "")
            distance = total - 1 - idx

            # ============================================================
            # 用户消息 → 0 级
            # ============================================================
            if from_user == "user":
                content = entry.get("content", "")
                if content:
                    lines.append(f"[{ts}] 👤 {content}")

            # ============================================================
            # AI 消息
            # ============================================================
            elif from_user == "ai":
                think = entry.get("think", "")
                content = entry.get("content", "")
                call = entry.get("call", "")
                args = entry.get("args", {})
                retain = entry.get("retain", 0)
                in_retain = retain <= 0 or distance < retain

                # ---- think → 1 级 ----
                if think:
                    lines.append(f"{IND}[{ts}] 💭 {think}")

                # ---- reply → 1 级 ----
                if content and call in ("reply", "ask_user") and not is_reply_noise(content):
                    lines.append(f"{IND}[{ts}] 🤖 {content}")

                # ---- 调用类 → 2 级 ----
                if call and call not in ("reply", "ask_user"):
                    args_str = json.dumps(args, ensure_ascii=False, indent=2)
                    display = args_str if in_retain else shrink(args_str, ARGS_MAX_OUT)
                    # 标题一行 2 级，参数块再深一级
                    lines.append(f"{IND}{IND}[{ts}] 🔧 调用 {call}")
                    lines.append(indent_block(display, 3))

            # ============================================================
            # system 消息 → 3 级
            # ============================================================
            elif from_user == "system":
                action = entry.get("action", "")
                result = entry.get("result", "")
                detail = entry.get("detail", {})
                retain = entry.get("retain", 0)
                in_retain = retain <= 0 or distance < retain

                if action == "call_start":
                    call_id = detail.get("call_id", "")
                    target = detail.get("target", "")
                    status = detail.get("status", "pending")
                    params_str = json.dumps(detail.get("params", {}), ensure_ascii=False)
                    display = params_str if in_retain else shrink(params_str, PARAMS_MAX_OUT)
                    tag = "⏳" if status == "processing" else ("✅" if status == "completed" else "")
                    lines.append(f"{IND}{IND}{IND}[{ts}] 📤 {target} [id={call_id}] {tag}")
                    lines.append(indent_block(display, 4))

                elif action == "call_end":
                    call_id = detail.get("call_id", "")
                    result_str = json.dumps(detail.get("result", {}), ensure_ascii=False, indent=2)
                    display = result_str if in_retain else shrink(result_str, RESULT_MAX_OUT)
                    lines.append(f"{IND}{IND}{IND}[{ts}] ✅ [id={call_id}]")
                    lines.append(indent_block(display, 4))

                elif action == "async_processing":
                    trace_id = detail.get("trace_id", "")[:8]
                    lines.append(f"{IND}{IND}{IND}[{ts}] ⏳ 已提交 [trace={trace_id}]")

                elif action == "async_callback":
                    call_id = detail.get("call_id", "")
                    trace_id = detail.get("trace_id", "")[:8]
                    matched = detail.get("matched", False)
                    result_str = json.dumps(detail.get("result", {}), ensure_ascii=False, indent=2)
                    display = result_str if in_retain else shrink(result_str, RESULT_MAX_OUT)
                    match_str = "✅" if matched else "⚠️"
                    lines.append(f"{IND}{IND}{IND}[{ts}] 📨 [id={call_id}][trace={trace_id}] {match_str}")
                    lines.append(indent_block(display, 4))
                elif action == "task_summary":        # ★ 新增
                    lines.append(f"{IND}{IND}{IND}[{ts}] 📋 {result}")
                elif action == "call_error":
                    call_id = detail.get("call_id", "")
                    error = detail.get("error", "")
                    display = error if in_retain else shrink(error, RESULT_MAX_OUT)
                    lines.append(f"{IND}{IND}{IND}[{ts}] ❌ [id={call_id}]")
                    lines.append(indent_block(display, 4))

                elif action == "validation_error":
                    extra = entry.get("extra", {})
                    lines.append(f"{IND}{IND}{IND}[{ts}] ❌ {extra.get('full_result', '') or result}")

                else:
                    if result and not is_reply_noise(result):
                        lines.append(f"{IND}{IND}{IND}[{ts}] 📊 {result}")
                    full_result = entry.get("extra", {}).get("full_result", "")
                    if full_result:
                        display = full_result if in_retain else shrink(full_result, RESULT_MAX_OUT)
                        lines.append(indent_block(display, 4))

        return "\n".join(lines)


# ============================================================
# 会话锁
# ============================================================

_session_processing_locks: Dict[str, asyncio.Lock] = {}
_session_flows: Dict[str, InformationFlow] = {}


def _get_processing_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _session_processing_locks:
        _session_processing_locks[session_id] = asyncio.Lock()
    return _session_processing_locks[session_id]


def _get_or_create_flow(session_id: str) -> InformationFlow:
    if session_id not in _session_flows:
        _session_flows[session_id] = InformationFlow(session_id)
    return _session_flows[session_id]


# ============================================================
# WebSocket 推送
# ============================================================

async def _push_progress(agent, session_id: str, step: str, msg: str):
    try:
        await agent.system.call(core.Envelop(
            sender="builtins/agents/main_agent",
            receiver="os/_websocket",
            payload={
                "action": "push",
                "channel_id": f"pa_{session_id}",
                "data": {"type": "progress", "step": step, "msg": msg, "message": msg}
            }
        ))
    except Exception as e:
        Logger.warn(f"[WS] 推送进度失败: {e}")


async def _push_chat(agent, session_id: str, content: str):
    try:
        await agent.system.call(core.Envelop(
            sender="builtins/agents/main_agent",
            receiver="os/_websocket",
            payload={
                "action": "push",
                "channel_id": f"pa_{session_id}",
                "data": {"type": "chat", "content": content}
            }
        ))
    except Exception as e:
        Logger.warn(f"[WS] 推送消息失败: {e}")


async def _push_stream(agent, session_id: str, chunk: str):
    try:
        from plugins.builtins.notify.ws_notify import push as ws_push
        await ws_push(agent, f"pa_{session_id}", {"type": "summary_stream", "chunk": chunk})
    except Exception as e:
        Logger.warn(f"[WS] 流式推送失败: {e}")


# ============================================================
# LLM 输出解析器（完整原版 — 硬脱壳）
# ============================================================

class LLMOutputParser:
    def __init__(self):
        self._parsers = [
            self._parse_valid_json,
            self._parse_design_json,
            self._parse_success_response,
            self._parse_system_info,
            self._parse_fallback,
        ]
        self._max_extract_len = 150000
        
        # 有效的 call 值
        self._valid_calls = {
            "use_tool", "reply"
        }
    def _check_missing_content_block(self, result: dict) -> str:
        """检测哪些工具需要 @@CONTENT@@ 块但缺失。
        
        返回非空字符串表示缺失，字符串内容是工具描述。
        返回空字符串表示不需要块或块已存在。
        
        判断依据：params 里完全没有对应字段（不是字段为空）。
        显式写 "content": "" 表示空内容，放行。
        """
        if result.get("call") != "use_tool":
            return ""
        
        args = result.get("args", {})
        if not isinstance(args, dict):
            return ""
        
        target = args.get("target", "")
        action = args.get("action", "")
        params = args.get("params", {})
        if not isinstance(params, dict):
            params = {}
        
        # ★ 先取 mode，clear 模式不需要 content
        mode = params.get("mode", "")
        # ★ 也接受 action="clear" 的写法（LLM 可能把 clear 当 action）
        if action == "clear":
            mode = "clear"
        
        if target == "builtins/tools/add_experience":
            if mode == "clear":
                return ""  # clear 不需要 experience
            if "experience" not in params:
                return "add_experience（需要 experience）"
        elif target == "builtins/tools/add_task_board":
            if mode == "clear":
                return ""  # clear 不需要 content
            if "content" not in params:
                return "add_task_board（需要 content）"
        elif target == "builtins/tools/aicp_chat":
            if "task" not in params:
                return "aicp_chat（需要 task）"
        elif target == "builtins/tools/fix_tool":
            if "issue" not in params:
                return "fix_tool（需要 issue）"
        elif target == "os/file_utils_api":
            if action in ("write_file", "append_file", "apply_patch"):
                if "content" not in params:
                    return f"{action}（需要 content）"
        
        return ""

    def parse(self, raw: str) -> dict:
        if not isinstance(raw, str):
            Logger.error(f"LLMOutputParser.parse 收到非字符串输入: {type(raw)}")
            return self._get_error_response("输入类型错误", "抱歉，处理过程中出现错误，请稍后重试")

        try:
            raw_stripped = raw.strip()

            if not raw_stripped:
                return self._get_error_response("空输入", "抱歉，我遇到了一些问题...")

            if len(raw_stripped) > self._max_extract_len:
                Logger.warn(f"LLM 输出过长（{len(raw_stripped)} 字符），截断到 {self._max_extract_len}")
                raw_stripped = raw_stripped[:self._max_extract_len]

                        # ========== 第一步：提取 @@CONTENT@@ 块 ==========
            content_block, raw_without_content = self._extract_content_block(raw_stripped)

            # ========== 第一步半：单行 patch 兜底检测 ==========
            # 模型有时会把多行 patch 压成一行，re.DOTALL 拿到手就是一行，无法还原。
            # 检测：块内容非空、无换行、且含 diff 语义字符 → 触发重试。
            if content_block is not None and "\n" not in content_block:
                diff_markers = ("@@ ", "--- ", "+++ ", "@@ -", "--- a/", "+++ b/")
                if any(marker in content_block for marker in diff_markers):
                    Logger.warn(
                        f"检测到单行 patch（换行被压缩），触发重试。"
                        f"内容前 200 字: {content_block[:200]}"
                    )
                    return self._make_retry_dict(
                        "@@CONTENT@@ 块内是单行 patch，换行被压缩。"
                        "diff 的每一行必须独立成行（@@ / --- / +++ / 空格 / - / + 开头各占一行），"
                        "请重新输出，块内保留换行。"
                    )

            # ========== 第二步：判断是否有 JSON 意图 ==========
            has_json_intent = self._has_json_intent(raw_without_content)

            # ========== 第三步：查找并解析 JSON ==========
            result, json_found = self._find_and_parse_json(raw_without_content)

            # ========== 第四步：根据结果分流 ==========

            # 情况 A：解析出合法 JSON
            if json_found and isinstance(result, dict):
                call_val = self._normalize_call_field(result)

                if call_val is None:
                    # 有 JSON 但没 call → 重试
                    Logger.warn("JSON 缺少 call 字段，触发重试")
                    return self._make_retry_dict(
                        "JSON 缺少 call 字段，必须包含 call（use_tool 或 reply）。请重新输出。"
                    )

                result["call"] = call_val

                if content_block is not None:
                    self._inject_content_block(result, content_block)
                else:
                    # ★ 检测：需要 content 块但块缺失
                    missing = self._check_missing_content_block(result)
                    if missing:
                        Logger.warn(f"检测到缺少 @@CONTENT@@ 块: {missing}")
                        return self._make_retry_dict(
                            f"{missing} 需要大文本参数，但你没有输出 @@CONTENT@@ 块。"
                            f"请在 JSON 之后另起一行，写 @@CONTENT@@ ... @@END_CONTENT@@，"
                            f"块内放内容。注意：块内必须保留换行。"
                        )

                for parser in self._parsers:
                    try:
                        parsed = parser(result)
                        if parsed is not None and isinstance(parsed, dict):
                            return self._ensure_defaults(parsed)
                    except Exception as e:
                        Logger.warn(f"parser {parser.__name__} 异常: {e}")
                        continue

                return self._ensure_defaults(result)

            # 情况 B：没解析出 JSON，但有 JSON 意图 → 重试
            if has_json_intent:
                Logger.warn("检测到 JSON 意图但解析失败，触发重试")
                preview = raw_stripped[:300]
                return self._make_retry_dict(
                    f"JSON 解析失败|{preview}"
                )

            # 情况 C：没有 JSON 意图 → 纯文本回复
            Logger.info("纯文本回复")
            return self._make_reply_dict(
                think="LLM 直接回复",
                content=raw_stripped[:2000]
            )

        except Exception as e:
            Logger.error(f"LLM 输出解析异常: {e}")
            import traceback
            Logger.error(traceback.format_exc())
            return self._get_error_response("解析异常", "抱歉，处理过程中出现错误，请稍后重试")

    # ============================================================
    # JSON 意图判断
    # ============================================================

    def _has_json_intent(self, raw: str) -> bool:
        """判断 LLM 输出是否有 JSON 意图

        规则：
        - 找到 { 后，200 字符内出现 "call" / "args" / "target" / "think" 之一
        - 或者整个文本以 { 开头、以 } 结尾
        """
        if not raw:
            return False

        s = raw.strip()

        # 整体像 JSON
        if s.startswith("{") and s.endswith("}"):
            return True

        # 找 { 后 200 字符内是否有 JSON 字段名
        idx = s.find("{")
        if idx == -1:
            return False

        tail = s[idx:idx + 200]
        json_keys = ('"call"', "'call'", '"args"', "'args'", '"target"', "'target'", '"think"', "'think'")

        return any(kw in tail for kw in json_keys)

    # ============================================================
    # 重试标记
    # ============================================================

    def _make_retry_dict(self, reason: str) -> dict:
        """构造重试响应"""
        return {
            "think": "格式错误，需要重试",
            "call": "_retry",
            "content": "",
            "args": {},
            "_validation_reason": reason,
        }

    # ============================================================
    # 新增：提取 @@CONTENT@@ 块
    # ============================================================
    
    def _extract_content_block(self, raw: str) -> tuple:
        """提取 @@CONTENT@@ 块，支持不完整结束标记

        空块返回 (None, raw)，不注入。
        """
        import re
        pattern = r'@@CONTENT@@\s*\n?(.*?)(?:@@END_CONTENT@@|$)'
        match = re.search(pattern, raw, re.DOTALL)
        if match:
            content = match.group(1).strip()
            if not content:
                # ★ 空块不返回，直接当没有
                Logger.debug("@@CONTENT@@ 块为空，忽略")
                return None, raw
            cleaned = raw[:match.start()] + raw[match.end():]
            return content, cleaned
        return None, raw

    # ============================================================
    # 新增：查找并解析 JSON
    # ============================================================
    
    def _find_and_parse_json(self, raw: str) -> tuple:
        """只取第一个 { ... }，不扫描后续。

        第一个不合法 → 返回 (parsed, True)，让调用方判断是否 _retry。
        """
        if not raw:
            return None, False

        # 剥离 markdown 代码块（如果整个文本被 ```json 包裹）
        unwrapped = self._unwrap_code_block(raw)
        if unwrapped != raw:
            raw = unwrapped

        # 找第一个 {
        start = raw.find('{')
        if start == -1:
            return None, False

        # 找第一个配对的 }
        depth = 0
        in_string = False
        escape = False
        end = -1

        for i in range(start, len(raw)):
            ch = raw[i]
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end == -1:
            # 括号不全，用修复逻辑
            candidate = raw[start:]
            try:
                fixed = self._fix_json_unterminated_string(candidate)
                parsed = json.loads(fixed)
                if isinstance(parsed, dict):
                    return parsed, True
            except Exception:
                pass
            return None, False

        # 只解析第一个
        candidate = raw[start:end + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed, True
        except Exception:
            pass

        return None, False

    @staticmethod
    def _unwrap_code_block(raw: str) -> str:
        """剥离 ```json ... ``` / ``` ... ``` 代码块包裹
        
        优先提取 ```json 块；如果没有，再试普通 ``` 块。
        代码块外面的文本保留，供后续扫描。
        """
        import re
        
        # 优先 ```json ... ```
        m = re.search(r'```json\s*\n?(.*?)\n?```', raw, re.DOTALL)
        if m:
            inner = m.group(1).strip()
            before = raw[:m.start()].strip()
            after = raw[m.end():].strip()
            parts = [p for p in (before, inner, after) if p]
            return "\n".join(parts)
        
        # 再试普通 ``` ... ```
        m = re.search(r'```\s*\n?(.*?)\n?```', raw, re.DOTALL)
        if m:
            inner = m.group(1).strip()
            before = raw[:m.start()].strip()
            after = raw[m.end():].strip()
            parts = [p for p in (before, inner, after) if p]
            return "\n".join(parts)
        
        return raw


    def _is_valid_llm_output(self, result: dict) -> bool:
        """检查是否是有效的 LLM 输出"""
        if "call" in result:
            return True
        if "args" in result and isinstance(result["args"], dict) and result["args"].get("target"):
            return True
        if "content" in result and len(result.keys()) <= 3:
            return True
        return False

    # ============================================================
    # 新增：归一化 call 字段
    # ============================================================
    
    def _normalize_call_field(self, result: dict) -> str:
        """归一化 call 字段，返回标准 call 值"""
        if not isinstance(result, dict):
            return None
        
        # 大小写不敏感查找 call 字段
        call_val = None
        for key in ("call", "Call", "CALL"):
            if key in result:
                call_val = result.pop(key)
                break
        
        if call_val:
            call_lower = str(call_val).lower()
            if call_lower in self._valid_calls:
                return call_lower
        
        # 没有 call 字段，尝试推断
        args = result.get("args", {})
        if isinstance(args, dict):
            # 有 args.target → use_tool
            if args.get("target"):
                Logger.warn(f"缺少 call 字段，但有 args.target={args.get('target')}，自动补全为 use_tool")
                return "use_tool"
        
        # 有顶层 target → use_tool
        if "target" in result:
            Logger.warn("缺少 call 字段，但有顶层 target，自动补全为 use_tool")
            # 把顶层字段移到 args
            if "args" not in result or not isinstance(result["args"], dict):
                result["args"] = {}
            result["args"]["target"] = result.pop("target")
            if "action" in result:
                result["args"]["action"] = result.pop("action")
            if "params" in result:
                result["args"]["params"] = result.pop("params")
            return "use_tool"
        
        # 有 content 且字段少 → reply
        if "content" in result and len(result.keys()) <= 3:
            Logger.warn("缺少 call 字段，但有 content，自动补全为 reply")
            return "reply"
        
        return None

    # ============================================================
    # 新增：注入 content 块
    # ============================================================
    
    def _inject_content_block(self, result: dict, content_block: str):
        """注入 content 块到正确位置（按 target/action 映射字段名）"""
        call = result.get("call", "")

        if call in ("reply", "ask_user"):
            result["content"] = content_block
            Logger.debug(f"{call} 注入 content 块")
            return

        if call != "use_tool":
            Logger.debug(f"call={call} 的 content 块被忽略")
            return

        args = result.get("args", {})
        if not isinstance(args, dict):
            args = {}
            result["args"] = args

        params = args.get("params", {})
        if not isinstance(params, dict):
            params = {}
            args["params"] = params

        target = args.get("target", "")
        action = args.get("action", "")

        # ★ clear 模式不需要注入（mode 在 params 里，action 在 args 顶层）
        mode = params.get("mode", "")
        if mode == "clear" or action == "clear":
            Logger.debug(f"clear 模式，跳过 content 注入 (target={target}, mode={mode}, action={action})")
            return

        # ★ 按目标工具映射字段名
        if target == "builtins/tools/add_experience":
            params["experience"] = content_block
            Logger.debug("content 块 → add_experience.experience")
        elif target == "builtins/tools/add_task_board":
            params["content"] = content_block
            Logger.debug("content 块 → add_task_board.content")
        elif target == "builtins/tools/aicp_chat":
            params["task"] = content_block
        elif target == "builtins/tools/fix_tool":
            params["issue"] = content_block
            Logger.debug("content 块 → fix_tool.issue")
        elif target == "os/file_utils_api" and action in ("write_file", "append_file", "apply_patch"):
            params["content"] = content_block
            Logger.debug(f"content 块 → file_utils_api.{action}.content")
        else:
            # 兜底：默认塞 content
            params["content"] = content_block
            Logger.debug(f"content 块 → 兜底塞 params.content (target={target})")

    # ============================================================
    # 原有方法保留
    # ============================================================

    def _count_json_objects(self, raw: str) -> int:
        """统计 raw 中有多少个完整的、包含 call/think/args 任一字段的 JSON 对象"""
        if not raw:
            return 0
        
        count = 0
        i = 0
        
        while i < len(raw):
            start = raw.find('{', i)
            if start == -1:
                break
            
            depth = 0
            in_string = False
            escape = False
            end = -1
            
            for j in range(start, len(raw)):
                ch = raw[j]
                if escape:
                    escape = False
                    continue
                if ch == '\\':
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
            
            if end != -1:
                try:
                    obj = json.loads(raw[start:end+1])
                    # ★ 改动：只要含 call/think/args 任一字段就计数
                    if isinstance(obj, dict) and any(k in obj for k in ("call", "think", "args")):
                        count += 1
                except:
                    pass
                i = end + 1
            else:
                break
        
        return count

    def _extract_outer_json_and_content(self, raw: str):
        """找最外层的 JSON 对象，并且提取 @CONTENT@ 块"""
        if not raw:
            return None, None

        start = raw.find('{')
        if start == -1:
            return None, None

        depth = 0
        in_string = False
        escape = False
        json_end = -1

        for i, ch in enumerate(raw[start:], start):
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    json_end = i
                    break

        if json_end == -1:
            content_pos = raw.find('@@CONTENT@@', start)
            if content_pos != -1:
                json_end = content_pos - 1
            else:
                json_end = len(raw) - 1
            
            while json_end > start and raw[json_end] in ' \t\n\r':
                json_end -= 1

        json_str = raw[start:json_end + 1].strip()

        rest = raw[json_end + 1:].strip()
        content_block = None

        if rest:
            content_start = rest.find('@@CONTENT@@')
            if content_start != -1:
                content_start += len('@@CONTENT@@')
                rest_after = rest[content_start:].lstrip('\n')
                end_idx = rest_after.find('@@END_CONTENT@@')
                if end_idx != -1:
                    content_block = rest_after[:end_idx].rstrip('\n')
                else:
                    content_block = rest_after

        return json_str, content_block

    def _fix_json_unterminated_string(self, raw: str) -> str:
        """修复 JSON 字符串里未转义的引号和控制字符"""
        result = []
        in_string = False
        escape = False

        for ch in raw:
            if escape:
                result.append(ch)
                escape = False
                continue
            if ch == '\\':
                result.append(ch)
                escape = True
                continue
            if ch == '"':
                if in_string:
                    in_string = False
                    result.append(ch)
                else:
                    in_string = True
                    result.append(ch)
                continue
            if in_string and ch == '\n':
                result.append('\\n')
                continue
            if in_string and ch == '\r':
                result.append('\\r')
                continue
            if in_string and ch == '\t':
                result.append('\\t')
                continue
            result.append(ch)

        fixed = ''.join(result)

        # ★★★ 用栈扫描，按实际嵌套顺序补齐闭合符 ★★★
        fixed += self._build_closing_suffix(fixed)

        return fixed

    @staticmethod
    def _build_closing_suffix(s: str) -> str:
        """扫描字符串，构建需要补的闭合符后缀
        
        用栈处理嵌套，按弹出顺序生成闭合符：
        如果栈里剩 [{，则补 }]（先闭后开的）；
        如果栈里剩 {[，则补 ]}。
        """
        stack = []
        in_string = False
        escape = False

        for ch in s:
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in ('{', '['):
                stack.append(ch)
            elif ch == '}':
                if stack and stack[-1] == '{':
                    stack.pop()
            elif ch == ']':
                if stack and stack[-1] == '[':
                    stack.pop()

        suffix = ''
        while stack:
            c = stack.pop()
            if c == '{':
                suffix += '}'
            elif c == '[':
                suffix += ']'

        return suffix

    def _extract_fields_fallback(self, raw: str) -> dict:
        """JSON 解析失败时的兜底：用正则提取关键字段"""
        result = {
            "think": "",
            "call": "reply",
            "retain": 3,
            "args": {}
        }

        m = re.search(r'"think"\s*:\s*"([^"]*)"', raw)
        if m:
            result["think"] = m.group(1)

        m = re.search(r'"call"\s*:\s*"([^"]*)"', raw)
        if m:
            result["call"] = m.group(1)

        m = re.search(r'"retain"\s*:\s*(\d+)', raw)
        if m:
            result["retain"] = int(m.group(1))

        m = re.search(r'"target"\s*:\s*"([^"]*)"', raw)
        if m:
            result.setdefault("args", {})["target"] = m.group(1)

        m = re.search(r'"action"\s*:\s*"([^"]*)"', raw)
        if m:
            result.setdefault("args", {})["action"] = m.group(1)

        m = re.search(r'"plugin"\s*:\s*"([^"]*)"', raw)
        if m:
            result.setdefault("args", {})["plugin"] = m.group(1)

        m = re.search(r'"task"\s*:\s*"([^"]*)"', raw)
        if m:
            result.setdefault("args", {})["task"] = m.group(1)

        m = re.search(r'"name"\s*:\s*"([^"]*)"', raw)
        if m:
            result.setdefault("args", {})["name"] = m.group(1)

        m = re.search(r'"description"\s*:\s*"([^"]*)"', raw)
        if m:
            result.setdefault("args", {})["description"] = m.group(1)

        m = re.search(r'"issue"\s*:\s*"([^"]*)"', raw)
        if m:
            result.setdefault("args", {})["issue"] = m.group(1)

        m = re.search(r'"experience"\s*:\s*"([^"]*)"', raw)
        if m:
            result.setdefault("args", {})["experience"] = m.group(1)

        m = re.search(r'"mode"\s*:\s*"([^"]*)"', raw)
        if m:
            result.setdefault("args", {})["mode"] = m.group(1)

        params_match = re.search(r'"params"\s*:\s*\{([\s\S]*?)\}\s*\}\s*$', raw)
        if params_match:
            params_str = params_match.group(1)
            params = {}
            for pm in re.finditer(r'"(\w+)"\s*:\s*"([^"]*)"', params_str):
                key, val = pm.group(1), pm.group(2)
                params[key] = val
            for pm in re.finditer(r'"(\w+)"\s*:\s*(\d+)', params_str):
                key, val = pm.group(1), int(pm.group(2))
                if key not in params:
                    params[key] = val
            result.setdefault("args", {})["params"] = params

        if not result["think"] and result["call"] == "reply" and not result["args"]:
            return {}

        return result

    def _parse_valid_json(self, result: dict) -> dict | None:
        if "call" in result:
            return result
        return None

    def _parse_design_json(self, result: dict) -> dict | None:
        design_keys = {"project_name", "pages", "layout", "components", "design"}
        if any(k in result for k in design_keys):
            Logger.info("LLM 返回了设计数据，转为自然语言")
            try:
                natural_text = self._design_to_text(result)
                return self._make_reply_dict(think="将设计数据转为自然语言回复", content=natural_text)
            except Exception as err:
                Logger.warning(f"_design_to_text转换失败 {err}，降级直接输出json")
                return self._json_to_reply(result)
        return None

    def _parse_success_response(self, result: dict) -> dict | None:
        if "success" in result or "ok" in result:
            msg = result.get("message") or result.get("think") or "操作执行成功"
            return self._make_reply_dict(think="系统操作完成", content=str(msg))
        return None

    def _parse_system_info(self, result: dict) -> dict | None:
        if "call" not in result and ("projects" in result or "think" in result):
            return self._make_reply_dict(
                think="整理系统信息",
                content=json.dumps(result, ensure_ascii=False, indent=2)
            )
        return None

    def _parse_fallback(self, result: dict) -> dict:
        return self._json_to_reply(result)

    def _ensure_defaults(self, result: dict) -> dict:
        result.setdefault("think", "")
        result.setdefault("call", "reply")
        result.setdefault("args", {})

        call_val = result.get("call", "reply")
        if call_val in ("reply", "ask_user"):
            result.setdefault("content", "")
            if isinstance(result.get("content"), (dict, list)):
                result["content"] = json.dumps(result["content"], ensure_ascii=False, indent=2)
            result["content"] = str(result["content"])

        if not isinstance(result["args"], dict):
            result["args"] = {}
        result["think"] = str(result["think"])
        result["call"] = str(result["call"])
        return result

    def _get_error_response(self, think: str, content: str) -> dict:
        return {
            "think": think,
            "call": "reply",
            "content": content,
            "args": {},
            "_validation_failed": True,
            "_validation_reason": think
        }

    def _make_reply_dict(self, think: str, content: str) -> dict:
        return {"think": think, "call": "reply", "content": content, "args": {}}

    def _json_to_reply(self, data, think: str = "格式异常") -> dict:
        try:
            text = json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            text = str(data)
        return self._make_reply_dict(think=think, content=text)

    @staticmethod
    def _design_to_text(data: dict) -> str:
        lines = []
        project_name = data.get("project_name", "") or ""
        if project_name:
            lines.append(f"好的，以下是「{project_name}」的前端页面设计：\n")
        pages = data.get("pages", [])
        if not isinstance(pages, list):
            pages = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_name = page.get("page", "页面") or "页面"
            layout = page.get("layout", "") or ""
            lines.append(f"【{page_name}】")
            if layout:
                lines.append(f"整体布局：{layout}")
            lines.append("")
            sections = page.get("sections", [])
            if not isinstance(sections, list):
                continue
            for section in sections:
                if not isinstance(section, dict):
                    continue
                area = section.get("area", "") or ""
                lines.append(f"  {area}：")
                comps = section.get("components", [])
                if not isinstance(comps, list):
                    continue
                for comp in comps:
                    lines.append(f"    - {comp}")
                lines.append("")
        features = data.get("features", [])
        if isinstance(features, list) and features:
            lines.append("【主要功能】")
            for f in features:
                if isinstance(f, dict):
                    name = f.get("name", "") or ""
                    desc = f.get("description", "") or ""
                    lines.append(f"  - {name}：{desc}")
                else:
                    lines.append(f"  - {f}")
            lines.append("")
        lines.append("你觉得这个设计方向可以吗？有什么需要调整的？")
        return "\n".join(lines)

    @staticmethod
    def _design_to_text(data: dict) -> str:
        lines = []
        project_name = data.get("project_name", "") or ""
        if project_name:
            lines.append(f"好的，以下是「{project_name}」的前端页面设计：\n")
        pages = data.get("pages", [])
        if not isinstance(pages, list):
            pages = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_name = page.get("page", "页面") or "页面"
            layout = page.get("layout", "") or ""
            lines.append(f"【{page_name}】")
            if layout:
                lines.append(f"整体布局：{layout}")
            lines.append("")
            sections = page.get("sections", [])
            if not isinstance(sections, list):
                continue
            for section in sections:
                if not isinstance(section, dict):
                    continue
                area = section.get("area", "") or ""
                lines.append(f"  {area}：")
                comps = section.get("components", [])
                if not isinstance(comps, list):
                    continue
                for comp in comps:
                    lines.append(f"    - {comp}")
                lines.append("")
        features = data.get("features", [])
        if isinstance(features, list) and features:
            lines.append("【主要功能】")
            for f in features:
                if isinstance(f, dict):
                    name = f.get("name", "") or ""
                    desc = f.get("description", "") or ""
                    lines.append(f"  - {name}：{desc}")
                else:
                    lines.append(f"  - {f}")
            lines.append("")
        lines.append("你觉得这个设计方向可以吗？有什么需要调整的？")
        return "\n".join(lines)


# ============================================================
# LLM 思考引擎
# ============================================================

class LLMThinker:
    def __init__(self):
        self.parser = LLMOutputParser()
    
    
    async def think(self, agent, flow: InformationFlow, session_id: str, depth: int = 0) -> dict:
        llm = agent.llm
        if not llm:
            return {"think": "LLM 不可用", "call": "reply", "content": "抱歉，AI 服务暂时不可用", "args": {}}

        context = flow.get_context()
        system_prompt = PromptManager.build_system(session_id)
        user_prompt = PromptManager.build_user(session_id, context, depth)

        Logger.info("LLM 思考中...")

        channel = f"pa_{session_id}"
        raw = ""
        interrupted = False
        interrupted_reason = "未知原因"
        token_count = 0
        check_interval = 5

        THINK_MAX_CHARS = 1600        # think 字段超过这个长度就中断
        MAX_STREAM_TIME = 240         # 单次调用最长 240 秒

        start_time = time.time()

        try:
            async for token in llm.chat_stream([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]):
                raw += token
                token_count += 1

                # ★ 熔断 1：单次调用超时
                if time.time() - start_time > MAX_STREAM_TIME:
                    Logger.warn(f"单次 LLM 调用超过 {MAX_STREAM_TIME}s，中断生成")
                    interrupted = True
                    interrupted_reason = "单次调用超时"
                    try:
                        from plugins.builtins.notify.ws_notify import push as ws_push
                        await ws_push(agent, channel, {
                            "type": "error",
                            "content": f"⚠️ 生成超时（{MAX_STREAM_TIME}s），已中断，正在重试..."
                        })
                    except Exception:
                        pass
                    break

                # ★ 熔断 2：think 字段过长（且 call 还没出现）
                if '"think"' in raw and '"call"' not in raw:
                    think_start = raw.find('"think"')
                    if think_start != -1 and len(raw) - think_start > THINK_MAX_CHARS:
                        Logger.warn(f"think 字段过长（>{THINK_MAX_CHARS} 字符），中断生成")
                        interrupted = True
                        interrupted_reason = "think 字段过长"
                        try:
                            from plugins.builtins.notify.ws_notify import push as ws_push
                            await ws_push(agent, channel, {
                                "type": "error",
                                "content": "⚠️ think 字段过长，已中断，正在重试..."
                            })
                        except Exception:
                            pass
                        break

                # ★ 熔断 3：检测多个 JSON 对象（三层检测）
                # 只统计 @@CONTENT@@ 块之前的内容，避免块内 HTML/JSON 误触发
                before_content = raw.split("@@CONTENT@@")[0]

                # 第一层（早）：检测 } 后紧跟 { 的模式（每 token 都查，最敏感）
                # 匹配 "}{", "} {", "}\n{", "}\n\n{" 等
                if re.search(r'}\s*\{', before_content):
                    Logger.warn("检测到相邻的 JSON 对象（} 后紧跟 {），中断生成")
                    interrupted = True
                    interrupted_reason = "检测到多个 JSON 对象"
                    try:
                        from plugins.builtins.notify.ws_notify import push as ws_push
                        await ws_push(agent, channel, {
                            "type": "error",
                            "content": "⚠️ 检测到异常输出（多个 JSON），已中断，正在重试..."
                        })
                    except Exception:
                        pass
                    break

                # 第二层（中）：每 5 token 扫一次完整顶层对象数
                if token_count % check_interval == 0:
                    brace_count = before_content.count("{")
                    if brace_count >= 2:
                        json_count = self.parser._count_json_objects(before_content)
                        if json_count >= 2:
                            Logger.warn(f"检测到 {json_count} 个 JSON 对象，中断生成")
                            interrupted = True
                            interrupted_reason = "检测到多个 JSON 对象"
                            try:
                                from plugins.builtins.notify.ws_notify import push as ws_push
                                await ws_push(agent, channel, {
                                    "type": "error",
                                    "content": f"⚠️ 检测到异常输出（{json_count} 个 JSON），已中断，正在重试..."
                                })
                            except Exception:
                                pass
                            break

                # 正常推送流式 token
                try:
                    from plugins.builtins.notify.ws_notify import push as ws_push
                    await ws_push(agent, channel, {"type": "summary_stream", "chunk": token})
                except Exception:
                    pass

        except Exception as e:
            Logger.warn(f"流式思考失败: {e}")
            raw = await llm.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ])

        # ★★★ 如果被中断，返回重试标记 ★★★
        if interrupted:
            return {
                "think": "输出违规，需要重试",
                "call": "_retry",
                "content": "",
                "args": {},
                "_validation_reason": interrupted_reason
            }

        # ★★★ 检测 LLM 错误包装（统一入口）★★★
        raw_stripped = raw.strip() if raw else ""
        if is_llm_error_string(raw_stripped):
            Logger.warn(f"检测到 LLM 错误包装: {raw_stripped[:150]}")
            raise ContentSafetyBlockedError(raw_stripped)

        result = self.parser.parse(raw)

        # ★★★ 检测解析后的 content 是否是错误包装 ★★★
        content = result.get("content", "")
        if is_llm_error_string(content):
            Logger.warn(f"检测到错误包装（解析后）: {content[:150]}")
            raise ContentSafetyBlockedError(content)

        return result

     
        


# ============================================================
# Action 注册表
# ============================================================

class ActionRegistry:
    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._register_defaults()

    def _register_defaults(self):
        self.register("use_tool", self._handle_use_tool)
        # self.register("create_tool", self._handle_create_tool)
        # self.register("fix_tool", self._handle_fix_tool)
        # self.register("remove_tool", self._handle_remove_tool)
        # self.register("add_experience", self._handle_add_experience)
        # self.register("aicp_chat", self._handle_aicp_chat)
        # self.register("query_plugin", self._handle_query_plugin)
        # self.register("query_system", self._handle_query_system)

    def register(self, call: str, handler: Callable):
        self._handlers[call] = handler

    async def execute(self, call: str, agent, args: dict, session_id: str, flow: InformationFlow) -> dict:
        handler = self._handlers.get(call)
        if not handler:
            return {"ok": False, "think": f"未知操作: {call}"}
        try:
            return await handler(agent, args, session_id, flow)
        except Exception as e:
            Logger.error(f"执行操作 '{call}' 失败: {e}", e)
            return {"ok": False, "think": f"执行 {call} 失败: {str(e)}"}

    # async def _handle_add_experience(self, agent, args, session_id, flow):
    #     experience = args.get("experience", "")
    #     if not experience:
    #         return {"ok": False, "think": "缺少 experience 参数"}

    #     current = _load_experience_backpack(session_id)
    #     mode = args.get("mode", "append")

    #     if mode == "replace":
    #         new_backpack = experience
    #     else:
    #         if current:
    #             new_backpack = current + "\n" + experience
    #         else:
    #             new_backpack = experience

    #     _save_experience_backpack(session_id, new_backpack)

    #     return {
    #         "ok": True,
    #         "think": f"经验已{'更新' if mode == 'replace' else '追加'}，当前背包 {len(new_backpack)} 字"
    #     }

    async def _handle_use_tool(self, agent, args, session_id, flow):
        target = args.get("target", "")
        tool_action = args.get("action", "run")
        tool_params = args.get("params", {})

        if not target:
            return {"ok": False, "think": "缺少 target 参数"}
        if target == "builtins/agents/main_agent":
            return {
                "ok": False,
                "think": (
                    "❌ 禁止直接调用 main_agent。\n"
                    "要开分身做子任务，请用 task_manager.create。\n"
                    "要回复用户，直接输出纯文本（call=reply）。"
                )
            }
        call_id = str(uuid.uuid4())[:8]
        retain = args.pop("_retain", 3) 
        await flow.append_system(
            action="call_start",
            result_think=f"📤 调用工具: {target}",
            detail={
                "call_id": call_id,
                "target": target,
                "action": tool_action,
                "params": tool_params,
                "status": "pending"
            },
            retain=retain
        )

        meta = {"session_id": session_id, "_call_id": call_id}
        call_params = {**tool_params}
        if "callback_receiver" in call_params:
            meta["callback_receiver"] = call_params.pop("callback_receiver")
        if "callback_session_id" in call_params:
            meta["callback_session_id"] = call_params.pop("callback_session_id")

        try:
            result = await agent.system.call(core.Envelop(
                sender="builtins/agents/main_agent",
                receiver=target,
                payload={"action": tool_action, **call_params},
                meta=meta
            ))

            if result and result.payload:
                plugin_ok = result.payload.get("ok", True) if isinstance(result.payload, dict) else True
                plugin_error = result.payload.get("error", "") if isinstance(result.payload, dict) else ""

                if plugin_ok:
                    think = f"工具「{target}」业务成功"
                else:
                    think = f"工具「{target}」业务失败: {plugin_error[:150]}"

                if result.payload.get("status") != "processing":
                    await flow.append_system(
                        action="call_end",
                        result_think=f"📨 调用完成: {target}",
                        detail={
                            "call_id": call_id,
                            "result": result.payload
                        },
                        retain=retain
                    )

                return {
                    "ok": True,
                    "think": think,
                    "data": result.payload,
                    "_full_result": json.dumps(result.payload, ensure_ascii=False) if isinstance(result.payload, dict) else str(result.payload)
                }
            return {"ok": True, "think": f"工具「{target}」已调用，无返回内容"}
        except Exception as e:
            await flow.append_system(
                action="call_error",
                result_think=f"❌ 调用失败: {target}",
                detail={"call_id": call_id, "error": str(e)},
                retain=retain
            )
            return {"ok": False, "think": f"工具「{target}」执行失败: {str(e)}"}

    # async def _handle_create_tool(self, agent, args, session_id, flow):
    #     try:
    #         result = await agent.system.call(core.Envelop(
    #             sender="builtins/agents/main_agent",
    #             receiver="builtins/tools/create_tool",
    #             payload={"params": args},
    #             meta={"session_id": session_id}
    #         ))
    #         return {"ok": True, "think": f"工具创建成功", "data": result.payload}
    #     except Exception as e:
    #         return {"ok": False, "think": f"创建工具失败: {str(e)}"}

    # async def _handle_fix_tool(self, agent, args, session_id, flow):
    #     try:
    #         result = await agent.system.call(core.Envelop(
    #             sender="builtins/agents/main_agent",
    #             receiver="builtins/tools/fix_tool",
    #             payload={"params": args},
    #             meta={"session_id": session_id}
    #         ))
    #         return {"ok": True, "think": f"工具修复成功", "data": result.payload}
    #     except Exception as e:
    #         return {"ok": False, "think": f"修复工具失败: {str(e)}"}

    # async def _handle_remove_tool(self, agent, args, session_id, flow):
    #     try:
    #         result = await agent.system.call(core.Envelop(
    #             sender="builtins/agents/main_agent",
    #             receiver="builtins/tools/remove_tool",
    #             payload={"params": args},
    #             meta={"session_id": session_id}
    #         ))
    #         return {"ok": True, "think": f"工具删除成功", "data": result.payload}
    #     except Exception as e:
    #         return {"ok": False, "think": f"删除工具失败: {str(e)}"}

    # async def _handle_query_plugin(self, agent, args, session_id, flow):
    #     plugin_name = args.get("plugin", "")
    #     if not plugin_name:
    #         return {"ok": False, "think": "缺少 plugin 参数"}

    #     try:
    #         result = await agent.system.call(core.Envelop(
    #             sender="builtins/agents/main_agent",
    #             receiver="builtins/agents/contract_agent",
    #             payload={"action": "get", "plugin": plugin_name},
    #             meta={"session_id": session_id}
    #         ))
    #         if result and result.payload:
    #             return {"ok": True, "think": f"契约查询成功", "data": result.payload}
    #         return {"ok": False, "think": "契约查询失败"}
    #     except Exception as e:
    #         return {"ok": False, "think": f"查询契约失败: {str(e)}"}

    # async def _handle_query_system(self, agent, args, session_id, flow):
    #     try:
    #         result = await agent.system.call(core.Envelop(
    #             sender="builtins/agents/main_agent",
    #             receiver="builtins/agents/cogitor",
    #             payload={"action": "get_map"}
    #         ))
    #         if result and result.payload:
    #             return {
    #                 "ok": True,
    #                 "think": "系统查询成功",
    #                 "data": result.payload,
    #                 "_full_result": json.dumps(result.payload, ensure_ascii=False) if isinstance(result.payload, dict) else str(result.payload)
    #             }
    #         return {"ok": False, "think": "系统查询失败"}
    #     except Exception as e:
    #         return {"ok": False, "think": f"查询系统失败: {str(e)}"}

    # async def _handle_aicp_chat(self, agent, args, session_id, flow):
    #     task = args.get("task") or args.get("prompt") or args.get("query") or ""

    #     if not task:
    #         return {"ok": False, "think": "缺少 task 参数"}

    #     try:
    #         result = await agent.system.call(core.Envelop(
    #             sender="builtins/agents/main_agent",
    #             receiver="builtins/aicp/chat",
    #             payload={"action": "chat", "messages": [{"role": "user", "content": task}]},
    #             meta={"session_id": session_id}
    #         ))
    #         if result and result.payload:
    #             return {"ok": True, "think": "任务执行成功", "data": result.payload}
    #         return {"ok": False, "think": "任务执行失败"}
    #     except Exception as e:
    #         return {"ok": False, "think": f"执行任务失败: {str(e)}"}


# ============================================================
# 核心处理器
# ============================================================

class CoreProcessor:
    def __init__(self):
        self.thinker = LLMThinker()
        self.actions = ActionRegistry()
        self._think_sessions: set = set()
        self._pending_callbacks: Dict[str, int] = {}
        self._interrupt_signals: Dict[str, bool] = {}

    def signal_interrupt(self, session_id: str):
        self._interrupt_signals[session_id] = True

    def clear_interrupt(self, session_id: str):
        self._interrupt_signals.pop(session_id, None)

    async def process(self, agent, session_id: str, flow: InformationFlow, depth: int = 0) -> dict:
        if session_id in self._think_sessions:
            self._pending_callbacks[session_id] = self._pending_callbacks.get(session_id, 0) + 1
            Logger.info(f"[{session_id}] 思考中，跳过触发（待处理回调 +1）")
            return {"ok": True, "waiting": True, "call": "skip", "content": "", "args": {}}

        self._think_sessions.add(session_id)

        try:
            result = await self._process_internal(agent, session_id, flow, depth)

            pending_count = self._pending_callbacks.pop(session_id, 0)
            if pending_count > 0:
                Logger.info(f"[{session_id}] 思考结束，处理 {pending_count} 个待处理回调")
                result = await self._process_internal(agent, session_id, flow, 0)

            return result
        finally:
            self._think_sessions.discard(session_id)

    async def _process_internal(self, agent, session_id: str, flow: InformationFlow, depth: int = 0) -> dict:
        # 检查中断
        if self._interrupt_signals.get(session_id, False):
            self.clear_interrupt(session_id)
            return {
                "ok": True,
                "interrupted": True,
                "call": "reply",
                "content": "⏸️ 任务已中断，继续输入新指令",
                "args": {}
            }

        if depth > MAX_RECURSION_DEPTH:
            Logger.warn(f"达到最大递归深度 ({MAX_RECURSION_DEPTH})")
            return {
                "ok": False,
                "waiting": False,
                "call": "reply",
                "content": "处理步骤过多，请简化需求后重试",
                "args": {}
            }

        output = await self.thinker.think(agent, flow, session_id, depth)

        # ★ 统一处理重试（_retry 标记）
        if output.get("call") == "_retry":
            reason = output.get("_validation_reason", "格式错误")

            if "多个 JSON" in reason:
                error_msg = (
                    "❌ 你上一轮输出了多个 JSON 对象，系统只执行了第一个，已中断生成。\n\n"
                    "【规则】每轮只能输出 1 个 JSON。\n"
                    "【原因】系统按“轮”执行，一轮一个动作，执行完才进入下一轮。\n"
                    "【怎么办】如果需要多个操作，请分多轮：\n"
                    "  - 本轮输出第 1 个 JSON\n"
                    "  - 等系统执行完，下一轮再输出第 2 个 JSON\n"
                    "【示例】本轮只输出：\n"
                    '  {"think":"先读README了解项目","call":"use_tool","retain":3,'
                    '"args":{"target":"os/file_utils_api","action":"read_file","params":{"path":"README.md"}}}\n'
                    "下一轮再输出下一个动作。"
                )

            elif "think 过长" in reason:
                error_msg = (
                    "❌ 你上一轮的 think 字段过长（超过 1500 字符），系统已中断生成。\n\n"
                    "【think 是什么】一句话说明“下一步做什么、为什么”，不是内心戏，不是背景解释，不是重复用户的话。\n"
                    "【怎么改】控制在 100 字以内。\n"
                    "【正确示例】\n"
                    '  - "用户要分析项目结构，我先列目录"\n'
                    '  - "上一步读文件失败，我换个文件名再试"\n'
                    '  - "任务完成，回复用户"\n'
                    "【错误示例】\n"
                    '  - "用户让我做X，我在想是不是该做Y，但是Z看起来也有可能，所以我认为..."（内心戏）\n'
                    '  - "用户在之前说过A，现在又说B，结合上下文..."（重复背景）\n'
                    "请重新输出，think 控制在 100 字以内。"
                )

            elif "JSON 解析失败" in reason:
                preview = reason.split("|", 1)[1] if "|" in reason else ""
                error_msg = (
                    "❌ 你上一轮的输出有 JSON 意图，但解析失败。\n\n"
                    "【常见原因】\n"
                    "  - 括号不闭合（{ 没有对应的 }）\n"
                    "  - 字符串里未转义的引号（\" 应该写成 \\\"）\n"
                    "  - 多余或缺失逗号\n"
                    "  - 字段名没加引号\n"
                    "【怎么改】检查 JSON 格式后重新输出。\n"
                    "【你的原始输出预览】\n"
                    f"{preview}\n\n"
                    "请重新输出完整合法的 JSON，或直接输出纯文本回复用户。"
                )

            elif "单行 patch" in reason:          # ★ 新增
                error_msg = (
                    "❌ 你上一轮把多行 patch 压缩成了一行，系统无法解析。\n\n"
                    "【规则】@@CONTENT@@ 块内必须保留换行，每行独立。\n"
                    "【diff 格式】\n"
                    "  --- a/文件路径\n"
                    "  +++ b/文件路径\n"
                    "  @@ -1,3 +1,3 @@\n"
                    "   上下文行（空格开头）\n"
                    "  -删除行\n"
                    "  +新增行\n"
                    "【注意】@@ / --- / +++ / 空格 / - / + 各占一行，不能挤在一起。\n"
                    "请重新输出，块内保留换行。"
                )

            else:
                error_msg = (
                    f"❌ 输出不符合要求：{reason}\n\n"
                    f"请重新输出，只输出 1 个合法 JSON：\n"
                    f'{{"think":"...","call":"use_tool","retain":3,"args":{{"target":"...","params":{{...}}}}}}\n'
                    f"或直接输出纯文本回复用户。"
                )

            await flow.append_system(
                action="validation_error",
                result_think="输出格式错误，请重新输出",
                detail={"error": reason},
                extra={"full_result": error_msg},
                retain=5
            )
            return await self._process_internal(agent, session_id, flow, depth + 1)

        # ★ FastValidator 校验
        output = FastValidator.validate(output)

        # ★ 处理 FastValidator 的校验失败
        if output.get("_validation_failed"):
            reason = output.get("_validation_reason", "输出格式错误")
            error_msg = (
                f"❌ 输出不符合要求：{reason}\n\n"
                f"请重新输出，只输出 1 个合法 JSON 或纯文本回复用户。"
            )
            await flow.append_system(
                action="validation_error",
                result_think="输出格式错误，请重新输出",
                detail={"error": reason},
                extra={"full_result": error_msg},
                retain=5
            )
            return await self._process_internal(agent, session_id, flow, depth + 1)

        think = output.get("think", "")
        call = output.get("call", "reply")
        content = output.get("content", "")
        args = output.get("args", {})
        retain = output.get("retain", 3)

        Logger.info(f"LLM 思考: {think[:80]}")
        Logger.info(f"LLM 决策: {call}")

        await flow.append_ai(think, {
            "call": call,
            "content": content,
            "args": args,
            "retain": retain      # ★ 传进去
        })

        # ★★★ 直接回复 ★★★
        if call == "reply":
            if not content:
                content = "好的，已处理"
            Logger.info(f"返回用户: {content[:80]}")
            return {
                "ok": True,
                "waiting": False,
                "call": "reply",
                "content": content,
                "args": {}
            }
        retain = output.get("retain", 3)
        args["_retain"] = retain
        
        # ★★★ use_tool 处理 ★★★
        if call == "use_tool":
            target = args.get("target")
            # retain = args.pop("_retain", 3) if isinstance(args, dict) else 3
            # reply 直接返回
            if target == "builtins/agents/main_agent/reply":
                content = args.get("params", {}).get("content", "")
                if not content:
                    content = "好的，已处理"
                Logger.info(f"返回用户: {content[:80]}")
                return {
                    "ok": True,
                    "waiting": False,
                    "call": "reply",
                    "content": content,
                    "args": {}
                }
            
            # ★★★ 执行工具 ★★★
            result = await self.actions.execute(call, agent, args, session_id, flow)

            # ★★★ 异步调用：记录 processing，继续递归让 LLM 看到 ★★★
            if result.get("data", {}).get("status") == "processing":
                trace_id = result.get("data", {}).get("trace_id", "")
                
                # 找到 call_start，更新 trace_id 和状态
                for entry in reversed(flow._flow):
                    if entry.get("from") == "system" and entry.get("action") == "call_start":
                        if entry.get("detail", {}).get("call_id"):
                            entry["detail"]["trace_id"] = trace_id
                            entry["detail"]["status"] = "processing"
                            await flow._save_locked()
                            break
                
                # ★★★ 写入 processing 系统消息 ★★★
                await flow.append_system(
                    action="async_processing",
                    result_think=f"⏳ 异步任务已提交 [trace={trace_id[:8]}]",
                    detail={
                        "trace_id": trace_id,
                        "status": "processing"
                    },
                    retain=3
                )
                
                await _push_chat(agent, session_id, "⏳ 任务已提交，正在后台处理...")
                
                # ★★★ 继续递归，让 LLM 看到 processing，主动 reply 用户 ★★★
                return await self._process_internal(agent, session_id, flow, depth + 1)

            # ★★★ 同步调用：继续递归 ★★★
            await _push_progress(agent, session_id, "done", f"✅ {call} 完成")
            return await self._process_internal(agent, session_id, flow, depth + 1)

        # ★ create_tool / fix_tool 直接走异步
        if call in ["create_tool", "fix_tool"]:
            tool_name = args.get("name", args.get("target", "工具"))
            reply_msg = f"收到！正在后台处理「{tool_name}」，完成后会通知你。"
            async_task_manager.submit(
                session_id,
                self._execute_async_and_continue(agent, session_id, flow, call, args, depth)
            )
            await _push_progress(agent, session_id, "executing", f"⚡ 正在后台{call}...")
            return {
                "ok": True,
                "waiting": False,
                "call": "reply",
                "content": reply_msg,
                "args": {}
            }

        # ★ 其他工具走同步处理
        return await self._handle_sync_action(agent, session_id, flow, call, args, depth)

    # def _handle_direct_reply(self, call: str, content: str) -> dict:
    #     if not content:
    #         content = "好的，已处理" if call == "reply" else "请补充信息"
    #     Logger.info(f"返回用户: {content[:80]}")
    #     return {
    #         "ok": True,
    #         "waiting": call == "ask_user",
    #         "call": call,
    #         "content": content,
    #         "args": {}
    #     }

    async def _handle_special_action(self, agent, session_id, flow, call, args, depth):
        result = await self.actions.execute(call, agent, args, session_id, flow)

        retain = args.pop("_retain", 3) if isinstance(args, dict) else 3

        content = result.get("content", "")
        full_result = result.pop("_full_result", None) if result else None

        if content:
            think_text = result.get("think", f"执行{call}完成，返回 {len(content)} 字符")
            detail = result
            extra = {"full_result": full_result or content} if full_result or content else None
        else:
            think_text = result.get("think", f"执行{call}完成")
            detail = result
            extra = {"full_result": full_result} if full_result else None

        try:
            await flow.append_system(call, think_text, result, {"full_result": full_result} if full_result else None)
        except Exception as e:
            Logger.error(f"[DEBUG] append_system 失败: {e}")
            import traceback
            traceback.print_exc()
        
        Logger.info(f"[DEBUG] use_tool 完成，准备进入第 {depth + 1} 轮")
        return await self._process_internal(agent, session_id, flow, depth + 1)

    async def _handle_async_action(self, agent, session_id, flow, call, args, depth):
        tool_name = args.get("name", args.get("target", "工具"))
        reply_msg = f"收到！正在后台处理「{tool_name}」，完成后会通知你。"
        
        async_task_manager.submit(
            session_id,
            self._execute_async_and_continue(agent, session_id, flow, call, args, depth)
        )
        await _push_progress(agent, session_id, "executing", f"⚡ 正在后台{call}...")
        
        return {
            "ok": True,
            "waiting": False,
            "call": "reply",
            "content": reply_msg,
            "args": {}
        }

    async def _handle_sync_action(self, agent, session_id, flow, call, args, depth):
        retry_config = SmartRetryPolicy.get_retry_config(call)

        await _push_progress(agent, session_id, "executing", f"⚡ 正在{call}...")

        result = None
        for attempt in range(retry_config.get("max_retries", 0) + 1):
            result = await self.actions.execute(call, agent, args, session_id, flow)

            if result.get("ok"):
                if result.get("data") and isinstance(result["data"], dict) and result["data"].get("status") == "processing":
                    trace_id = result["data"].get("trace_id", "")
                    await _push_progress(agent, session_id, "executing", f"⚡ 异步任务已提交（trace={trace_id[:8]}）")
                break

            error_msg = result.get("think", "")
            if SmartRetryPolicy.should_retry_directly(call, error_msg):
                if attempt < retry_config.get("max_retries", 0):
                    Logger.info(f"直接重试 {call} (第 {attempt+1} 次): {error_msg[:50]}")
                    await asyncio.sleep(retry_config.get("delay", 0.5))
                    continue
            if SmartRetryPolicy.should_ask_llm_to_fix(error_msg):
                result["_needs_llm_fix"] = True
            break

        retain = args.pop("_retain", 3) if isinstance(args, dict) else 3

        full_result = result.pop("_full_result", None) if result else None
        think_text = result.get("think", f"执行{call}完成") if result else f"执行{call}完成"
        detail = result if result else {}

        extra = {"full_result": full_result} if full_result else None
        await flow.append_system(call, think_text, detail, extra, retain=retain)

        await _push_progress(agent, session_id, "done", f"✅ {call} 完成")
        return await self._process_internal(agent, session_id, flow, depth + 1)

    async def _execute_async_and_continue(self, agent, session_id, flow, call, args, depth):
        try:
            await _push_progress(agent, session_id, "executing", f"⚡ 正在{call}...")
            result = await self.actions.execute(call, agent, args, session_id, flow)

            full_result = result.pop("_full_result", None) if result else None
            think_text = result.get("think", f"执行{call}完成") if result else f"执行{call}完成"
            detail = result if result else {}

            await flow.append_system(call, think_text, detail, {"full_result": full_result} if full_result else None)
            await _push_progress(agent, session_id, "done", f"✅ {call} 完成")

            if self._interrupt_signals.get(session_id, False):
                    self.clear_interrupt(session_id)
                    return

            follow_up = await self._process_internal(agent, session_id, flow, depth + 1)
        except Exception as e:
            Logger.error(f"异步执行失败 [{session_id}]: {e}", e)
            error_msg = f"❌ {call} 失败: {str(e)}"
            await flow.append_system(call, error_msg)
            try:
                follow_up = await self._process_internal(agent, session_id, flow, depth + 1)
            except Exception:
                pass


action_registry = ActionRegistry()
core_processor = CoreProcessor()