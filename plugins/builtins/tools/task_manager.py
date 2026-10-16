"""task_manager — 分身任务管理器

职责：
- 创建分身任务（sub_ session）
- 给已有分身发送消息（续跑/改进）
- 拼装所有 meta（session_id / callback_receiver / _task_id）
- 接收回调，匹配 task_id，更新状态
- 提供 list / status / collect 查询
- 通知 main_agent 有新进展

LLM 只传 description 和 task（或 task_id 和 message），其余全部由本模块处理。
"""

import json
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import core


ACTIONS_SCHEMA = {
    "create": {
        "description": "创建一个分身任务（自动分配 session，异步执行）",
        "params": {
            "description": {"type": "string", "description": "任务简述（用于列表展示）"},
            "task": {"type": "string", "description": "给分身的完整任务描述"}
        },
        "required": ["description", "task"]
    },
    "send": {
        "description": "给已有分身发送消息（续跑/改进，保留分身的记忆）",
        "params": {
            "task_id": {"type": "string", "description": "任务 ID（sub_xxx）"},
            "message": {"type": "string", "description": "要发送的消息"}
        },
        "required": ["task_id", "message"]
    },
    "list": {
        "description": "列出当前 session 的所有分身任务",
        "params": {
            "status": {"type": "string", "description": "可选筛选：running / done / failed / timeout"}
        },
        "required": []
    },
    "status": {
        "description": "查看某个分身任务的详细状态",
        "params": {
            "task_id": {"type": "string", "description": "任务 ID"}
        },
        "required": ["task_id"]
    },
    "cancel": {
        "description": "取消某个分身任务（仅标记，不强制中断）",
        "params": {
            "task_id": {"type": "string", "description": "任务 ID"}
        },
        "required": ["task_id"]
    },
    "collect": {
        "description": "收集所有已完成任务的结果",
        "params": {
            "task_id": {"type": "string", "description": "可选，指定单个任务 ID"},
            "wait": {"type": "boolean", "description": "是否等待未完成任务（默认 false）"},
            "timeout": {"type": "number", "description": "等待超时秒数（默认 60）"}
        },
        "required": []
    },
    "clear": {
        "description": "批量清理所有已结束的分身（任务记录 + flow 文件）",
        "params": {},
        "required": []
    },
    "delete": {
        "description": "永久删除指定的分身（任务记录 + flow 文件）",
        "params": {
            "task_ids": {
                "type": "array",
                "description": "要删除的任务 ID 列表，如 [\"sub_a3f8b2c1\", \"sub_9e520e28\"]"
            },
            "task_id": {
                "type": "string",
                "description": "（可选）单个任务 ID，等价于 task_ids=[task_id]"
            }
        },
        "required": []
    }
}


# ============================================================
# 常量
# ============================================================

TASKS_DIR = Path("data/memories/main_agent/tasks")
FLOWS_BASE = Path("data/memories/main_agent/flows")
MAX_CONCURRENT_TASKS = 5
TASK_TIMEOUT_SECONDS = 600


# ============================================================
# 存储
# ============================================================

def _tasks_file(parent_session: str) -> Path:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    return TASKS_DIR / f"{parent_session}.json"


def _load_tasks(parent_session: str) -> dict:
    f = _tasks_file(parent_session)
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_tasks(parent_session: str, tasks: dict):
    f = _tasks_file(parent_session)
    f.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now().isoformat()


def _update_task(parent_session: str, task_id: str, patch: dict):
    tasks = _load_tasks(parent_session)
    if task_id not in tasks:
        return False
    tasks[task_id].update(patch)
    _save_tasks(parent_session, tasks)
    return True


def _count_running(parent_session: str) -> int:
    tasks = _load_tasks(parent_session)
    return sum(1 for t in tasks.values() if t.get("status") == "running")


def _check_timeouts(parent_session: str):
    """标记超时任务"""
    tasks = _load_tasks(parent_session)
    now = datetime.now()
    changed = False
    for tid, t in tasks.items():
        if t.get("status") != "running":
            continue
        try:
            created = datetime.fromisoformat(t["created_at"])
            if (now - created).total_seconds() > TASK_TIMEOUT_SECONDS:
                t["status"] = "timeout"
                t["error"] = f"任务超过 {TASK_TIMEOUT_SECONDS} 秒未完成"
                t["finished_at"] = _now()
                changed = True
        except Exception:
            pass
    if changed:
        _save_tasks(parent_session, tasks)


