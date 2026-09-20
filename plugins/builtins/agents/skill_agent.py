# plugins/builtins/agents/skill_agent.py
"""
Skill Agent — 独立技能执行器
核心设计：
- 每个 skill_agent 实例绑定一个 skill.md 文件
- 拥有独立的记忆流（与主 agent 隔离）
- 可调用 aicp_chat + file（受限目录）
- 对话结束后返回完整结果给主 agent
- ★ 和 main_agent 一样，直接输出 use_tool 执行工具
- ★ 支持 WebSocket 进度推送，前端可见执行过程
- ★ 输出结构统一为 think + call + args（与主 Agent 对齐）
"""

import json
import uuid
import asyncio
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

import core

# ============================================================
# 常量
# ============================================================

STATE_DIR = Path("data/memories/skill_agent")
STATE_FILE = STATE_DIR / "state.json"
WORKSPACE_DIR = Path("data/workspace")
OUTPUT_DIR = Path("data/output")
SKILLS_BASE_DIR = Path("data/skills")

STATE_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SKILLS_BASE_DIR.mkdir(parents=True, exist_ok=True)

TOOL_TIMEOUT = 60
MAX_ITERATIONS = 5



# ============================================================
# 状态管理（原子写入 + 并发保护）
# ============================================================

class StateManager:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def load(self) -> dict:
        async with self._lock:
            if not STATE_FILE.exists():
                return {"active": None, "queue": []}
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {"active": None, "queue": []}

    async def save(self, state: dict) -> bool:
        async with self._lock:
            try:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                content = json.dumps(state, ensure_ascii=False, indent=2)
                temp_file = STATE_FILE.with_suffix('.tmp')
                temp_file.write_text(content, encoding="utf-8")
                temp_file.replace(STATE_FILE)
                return True
            except Exception as e:
                print(f"[skill_agent] save_state 失败: {e}")
                return False

    async def verify(self, session_id: str) -> bool:
        state = await self.load()
        active = state.get("active")
        if not active:
            return False
        return active.get("session_id") == session_id


state_manager = StateManager()


# ============================================================
# Skill Agent 独立信息流
# ============================================================

