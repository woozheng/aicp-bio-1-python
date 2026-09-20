# main_agent — 主入口

import uuid
import json
from pathlib import Path
from datetime import datetime

import core
from .core import (
    _get_or_create_flow, _get_processing_lock, 
    core_processor, _push_progress, _push_chat,
    Logger, MEMORY_DIR
)


# ============================================================
# 经验背包 / 任务看板 存储路径（和 add_experience / add_task_board 保持一致）
# ============================================================

_MEMORY_BASE = Path("data/memories/main_agent")
_EXPERIENCE_DIR = _MEMORY_BASE / "experiences"
_TASKBOARD_DIR = _MEMORY_BASE / "taskboards"


def _get_experience_file(session_id: str) -> Path:
    _EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)
    return _EXPERIENCE_DIR / f"{session_id}_backpack.txt"


def _get_taskboard_file(session_id: str) -> Path:
    _TASKBOARD_DIR.mkdir(parents=True, exist_ok=True)
    return _TASKBOARD_DIR / f"{session_id}_taskboard.txt"


def save_state(state):
    """保存 skill_agent 状态（保留，供外部调用）"""
    state_file = Path("data/memories/skill_agent/state.json")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# 工具执行器（支持异步回调）
# ============================================================

class ToolExecutor:
    @staticmethod
    async def use_tool(agent, target: str, action: str, params: dict, session_id: str = "default") -> dict:
        """使用工具，支持同步和异步调用"""
        Logger.info(f"使用工具: target={target}, action={action}")

        # ★★★ 自调用拦截 ★★★
        if target == "builtins/agents/main_agent":
            target_session = params.get("session_id", "")
            if not target_session:
                params["session_id"] = f"sub_{uuid.uuid4().hex[:8]}"
            elif target_session == session_id:
                return {
                    "ok": False,
                    "think": (
                        "禁止同 session 自调用（会死循环）。"
                        "技能已加载时直接用当前角色回复；"
                        "如需分身，session_id 用 sub_ 开头的新 ID。"
                    )
                }
            elif not target_session.startswith("sub_"):
                return {
                    "ok": False,
                    "think": "分身 session_id 必须以 sub_ 开头，例如 sub_task_001。"
                }
        # ================================

        # ★★★ 提取异步相关参数到 meta ★★★
        call_meta = {"session_id": session_id}
        call_params = {**params}  # 复制一份，不修改原 params
        
        # 提取 callback_receiver（从 params 中移除并放入 meta）
        if "callback_receiver" in call_params:
            call_meta["callback_receiver"] = call_params.pop("callback_receiver")
        
        # 提取 callback_session_id（从 params 中移除并放入 meta）
        if "callback_session_id" in call_params:
            call_meta["callback_session_id"] = call_params.pop("callback_session_id")
        
        # 提取 token（从 params 中移除并放入 meta）
        if "token" in call_params:
            call_meta["token"] = call_params.pop("token")

        try:
            result = await agent.system.call(core.Envelop(
                sender="builtins/agents/main_agent",
                receiver=target,
                payload={"action": action, **call_params},
                meta=call_meta  # ★ 传入 meta（包含 callback_receiver）
            ))
            
            if result and result.payload:
                # 异步任务已提交
                if result.payload.get("status") == "processing":
                    trace_id = result.meta.get("trace_id", "")
                    return {
                        "ok": True,
                        "think": f"任务已提交（trace={trace_id[:8]}）",
                        "data": {"status": "processing", "trace_id": trace_id}
                    }

                if result.payload.get("ok") is False or result.payload.get("error"):
                    error_msg = result.payload.get("error", "执行失败")
                    return {
                        "ok": False,
                        "think": f"工具「{target}」执行失败: {error_msg}",
                        "data": result.payload
                    }
                
                data = result.payload.get("data", result.payload)
                return {"ok": True, "think": f"工具「{target}」执行成功", "data": data}
            
            return {"ok": True, "think": f"工具「{target}」执行完成"}
            
        except Exception as e:
            Logger.error(f"工具执行异常: target={target}, action={action}\n{e}", e)
            return {
                "ok": False,
                "think": f"工具「{target}」执行异常: {str(e)}"
            }


# ============================================================
# 主入口
# ============================================================