def _format_summary(parent_session: str) -> str:
    """给 main_agent 看的任务摘要"""
    tasks = _load_tasks(parent_session)
    if not tasks:
        return ""
    lines = ["【分身任务】"]
    for tid, t in sorted(tasks.items(), key=lambda x: x[1].get("created_at", "")):
        status = t.get("status", "unknown")
        icon = {
            "running": "⏳",
            "done": "✅",
            "failed": "❌",
            "timeout": "⏰",
            "cancelled": "🚫",
        }.get(status, "?")
        desc = t.get("description", "")
        lines.append(f"{icon} {tid}: {desc} [{status}]")
    return "\n".join(lines)


def _delete_flow_files(task_ids: list) -> int:
    """删除指定的 flow 文件，返回删除数量"""
    deleted = 0
    if not FLOWS_BASE.exists():
        return 0
    for date_dir in FLOWS_BASE.iterdir():
        if not date_dir.is_dir():
            continue
        for tid in task_ids:
            flow_file = date_dir / f"{tid}.json"
            if flow_file.exists():
                try:
                    flow_file.unlink()
                    deleted += 1
                except Exception:
                    pass
    return deleted


# ============================================================
# action 处理器
# ============================================================

async def _handle_create(agent, args, session_id, flow):
    description = args.get("description", "").strip()
    task_text = args.get("task", "").strip()

    if not description or not task_text:
        return {"ok": False, "think": "缺少 description 或 task"}

    # 并发上限
    if _count_running(session_id) >= MAX_CONCURRENT_TASKS:
        return {
            "ok": False,
            "think": f"并发任务已达上限（{MAX_CONCURRENT_TASKS}）。请等现有任务完成，或用 task_manager.list 查看。"
        }

    # ★ 自动分配 session_id
    task_id = f"sub_{uuid.uuid4().hex[:8]}"

    # 记录任务
    tasks = _load_tasks(session_id)
    tasks[task_id] = {
        "task_id": task_id,
        "parent_session": session_id,
        "description": description,
        "task": task_text,
        "status": "running",
        "created_at": _now(),
        "finished_at": None,
        "result": None,
        "error": None,
    }
    _save_tasks(session_id, tasks)

    # 拼装 meta —— LLM 完全不用管
    call_meta = {
        "session_id": session_id,
        "callback_receiver": "builtins/tools/task_manager",
        "callback_session_id": session_id,
        "_task_id": task_id,
    }

    try:
        await agent.system.call(core.Envelop(
            sender="builtins/tools/task_manager",
            receiver="builtins/agents/main_agent",
            payload={
                "content": task_text,
                "session_id": task_id,
                "action": "",
            },
            meta=call_meta,
        ))
    except Exception as e:
        _update_task(session_id, task_id, {
            "status": "failed",
            "error": f"创建分身失败: {e}",
            "finished_at": _now(),
        })
        return {"ok": False, "think": f"创建分身失败: {e}"}

    return {
        "ok": True,
        "think": f"✅ 分身任务已创建：{task_id}",
        "data": {
            "task_id": task_id,
            "description": description,
            "status": "running",
        },
        "_summary": _format_summary(session_id),
    }