class SkillFlow:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.flow_file = STATE_DIR / f"{session_id}_flow.json"
        self._flow: List[Dict] = []
        self._load()

    def _load(self):
        if not self.flow_file.exists():
            self._flow = []
            return
        try:
            data = json.loads(self.flow_file.read_text(encoding="utf-8"))
            self._flow = data
        except:
            self._flow = []

    def _save(self):
        try:
            temp_file = self.flow_file.with_suffix('.tmp')
            temp_file.write_text(
                json.dumps(self._flow, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            temp_file.replace(self.flow_file)
        except Exception as e:
            print(f"[skill_agent] flow save 失败: {e}")

    async def append_user(self, content: str):
        self._flow.append({
            "timestamp": datetime.now().isoformat(),
            "from": "user",
            "content": content
        })
        self._save()

    async def append_ai(self, content: str):
        self._flow.append({
            "timestamp": datetime.now().isoformat(),
            "from": "ai",
            "content": content
        })
        self._save()

    async def append_system(self, result: str):
        self._flow.append({
            "timestamp": datetime.now().isoformat(),
            "from": "system",
            "content": result
        })
        self._save()

    def get_context(self, limit: int = 30) -> str:
        entries = self._flow[-limit:] if len(self._flow) > limit else self._flow
        lines = []
        for entry in entries:
            ts_full = entry.get("timestamp", "")
            ts = ts_full[5:19] if ts_full else ""
            from_user = entry.get("from", "")
            content = entry.get("content", "")
            if from_user == "user":
                lines.append(f"[{ts}] 👤 用户：{content}")
            elif from_user == "ai":
                lines.append(f"[{ts}] 🤖 {content}")
            elif from_user == "system":
                lines.append(f"[{ts}] 📊 系统返回：\n{content}")
        return "\n".join(lines)

    def cleanup(self):
        try:
            if self.flow_file.exists():
                self.flow_file.unlink()
        except Exception as e:
            print(f"[skill_agent] cleanup flow 失败: {e}")


_session_flows: Dict[str, SkillFlow] = {}


def _get_skill_flow(session_id: str) -> SkillFlow:
    if session_id not in _session_flows:
        _session_flows[session_id] = SkillFlow(session_id)
    return _session_flows[session_id]


async def _cleanup_session(session_id: str):
    if session_id in _session_flows:
        flow = _session_flows.pop(session_id)
        flow.cleanup()


# ============================================================
# WS 推送
# ============================================================

async def _push_progress(agent, session_id: str, step: str, msg: str, agent_type: str = "skill"):
    try:
        await agent.system.call(core.Envelop(
            sender="builtins/agents/main_agent",
            receiver="os/_websocket",
            payload={
                "action": "push",
                "channel_id": f"pa_{session_id}",
                "data": {
                    "type": "progress",
                    "step": step,
                    "msg": msg,
                    "message": msg,
                    "agent_type": agent_type
                }
            }
        ))
    except Exception as e:
        print(f"[skill_agent] WS 推送失败: {e}")


# ============================================================
# 工具权限定义（与主 Agent 对齐：think + 无 summary）
# ============================================================

ALLOWED_TOOLS_DESC = """
## 可用工具（通过 use_tool 调用）

### 调用铁律
1. 一次只输出一个 JSON，等待系统反馈，禁止一次输出多个
2. JSON 结构固定：{"think":"推理","call":"use_tool","retain":N,"args":{"target":"...","action":"...","params":{...}}}
3. 有 action 的插件：action 放 args 顶层，参数放 args.params
4. 需要 content 参数的插件：JSON 中省略 content，用 @@CONTENT@@ 块传递
5. 禁止：多个 JSON、<think> 标签、Python 代码块、额外解释文字

### 工具清单

**builtins/tools/aicp_chat**（一次性任务兜底，无 action）：

{"think":"用 aicp_chat 执行一次性任务","call":"use_tool","retain":3,"args":{"target":"builtins/tools/aicp_chat","params":{"task":"详细任务描述"}}}

**os/file_utils_api**（文件读写，action 在顶层）：

读文件：
{"think":"读取文件内容","call":"use_tool","retain":3,"args":{"target":"os/file_utils_api","action":"read_file","params":{"path":"data/output/xxx.aoml"}}}

写文件（content 走 @@CONTENT@@ 块）：
{"think":"写入文件到磁盘","call":"use_tool","retain":1,"args":{"target":"os/file_utils_api","action":"write_file","params":{"path":"data/output/xxx.aoml"}}}
@@CONTENT@@
文件内容写这里，无需转义
@@END_CONTENT@@

列目录：
{"think":"查看目录内容","call":"use_tool","retain":3,"args":{"target":"os/file_utils_api","action":"list_dir","params":{"path":"data/output"}}}

### 输出规则
- 需要调用工具：只输出一个 JSON（可带 think 字段）
- 不需要调用工具：直接输出纯文本回复
- 不要在 JSON 前后添加解释文字
"""


def build_skill_prompt(skill_content: str) -> str:
    return skill_content + "\n\n" + ALLOWED_TOOLS_DESC + """

【文件写入规则】
- 你只能写入以下目录：data/workspace/ 和 data/output/
- 禁止写入 plugins/applications/ 等系统目录
- 写入前确认目录存在

【代码执行规则】
- 可以用 aicp_chat 执行代码来验证方案
- 执行结果会返回给你，用于判断下一步

【你的工作方式】
1. 理解用户需求
2. 制定方案
3. 生成代码/内容
4. 用 os/file_utils_api 落盘
5. 返回最终结果

【退出规则】
- 任务完成后，用户说「退下」或「完成」时，生成总结并退出
- 总结要包含：做了什么、产出了什么、放在哪里了
- 返回 handover_to_main 标记给主 Agent"""


# ============================================================
# LLM 输出解析器（与主 Agent 对齐：think + 无 summary）
# ============================================================

def extract_content_block(raw: str) -> Tuple[Optional[str], str]:
    """提取 @@CONTENT@@ 块"""
    pattern = r'@@CONTENT@@\s*\n(.*?)\n\s*@@END_CONTENT@@'
    match = re.search(pattern, raw, re.DOTALL)
    if match:
        content = match.group(1)
        cleaned = raw[:match.start()] + raw[match.end():]
        return content, cleaned
    return None, raw


def _find_json(raw: str) -> Optional[dict]:
    """找第一个含 call 字段的 JSON 对象"""
    if not raw:
        return None

    raw = raw.strip()

    try:
        result = json.loads(raw)
        if isinstance(result, dict) and "call" in result:
            return result
    except:
        pass

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
            candidate = raw[start:end + 1]
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and "call" in parsed:
                    return parsed
            except:
                pass
            i = end + 1
        else:
            break

    return None


def _count_json_objects(raw: str) -> int:
    """统计含 call 字段的 JSON 对象数量"""
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
                if isinstance(obj, dict) and "call" in obj:
                    count += 1
            except:
                pass
            i = end + 1
        else:
            break
    return count


def parse_llm_output(raw: str) -> dict:
    """解析 LLM 输出，只提取第一个含 call 的 JSON"""
    if not raw:
        return {
            "think": "空输入",
            "call": "reply",
            "content": "抱歉，我遇到了一点问题。"
        }

    # 提取 @@CONTENT@@ 块
    content_block, raw_without_content = extract_content_block(raw)

    # 找第一个含 call 的 JSON
    json_obj = _find_json(raw_without_content)

    if json_obj is None:
        return {
            "think": "LLM 返回纯文本",
            "call": "reply",
            "content": raw.strip()
        }

    # 归一化 call
    call = json_obj.get("call", "")
    if call not in ("use_tool", "reply"):
        return {
            "think": f"未知 call: {call}",
            "call": "reply",
            "content": raw.strip()
        }

    # 注入 content 块（按 target/action 映射字段）
    if content_block is not None and call == "use_tool":
        args = json_obj.setdefault("args", {})
        params = args.setdefault("params", {})
        target = args.get("target", "")
        action = args.get("action", "")

        if target == "os/file_utils_api" and action in ("write_file", "append_file"):
            params["content"] = content_block
        else:
            params["content"] = content_block

    json_obj.setdefault("think", "")
    return json_obj


# ============================================================
# 路径安全验证
# ============================================================

def safe_resolve_skill_path(skill_path: str) -> Optional[Path]:
    try:
        path = Path(skill_path)
        if path.is_absolute():
            path = Path(path.name)

        full_path = (SKILLS_BASE_DIR / path).resolve()

        if not str(full_path).startswith(str(SKILLS_BASE_DIR.resolve())):
            return None

        return full_path if full_path.exists() else None
    except Exception:
        return None


def validate_workspace_path(file_path: str) -> bool:
    try:
        path = Path(file_path).resolve()
        workspace = WORKSPACE_DIR.resolve()
        output = OUTPUT_DIR.resolve()
        return str(path).startswith(str(workspace)) or str(path).startswith(str(output))
    except Exception:
        return False


# ============================================================
# 工具执行器
# ============================================================

async def execute_tool_call(agent, tool_call: dict, session_id: str = "default") -> dict:
    """执行工具调用并推送进度"""
    target = tool_call.get("target", "")
    tool_action = tool_call.get("action", "")       # ★ 从顶层取 action
    params = tool_call.get("params", {})

    if not target:
        return {"ok": False, "error": "缺少工具 target"}

    # 路径校验
    if target == "os/file_utils_api":
        file_path = params.get("path", "")
        if file_path and tool_action in ["write_file", "read_file", "append_file"]:
            if not validate_workspace_path(file_path):
                return {"ok": False, "error": f"非法文件路径: {file_path}"}

    await _push_progress(agent, session_id, "executing", f"⚡ 正在执行 {target}...")

    try:
        # ★ 统一构建 payload：有 action 就带上
        payload = {"action": tool_action, **params} if tool_action else dict(params)

        result = await asyncio.wait_for(
            agent.system.call(core.Envelop(
                sender="builtins/agents/skill_agent",
                receiver=target,
                payload=payload,
                meta={"session_id": session_id}
            )),
            timeout=TOOL_TIMEOUT
        )

        if result and result.payload:
            print(f"[DEBUG] 工具返回类型: {type(result.payload)}")
            print(f"[DEBUG] 工具返回前500字符: {str(result.payload)[:500]}")
            await _push_progress(agent, session_id, "done", f"✅ {target} 执行成功")
            return {"ok": True, "data": result.payload}

        await _push_progress(agent, session_id, "error", f"❌ {target} 无响应")
        return {"ok": False, "error": f"工具 {target} 无响应"}
    except asyncio.TimeoutError:
        await _push_progress(agent, session_id, "error", f"❌ {target} 执行超时")
        return {"ok": False, "error": f"工具 {target} 执行超时"}
    except Exception as e:
        await _push_progress(agent, session_id, "error", f"❌ {target} 执行失败: {str(e)}")
        return {"ok": False, "error": f"工具 {target} 执行失败: {e}"}


# ============================================================
# 主入口
# ============================================================

async def execute(envelop, agent):
    action = envelop.payload.get("action", "chat")
    session_id = envelop.payload.get("session_id", "default")
    skill_path = envelop.payload.get("skill_path", "")
    skill_name = envelop.payload.get("skill_name", "技能专家")
    content = envelop.payload.get("content", "")

    # ============================================================
    # shift — 切换到技能角色
    # ============================================================
    if action == "shift":
        if not skill_path:
            envelop.payload = {
                "ok": False,
                "error": "缺少 skill_path 参数，请传入 skill.md 文件路径"
            }
            return envelop

        await _push_progress(agent, session_id, "loading", f"📂 正在加载技能 {skill_name}...")

        skill_path_obj = safe_resolve_skill_path(skill_path)
        if not skill_path_obj:
            envelop.payload = {
                "ok": False,
                "error": f"skill 文件不存在或路径非法: {skill_path}"
            }
            return envelop

        try:
            skill_content = skill_path_obj.read_text(encoding="utf-8")
        except Exception as e:
            envelop.payload = {
                "ok": False,
                "error": f"读取 skill 文件失败: {e}"
            }
            return envelop

        if len(skill_content) < 10:
            envelop.payload = {
                "ok": False,
                "error": f"skill 内容过短（{len(skill_content)} 字符），请检查文件是否完整"
            }
            return envelop

        skill_session_id = f"skill_{uuid.uuid4().hex[:8]}"

        try:
            full_prompt = build_skill_prompt(skill_content)
        except Exception as e:
            envelop.payload = {
                "ok": False,
                "error": f"构建 skill prompt 失败: {e}"
            }
            return envelop

        state = await state_manager.load()
        entry = {
            "session_id": skill_session_id,
            "skill_name": skill_name,
            "skill_prompt": full_prompt,
            "parent_session": session_id,
            "created_at": datetime.now().isoformat(),
        }

        if state.get("active"):
            state["queue"].append(state["active"])
        state["active"] = entry

        if not await state_manager.save(state):
            envelop.payload = {
                "ok": False,
                "error": f"状态文件写入失败: {STATE_FILE}，请检查目录权限"
            }
            return envelop

        if not await state_manager.verify(skill_session_id):
            envelop.payload = {
                "ok": False,
                "error": f"状态验证失败: session_id {skill_session_id} 未正确写入"
            }
            return envelop

        try:
            flow = _get_skill_flow(skill_session_id)
            await flow.append_ai(f"你好！我是 {skill_name}。有什么可以帮你的？")
        except Exception as e:
            state = await state_manager.load()
            if state.get("active") and state["active"].get("session_id") == skill_session_id:
                state["active"] = None
                if state.get("queue"):
                    state["active"] = state["queue"].pop(0)
                await state_manager.save(state)
            envelop.payload = {
                "ok": False,
                "error": f"信息流初始化失败: {e}"
            }
            return envelop

        await _push_progress(agent, session_id, "loaded", f"✅ 已切换到 {skill_name}")

        envelop.payload = {
            "ok": True,
            "data": {
                "mode": "persona",
                "session_id": skill_session_id,
                "persona_name": skill_name,
                "message": f"✅ 已切换到 {skill_name}"
            }
        }
        return envelop

    # ============================================================
    # chat — 和技能角色对话（支持工具调用循环）
    # ============================================================
    elif action == "chat":
        skill_session_id = envelop.payload.get("session_id", "")
        user_input = envelop.payload.get("content", "")

        if not skill_session_id:
            envelop.payload = {"ok": False, "error": "缺少 session_id 参数"}
            return envelop

        if not user_input:
            envelop.payload = {"ok": False, "error": "缺少 content 参数"}
            return envelop

        if not STATE_FILE.exists():
            envelop.payload = {"ok": False, "error": "状态文件不存在，请先调用 shift 加载技能"}
            return envelop

        if not await state_manager.verify(skill_session_id):
            envelop.payload = {"ok": False, "error": f"session_id {skill_session_id} 无效或已过期"}
            return envelop

        state = await state_manager.load()
        active = state.get("active")
        if not active or active.get("session_id") != skill_session_id:
            envelop.payload = {"ok": False, "error": "当前没有激活的技能角色"}
            return envelop

        skill_prompt = active.get("skill_prompt", "")
        skill_name = active.get("skill_name", "技能专家")
        parent_session = active.get("parent_session", session_id)

        flow = _get_skill_flow(skill_session_id)
        await flow.append_user(user_input)

        # 检查是否退出
        if user_input.strip() in ["退下", "退出", "完成", "quit", "exit"] or "退" in user_input:
            await _push_progress(agent, parent_session, "exiting", "👋 正在退出技能专家...")

            context = flow.get_context()
            summary_prompt = f"""你是技能专家，用户说「退下」了。
请生成一段 200 字以内的对话总结，包含：
1. 用户的主要需求
2. 你做了什么
3. 产出了什么、放在哪里了

{context}

只输出总结文本。"""

            try:
                summary = await agent.llm.chat([{"role": "user", "content": summary_prompt}])
                summary = summary.strip()
            except:
                summary = "技能专家完成了任务。"

            state = await state_manager.load()
            if state.get("active") and state["active"].get("session_id") == skill_session_id:
                state["active"] = None
                if state.get("queue"):
                    state["active"] = state["queue"].pop(0)
                await state_manager.save(state)

            await _cleanup_session(skill_session_id)
            await _push_progress(agent, parent_session, "exited", f"✅ 已退出技能专家，返回主 Agent")

            envelop.payload = {
                "ok": True,
                "data": {
                    "mode": "main",
                    "summary": summary,
                    "handover_to_main": True
                }
            }
            return envelop

        # 进入主循环
        max_iterations = MAX_ITERATIONS
        iteration = 0
        final_reply = None
        tool_call_history = []

        await _push_progress(agent, parent_session, "thinking", f"🧠 {skill_name} 正在思考...")

        while iteration < max_iterations:
            iteration += 1
            context = flow.get_context()

            system_prompt = f"""{skill_prompt}

【对话历史】
{context}

【重要规则 - 必须严格遵守】
1. 一次只能输出一个工具调用 JSON
2. 输出工具调用后，等待系统返回结果
3. 不要连续输出多个工具调用
4. 工具执行成功后，直接回复用户，不要再重复调用相同的工具
5. 如果文件已经写入成功，直接告诉用户结果

请根据你的角色和可用工具，回应用户。"""

            # 调用 LLM（流式 + 多 JSON 检测）
            try:
                raw = ""
                interrupted = False
                token_count = 0
                check_interval = 5

                try:
                    from plugins.builtins.notify.ws_notify import push as ws_push
                    channel = f"pa_{parent_session}"

                    async for token in agent.llm.chat_stream([
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": "继续处理，如果需要工具就调用，如果已处理完就回复用户"}
                    ]):
                        raw += token
                        token_count += 1

                        # 每 5 个 token 检测多个 JSON
                        if token_count % check_interval == 0:
                            if _count_json_objects(raw) >= 2:
                                interrupted = True
                                print(f"[DEBUG] 检测到多个 JSON 对象，中断生成")
                                break

                        try:
                            await ws_push(agent, channel, {
                                "type": "summary_stream",
                                "chunk": token
                            })
                        except Exception:
                            pass

                    if interrupted:
                        await flow.append_system("❌ 检测到多个 JSON 对象，每轮只能输出一个。请重新输出。")
                        continue

                    raw = raw.strip()

                except Exception as stream_error:
                    print(f"[DEBUG] 流式调用失败，降级为非流式: {stream_error}")
                    raw = await agent.llm.chat([
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": "继续处理，如果需要工具就调用，如果已处理完就回复用户"}
                    ])
                    raw = raw.strip()

            except Exception as e:
                envelop.payload = {
                    "ok": False,
                    "data": {"reply": f"抱歉，我遇到了一点问题：{e}"}
                }
                return envelop

            print(f"[DEBUG] LLM 原始输出: {raw[:300]}...")

            output = parse_llm_output(raw)
            call = output.get("call", "reply")
            think = output.get("think", "")

            # 记录 think 到信息流
            if think:
                await flow.append_ai(f"💭 {think}")

            print(f"[DEBUG] 解析结果: call={call}")

            if call == "use_tool":
                args = output.get("args", {})
                target = args.get("target", "")
                tool_action = args.get("action", "")     # ★ 从顶层取
                tool_params = args.get("params", {})

                call_key = json.dumps(
                    {"target": target, "action": tool_action, "params": tool_params},
                    sort_keys=True, ensure_ascii=False
                )

                if call_key in tool_call_history:
                    print(f"[DEBUG] 检测到重复工具调用，强制停止")
                    final_reply = "操作已完成，无需重复执行。"
                    await flow.append_ai(final_reply)
                    break

                tool_call_history.append(call_key)

                print(f"[DEBUG] 工具调用: target={target}, action={tool_action}")

                result = await execute_tool_call(
                    agent,
                    {"target": target, "action": tool_action, "params": tool_params},
                    parent_session
                )

                if result.get("ok"):
                    data = result.get("data", {})

                    if target == "os/file_utils_api" and tool_action == "read_file":
                        content_data = None
                        if isinstance(data, dict):
                            content_data = data.get("content") or data.get("data") or data.get("result")
                        elif isinstance(data, str):
                            content_data = data

                        if content_data:
                            result_text = f"✅ 文件读取成功\n文件路径：{tool_params.get('path', '')}\n文件内容：\n{content_data}"
                        else:
                            result_text = f"✅ 文件读取成功\n原始返回：{json.dumps(data, ensure_ascii=False)}"

                    elif target == "os/file_utils_api" and tool_action == "write_file":
                        result_text = f"✅ 文件写入成功\n文件路径：{tool_params.get('path', '')}"

                    elif target == "os/file_utils_api" and tool_action == "list_dir":
                        files = data.get("files", data.get("data", data)) if isinstance(data, dict) else data
                        result_text = f"✅ 文件列表：\n{json.dumps(files, ensure_ascii=False, indent=2)}"

                    else:
                        result_text = f"✅ 工具执行成功\n返回数据：{json.dumps(data, ensure_ascii=False)}"
                else:
                    result_text = f"❌ 工具执行失败：{result.get('error', '未知错误')}"

                print(f"[DEBUG] 工具结果: {result_text[:200]}...")

                await flow.append_system(result_text)
                continue

            if call == "reply":
                final_reply = output.get("content", "好的，已处理。")
                await flow.append_ai(final_reply)
                break

            final_reply = raw if raw else "好的，已处理。"
            await flow.append_ai(final_reply)
            break

        if final_reply is None:
            final_reply = "处理完成。"

        envelop.payload = {
            "ok": True,
            "data": {"reply": final_reply}
        }
        return envelop

    # ============================================================
    # status
    # ============================================================
    elif action == "status":
        if not STATE_FILE.exists():
            envelop.payload = {
                "ok": True,
                "data": {"active": None, "queue": [], "total": 0, "has_state": False}
            }
            return envelop

        try:
            state = await state_manager.load()
            active = state.get("active")
            queue = state.get("queue", [])

            envelop.payload = {
                "ok": True,
                "data": {
                    "active": {
                        "session_id": active.get("session_id") if active else None,
                        "name": active.get("skill_name") if active else None,
                    } if active else None,
                    "queue": [
                        {"session_id": q.get("session_id"), "name": q.get("skill_name")}
                        for q in queue
                    ],
                    "total": len(queue) + (1 if active else 0),
                    "has_state": True
                }
            }
            return envelop
        except Exception as e:
            envelop.payload = {"ok": False, "error": f"读取状态失败: {e}"}
            return envelop

    # ============================================================
    # clear
    # ============================================================
    elif action == "clear":
        for sid in list(_session_flows.keys()):
            await _cleanup_session(sid)

        await state_manager.save({"active": None, "queue": []})
        envelop.payload = {"ok": True, "data": {"message": "已清空所有技能角色"}}
        return envelop

    else:
        envelop.payload = {
            "ok": False,
            "error": f"未知 action: {action}，支持: shift, chat, status, clear"
        }
        return envelop


# ============================================================
# help
# ============================================================

def help():
    return {
        "route": "builtins/agents/skill_agent",
        "description": "独立技能执行器 — 与主 Agent 输出规范对齐（think + 无 summary）",
        "input": {
            "action": "shift | chat | status | clear",
            "skill_path": "skill.md 文件路径（shift 时必填）",
            "skill_name": "技能名称（shift 时可选）",
            "session_id": "技能会话 ID（chat 时必填）",
            "content": "用户输入（chat 时必填）"
        },
        "output": {
            "ok": "是否成功",
            "data": {
                "mode": "persona | main",
                "session_id": "技能会话 ID",
                "persona_name": "技能名称",
                "message": "响应消息",
                "reply": "AI 回复内容",
                "summary": "对话总结"
            }
        }
    }