async def execute(envelop, agent):
    try:
        session_id = envelop.payload.get("session_id", "default")
        user_input = envelop.payload.get("content", "").strip()
        action = envelop.payload.get("action", "")
        date_str = envelop.payload.get("date", "")
        attachments = envelop.payload.get("attachments", [])

        # ============================================================
        # 1. 回调处理（优先级最高）
        # ============================================================
        if envelop.meta.get("is_callback"):
            async with _get_processing_lock(session_id):
                return await _handle_callback(envelop, agent)

        # ============================================================
        # 2. 系统管理 action
        # ============================================================
        if action == "get_session":
            flow = _get_or_create_flow(session_id)
            all_days = []
            flows_dir = MEMORY_DIR / "flows"
            if flows_dir.exists():
                for day_dir in flows_dir.iterdir():
                    if day_dir.is_dir():
                        day_file = day_dir / f"{session_id}.json"
                        if day_file.exists():
                            all_days.append(day_dir.name)
            all_days.sort(reverse=True)
            envelop.payload = {
                "ok": True,
                "flow": flow._flow[-20:],
                "all_days": all_days,
                "current_day": datetime.now().strftime("%Y-%m-%d")
            }
            return envelop

        if action == "get_day_history":
            if not date_str:
                envelop.payload = {"ok": False, "error": "缺少 date 参数"}
                return envelop
            day_file = MEMORY_DIR / "flows" / date_str / f"{session_id}.json"
            if not day_file.exists():
                envelop.payload = {"ok": False, "error": f"没有 {date_str} 的历史"}
                return envelop
            try:
                entries = json.loads(day_file.read_text(encoding="utf-8"))
                envelop.payload = {"ok": True, "entries": entries, "date": date_str}
            except Exception as e:
                envelop.payload = {"ok": False, "error": f"读取失败: {e}"}
            return envelop

        if action == "clear_memory":
            flow = _get_or_create_flow(session_id)
            flow._flow = []
            await flow._save_locked()
            envelop.payload = {"ok": True}
            return envelop

        if action == "refresh_cache":
            from .core import _query_cache
            _query_cache.invalidate()
            envelop.payload = {"ok": True, "message": "缓存已刷新"}
            return envelop

        # ============================================================
        # 读取状态（经验背包 / 任务看板）
        # ============================================================
        if action == "get_state":
            state_type = envelop.payload.get("type", "exp")
            if state_type == "exp":
                f = _get_experience_file(session_id)
            else:
                f = _get_taskboard_file(session_id)
            content = f.read_text(encoding="utf-8") if f.exists() else ""
            envelop.payload = {
                "ok": True,
                "data": {
                    "type": state_type,
                    "content": content,
                    "size": len(content)
                }
            }
            return envelop

        # ============================================================
        # 保存状态（经验背包 / 任务看板）
        # ============================================================
        if action == "save_state":
            state_type = envelop.payload.get("type", "exp")
            content = envelop.payload.get("content", "")
            if state_type == "exp":
                f = _get_experience_file(session_id)
            else:
                f = _get_taskboard_file(session_id)
            f.write_text(content, encoding="utf-8")
            envelop.payload = {
                "ok": True,
                "data": {
                    "type": state_type,
                    "saved": True,
                    "size": len(content)
                }
            }
            return envelop

        # ============================================================
        # 3. 文件处理
        # ============================================================
        if attachments:
            MAX_FILE_SIZE = 10 * 1024 * 1024
            MAX_FILES = 5
            if len(attachments) > MAX_FILES:
                envelop.payload = {
                    "ok": False,
                    "error": f"文件数量超过限制（最多 {MAX_FILES} 个）"
                }
                return envelop

            oversized = []
            valid_attachments = []
            for att in attachments:
                size = att.get("size", 0)
                path = att.get("path", "")
                name = att.get("name", "未知文件")
                if size == 0 and path:
                    try:
                        size = Path(path).stat().st_size
                    except:
                        pass
                if size > MAX_FILE_SIZE:
                    oversized.append(f"{name} ({size / 1024 / 1024:.1f}MB)")
                else:
                    valid_attachments.append(att)

            if oversized:
                envelop.payload = {
                    "ok": False,
                    "error": f"以下文件超过大小限制: {', '.join(oversized)}"
                }
                return envelop

            flow = _get_or_create_flow(session_id)
            file_lines = []
            for att in valid_attachments:
                name = att.get('name', '未知')
                size = att.get('size', 0)
                path = att.get('path', '')
                file_lines.append(f"- {name} ({size / 1024:.1f}KB) 路径: {path}")
            file_think = "\n".join(file_lines)

            await flow.append_system(
                action="files_uploaded",
                result_think=f"用户上传了 {len(valid_attachments)} 个文件",
                detail={"files": valid_attachments}
            )

            if not user_input:
                user_input = f"用户上传了 {len(valid_attachments)} 个文件，请处理：\n{file_think}"
            else:
                user_input = f"{user_input}\n\n[已上传文件]\n{file_think}"

        # ============================================================
        # 4. 空输入检查
        # ============================================================
        if not user_input:
            envelop.payload = {"ok": False, "error": "请输入内容"}
            return envelop

        # ============================================================
        # 5. 打断检查
        # ============================================================
        if envelop.sender == "os/_gateway" and session_id in core_processor._think_sessions:
            core_processor.signal_interrupt(session_id)
            envelop.payload = {
                "ok": True,
                "waiting": True,
                "call": "reply",
                "content": "⏸️ 已接收新消息，正在中断当前任务...",
                "args": {}
            }
            return envelop

        # ============================================================
        # 6. 正常流程：交给 CoreProcessor
        # ============================================================
        Logger.info(f"用户输入 [{session_id}]: {user_input[:60]}")

        await _push_progress(agent, session_id, "thinking", "🧠 正在思考...")

        flow = _get_or_create_flow(session_id)
        await flow.append_user(user_input)

        async with _get_processing_lock(session_id):
            result = await core_processor.process(agent, session_id, flow, 0)

            if result.get("call") == "skip":
                envelop.payload = {
                    "ok": True,
                    "waiting": True,
                    "call": "reply",
                    "content": "⏳ 正在处理中，请稍候...",
                    "args": {}
                }
                return envelop

            envelop.payload = {
                "ok": result.get("ok", True),
                "waiting": result.get("waiting", False),
                "call": result.get("call", "reply"),
                "content": result.get("content", ""),
                "args": {}
            }
            return envelop

    except Exception as e:
        from .core import ContentSafetyBlockedError
        error_str = str(e)
        session_id = envelop.payload.get("session_id", "default")
        flow = _get_or_create_flow(session_id)

        # ★ 判断是否是 LLM 错误
        is_llm_error = isinstance(e, ContentSafetyBlockedError) or any(
            error_str.startswith(p) for p in (
                "[LLM stream error:",
                "[LLM请求失败:",
                "[系统错误:",
                "[服务请求超时",
                "[模型返回空响应",
                "[LLM 达到最大重试次数]",
                "[LLM 未配置]",
                "[空响应]",
            )
        )

        # ★ LLM 错误打 WARN，其他打 ERROR
        if is_llm_error:
            Logger.warn(f"LLM 调用失败: {error_str[:150]}")
        else:
            Logger.error(f"主入口执行异常: {e}", e)

        if is_llm_error:
            # ★ 回退 flow：删掉“触发这次 LLM 调用的最后一条”
            deleted = []
            if flow._flow:
                last = flow._flow[-1]
                last_from = last.get("from", "")
                last_action = last.get("action", "")

                if last_from == "user":
                    # 第一轮：删用户那条
                    deleted.append({
                        "from": "user",
                        "content": last.get("content", "")[:80],
                    })
                    flow._flow.pop()
                elif last_action in ("call_end", "call_error", "async_callback", "async_processing"):
                    # 多轮：删最后一个 ai 及之后的所有
                    for i in range(len(flow._flow) - 1, -1, -1):
                        if flow._flow[i].get("from") == "ai":
                            deleted = [
                                {
                                    "from": x.get("from"),
                                    "action": x.get("action"),
                                    "call": x.get("call"),
                                }
                                for x in flow._flow[i:]
                            ]
                            del flow._flow[i:]
                            break

            await flow._save_locked()

            # ★ 清理 pending
            core_processor._pending_callbacks.pop(session_id, 0)

            # ★ 构造错误信息
            lines = ["⚠️ LLM 调用失败，已中断本轮。"]
            lines.append("")
            lines.append(f"错误详情：{error_str[:300]}")
            if deleted:
                lines.append("")
                lines.append(f"已回退 {len(deleted)} 条 flow：")
                for item in deleted:
                    desc = item.get("content") or item.get("call") or item.get("action") or "?"
                    lines.append(f"  - [{item.get('from')}] {str(desc)[:60]}")
            lines.append("")
            lines.append("请修改后重试。")

            msg = "\n".join(lines)

            await _push_chat(agent, session_id, msg)

            envelop.payload = {
                "ok": False,
                "waiting": False,
                "call": "reply",
                "content": msg,
                "error": msg,
                "args": {
                    "error": error_str,
                    "deleted_count": len(deleted),
                    "deleted_items": deleted,
                }
            }
            return envelop

        # 普通异常
        await _push_chat(agent, session_id, f"❌ 系统错误: {error_str}")
        envelop.payload = {
            "ok": False,
            "waiting": False,
            "call": "reply",
            "content": f"系统错误: {error_str}",
            "error": f"系统错误: {error_str}",
            "args": {}
        }
        return envelop