async def _handle_send(agent, args, session_id, flow):
    """给已有分身发送消息（续跑/改进，保留分身的记忆）"""
    task_id = args.get("task_id", "").strip()
    message = args.get("message", "").strip()

    if not task_id or not message:
        return {"ok": False, "think": "缺少 task_id 或 message"}

    tasks = _load_tasks(session_id)
    t = tasks.get(task_id)
    if not t:
        return {"ok": False, "think": f"任务不存在: {task_id}"}

    # ★ 状态改回 running（如果是 done/failed/timeout/cancelled）
    _update_task(session_id, task_id, {
        "status": "running",
        "finished_at": None,
        "error": None,
    })

    call_meta = {
        "session_id": session_id,
        "callback_receiver": "builtins/tools/task_manager",
        "callback_session_id": session_id,
        "_task_id": task_id,
    }

    try:
        await agent.system.call(core.Envelop(
            sender="builtins/tools/task_manager",
            receiver="builtins/agents/main_agent",
            payload={
                "content": message,
                "session_id": task_id,     # ★ 复用已有的 session
                "action": "",
            },
            meta=call_meta,
        ))
    except Exception as e:
        _update_task(session_id, task_id, {
            "status": "failed",
            "error": f"发送失败: {e}",
            "finished_at": _now(),
        })
        return {"ok": False, "think": f"发送失败: {e}"}

    return {
        "ok": True,
        "think": f"✅ 已发送到 {task_id}，分身将带着记忆继续",
        "data": {
            "task_id": task_id,
            "status": "running",
        },
        "_summary": _format_summary(session_id),
    }


async def _handle_list(agent, args, session_id, flow):
    _check_timeouts(session_id)
    tasks = _load_tasks(session_id)
    status_filter = args.get("status", "")

    result = []
    for tid, t in tasks.items():
        if status_filter and t.get("status") != status_filter:
            continue
        result.append({
            "task_id": tid,
            "description": t.get("description", ""),
            "status": t.get("status", ""),
            "created_at": t.get("created_at"),
            "finished_at": t.get("finished_at"),
        })

    return {
        "ok": True,
        "think": f"共 {len(result)} 个任务",
        "data": {
            "tasks": result,
            "running_count": _count_running(session_id),
            "max_concurrent": MAX_CONCURRENT_TASKS,
        },
        "_summary": _format_summary(session_id),
    }


async def _handle_status(agent, args, session_id, flow):
    task_id = args.get("task_id", "")
    if not task_id:
        return {"ok": False, "think": "缺少 task_id"}

    tasks = _load_tasks(session_id)
    t = tasks.get(task_id)
    if not t:
        return {"ok": False, "think": f"任务不存在: {task_id}"}

    return {
        "ok": True,
        "think": f"任务 {task_id} 状态: {t.get('status')}",
        "data": t,
    }


async def _handle_cancel(agent, args, session_id, flow):
    task_id = args.get("task_id", "")
    if not task_id:
        return {"ok": False, "think": "缺少 task_id"}

    tasks = _load_tasks(session_id)
    t = tasks.get(task_id)
    if not t:
        return {"ok": False, "think": f"任务不存在: {task_id}"}

    if t.get("status") != "running":
        return {"ok": False, "think": f"任务 {task_id} 已结束（{t.get('status')}），无法取消"}

    _update_task(session_id, task_id, {
        "status": "cancelled",
        "error": "被调用方取消",
        "finished_at": _now(),
    })

    return {
        "ok": True,
        "think": f"任务 {task_id} 已标记取消",
        "data": {"task_id": task_id},
        "_summary": _format_summary(session_id),
    }


async def _handle_collect(agent, args, session_id, flow):
    task_id = args.get("task_id", "")
    wait = bool(args.get("wait", False))
    timeout = float(args.get("timeout", 60))

    # ★ 单个任务查询
    if task_id:
        _check_timeouts(session_id)
        tasks = _load_tasks(session_id)
        t = tasks.get(task_id)
        if not t:
            return {
                "ok": False,
                "think": f"任务不存在: {task_id}",
            }
        return {
            "ok": True,
            "think": f"任务 {task_id} 状态: {t.get('status')}",
            "data": {
                "task_id": task_id,
                "description": t.get("description", ""),
                "status": t.get("status"),
                "result": t.get("result"),
                "error": t.get("error"),
                "created_at": t.get("created_at"),
                "finished_at": t.get("finished_at"),
            },
            "_summary": _format_summary(session_id),
        }

    # ★ 全部收集
    _check_timeouts(session_id)

    if wait:
        start = asyncio.get_event_loop().time()
        while True:
            if _count_running(session_id) == 0:
                break
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed >= timeout:
                break
            await asyncio.sleep(0.5)
            _check_timeouts(session_id)

    tasks = _load_tasks(session_id)
    done = {}
    running = {}
    failed = {}

    for tid, t in tasks.items():
        status = t.get("status")
        if status == "done":
            done[tid] = {
                "description": t.get("description", ""),
                "result": t.get("result"),
            }
        elif status == "running":
            running[tid] = t.get("description", "")
        else:
            failed[tid] = {
                "description": t.get("description", ""),
                "status": status,
                "error": t.get("error"),
            }

    return {
        "ok": True,
        "think": f"完成 {len(done)} / 运行中 {len(running)} / 失败 {len(failed)}",
        "data": {
            "done": done,
            "running": running,
            "failed": failed,
        },
        "_summary": _format_summary(session_id),
    }


async def _handle_clear(agent, args, session_id, flow):
    """批量清理所有已结束的分身（任务记录 + flow 文件）"""
    tasks = _load_tasks(session_id)

    # 保留 running / pending
    kept = {tid: t for tid, t in tasks.items() if t.get("status") in ("pending", "running")}
    removed_tids = [tid for tid in tasks if tid not in kept]
    removed = len(removed_tids)

    # 保存精简后的 tasks
    _save_tasks(session_id, kept)

    # ★ 删除已结束分身的 flow 文件
    deleted_flows = _delete_flow_files(removed_tids)

    # 推送前端刷新
    try:
        await agent.system.call(core.Envelop(
            sender="builtins/tools/task_manager",
            receiver="os/_websocket",
            payload={
                "action": "push",
                "channel_id": f"pa_{session_id}",
                "data": {
                    "type": "progress",
                    "step": "done",
                    "msg": f"清理分身：{removed} 个记录，{deleted_flows} 个 flow",
                    "agent_type": "task_manager"
                }
            }
        ))
    except Exception:
        pass

    return {
        "ok": True,
        "think": f"已清空 {removed} 个已结束任务（含 {deleted_flows} 个 flow 文件）",
        "data": {
            "removed": removed,
            "deleted_flows": deleted_flows,
            "kept": len(kept),
        },
        "_summary": _format_summary(session_id),
    }


async def _handle_delete(agent, args, session_id, flow):
    """删除指定的分身（任务记录 + flow 文件）"""
    task_ids = args.get("task_ids", [])

    # 兼容单个 task_id 传参
    if not task_ids:
        single = args.get("task_id", "").strip()
        if single:
            task_ids = [single]

    if not task_ids:
        return {"ok": False, "think": "缺少 task_ids 或 task_id"}

    if not isinstance(task_ids, list):
        task_ids = [task_ids]

    tasks = _load_tasks(session_id)
    deleted = []
    skipped = []

    for tid in task_ids:
        tid = str(tid).strip()
        if not tid:
            continue

        if tid not in tasks:
            skipped.append({"task_id": tid, "reason": "不存在"})
            continue

        # 不能删"正在跑"的
        if tasks[tid].get("status") == "running":
            skipped.append({"task_id": tid, "reason": "正在运行，请先 cancel"})
            continue

        # ★ 删任务记录
        del tasks[tid]
        deleted.append(tid)

    _save_tasks(session_id, tasks)

    # ★ 删对应的 flow 文件
    deleted_flows = _delete_flow_files(deleted)

    # 推送前端刷新
    try:
        await agent.system.call(core.Envelop(
            sender="builtins/tools/task_manager",
            receiver="os/_websocket",
            payload={
                "action": "push",
                "channel_id": f"pa_{session_id}",
                "data": {
                    "type": "progress",
                    "step": "done",
                    "msg": f"删除分身：{len(deleted)} 个记录，{deleted_flows} 个 flow",
                    "agent_type": "task_manager"
                }
            }
        ))
    except Exception:
        pass

    return {
        "ok": True,
        "think": f"已删除 {len(deleted)} 个分身（含 {deleted_flows} 个 flow 文件）",
        "data": {
            "deleted": deleted,
            "deleted_flows": deleted_flows,
            "skipped": skipped,
        },
        "_summary": _format_summary(session_id),
    }


# ============================================================
# 回调处理
# ============================================================