async def _handle_callback(envelop, agent):
    """回调处理 — 支持 use_tool 异步结果 + task_manager 任务回调"""
    session_id = envelop.meta.get("callback_session_id", "default")
    if not session_id:
        session_id = envelop.meta.get("session_id", "default")
    if not session_id:
        session_id = "default"

    flow = _get_or_create_flow(session_id)
    trace_id = envelop.meta.get("trace_id", envelop.trace_id)
    call_id = envelop.meta.get("_call_id", "")
    task_id = envelop.meta.get("_task_id", "")           # ★ 新增
    task_summary = envelop.meta.get("_task_summary", "") # ★ 新增

    original_receiver = envelop.meta.get("callback_original_receiver", "unknown")

    payload = envelop.payload
    if isinstance(payload, dict):
        content = payload.get("content", "") or payload.get("reply", "") or json.dumps(payload, ensure_ascii=False)
    else:
        content = str(payload)

    # ★★★ 如果有任务摘要，先写入 flow ★★★
    if task_summary:
        await flow.append_system(
            action="task_summary",
            result_think=task_summary,
            detail={"task_id": task_id},
            retain=5,
        )

    # 找到对应的 call_start，标记已完成
    matched = False
    for entry in reversed(flow._flow):
        if entry.get("from") == "system" and entry.get("action") == "call_start":
            if entry.get("detail", {}).get("call_id") == call_id:
                entry["detail"]["status"] = "completed"
                entry["detail"]["callback_received"] = True
                matched = True
                await flow._save_locked()
                break

    think = f"📨 回调结果 [trace={trace_id[:8]}]"

    await flow.append_system(
        action="async_callback",
        result_think=think,
        detail={
            "call_id": call_id,
            "trace_id": trace_id,
            "task_id": task_id,           # ★ 新增
            "matched": matched,
            "result": payload
        },
        extra={"full_result": content},
        retain=3
    )

    await _push_progress(agent, session_id, "callback", f"📡 任务完成（trace={trace_id[:8]}）")

    # 继续处理：让 LLM 看到回调结果
    result = await core_processor.process(agent, session_id, flow)

    call = result.get("call", "reply")
    final_content = result.get("content", "")
    if call in ("reply", "ask_user") and final_content:
        await _push_chat(agent, session_id, final_content)

    envelop.payload = {"ok": True, "content": final_content}
    return envelop


def help():
    return {
        "route": "builtins/agents/main_agent",
        "description": "AICP 主控制台 Agent — 信息流驱动版 (支持异步回调)",
        "input": {
            "content": "用户输入",
            "session_id": "会话ID",
            "action": "get_session | get_day_history | clear_memory | refresh_cache | get_state | save_state",
            "date": "日期（get_day_history 时使用）",
            "type": "状态类型：exp（经验背包）/ task（任务看板）",
            "content_state": "保存内容（save_state 时使用）"
        },
        "output": {
            "ok": "操作是否成功",
            "waiting": "是否等待用户回复",
            "call": "reply | ask_user",
            "content": "AI 回复内容"
        },
        "features": [
            "支持 use_tool 异步回调（callback_receiver 在 params 中传递）",
            "回调自动回到 execute 并触发 _handle_callback",
            "异步结果自动写入信息流并继续推理",
            "支持 get_state / save_state 读取和保存经验背包、任务看板",
            "自调用拦截：禁止同 session 调 main_agent，分身需用 sub_ 前缀"
        ]
    }