async def _handle_callback(envelop, agent):
    """分身完成后的回调"""
    meta = envelop.meta or {}
    task_id = meta.get("_task_id", "")
    parent_session = meta.get("callback_session_id", "")

    if not task_id or not parent_session:
        return envelop

    payload = envelop.payload or {}
    plugin_ok = payload.get("ok", True) if isinstance(payload, dict) else True
    error = payload.get("error", "") if isinstance(payload, dict) else ""

    patch = {
        "status": "done" if plugin_ok else "failed",
        "finished_at": _now(),
        "result": payload,
        "error": error or None,
    }
    _update_task(parent_session, task_id, patch)

    # 通知 main_agent 继续推理
    try:
        await agent.system.call(core.Envelop(
            sender="builtins/tools/task_manager",
            receiver="builtins/agents/main_agent",
            payload={
                "content": "",
                "action": "",
            },
            meta={
                "session_id": parent_session,
                "is_callback": True,
                "callback_session_id": parent_session,
                "_task_id": task_id,
                "_task_summary": _format_summary(parent_session),
            },
        ))
    except Exception:
        pass

    return envelop


# ============================================================
# 主入口
# ============================================================

async def execute(envelop, agent):
    # ★ 回调优先——回调的 session 是主 session，必须先处理
    if envelop.meta.get("is_callback"):
        return await _handle_callback(envelop, agent)

    # 兼容两种传参
    params = envelop.payload.get("params", {})
    if not params:
        params = {k: v for k, v in envelop.payload.items() if k != "action"}

    action = envelop.payload.get("action", "list")
    session_id = envelop.meta.get("session_id", "default")

    # ★ 分身不能再使用 task_manager
    if session_id.startswith("sub_"):
        envelop.payload = {
            "ok": False,
            "error": "分身不能再使用 task_manager（避免无限嵌套）。请直接在当前执行线上完成任务。"
        }
        return envelop

    handlers = {
        "create": _handle_create,
        "send": _handle_send,
        "list": _handle_list,
        "status": _handle_status,
        "cancel": _handle_cancel,
        "collect": _handle_collect,
        "clear": _handle_clear,
        "delete": _handle_delete,
    }

    handler = handlers.get(action)
    if not handler:
        envelop.payload = {"ok": False, "error": f"未知 action: {action}"}
        return envelop

    try:
        result = await handler(agent, params, session_id, None)
    except Exception as e:
        envelop.payload = {"ok": False, "error": f"task_manager 异常: {e}"}
        return envelop

    summary = result.pop("_summary", None)
    envelop.payload = {
        "ok": result.get("ok", False),
        "data": result.get("data", {}),
        "message": result.get("think", ""),
    }
    if summary:
        envelop.payload["data"]["_summary"] = summary

    return envelop


def help():
    return {
        "route": "builtins/tools/task_manager",
        "description": "分身任务管理器 —— 创建、追踪、续跑、收集、清理并行子任务",
        "input": {
            "action": "create | send | list | status | cancel | collect | clear | delete",
            "description": "任务简述（create 时）",
            "task": "完整任务描述（create 时）",
            "task_id": "任务 ID（send / status / cancel / delete 时）",
            "message": "要发送的消息（send 时）",
            "task_ids": "任务 ID 列表（delete 时，支持批量）",
            "status": "筛选状态（list 时）",
            "wait": "是否等待（collect 时）",
            "timeout": "等待超时秒数（collect 时）",
        },
        "output": {
            "ok": "是否成功",
            "data.task_id": "任务 ID（create / send 时）",
            "data.tasks": "任务列表",
            "data.done": "已完成任务结果（collect 时）",
            "data.deleted": "已删除的任务 ID（delete 时）",
            "message": "结果消息",
        },
        "examples": [
            'create(description="调研 A", task="详细调研 A 方向的三个子问题") → 自动分配 sub_xxx',
            'send(task_id="sub_xxx", message="把 X 部分重做，用 Y 角度") → 分身带着记忆继续',
            'collect() → {"done": {...}, "running": {...}, "failed": {...}}',
            'delete(task_ids=["sub_a3f8b2c1", "sub_9e520e28"]) → 精确删除多个分身',
            'clear() → 批量清理所有已结束的分身（含 flow 文件）',
        ],
    }
