"""task_manager — 分身任务管理器

职责：
- 创建分身任务（sub_ session）
- 拼装所有 meta（session_id / callback_receiver / _task_id）
- 接收回调，匹配 task_id，更新状态
- 提供 list / status / collect 查询
- 通知 main_agent 有新进展

LLM 只传 description 和 task，其余全部由本模块处理。
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
        "description": "创建一个分身任务（异步执行）",
        "params": {
            "description": {"type": "string", "description": "任务简述（用于列表展示）"},
            "task": {"type": "string", "description": "给分身的完整任务描述"}
        },
        "required": ["description", "task"]
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
            "wait": {"type": "boolean", "description": "是否等待未完成任务（默认 false）"},
            "timeout": {"type": "number", "description": "等待超时秒数（默认 60）"}
        },
        "required": []
    },
    "clear": {
        "description": "清空已结束的任务记录",
        "params": {},
        "required": []
    }
}


# ============================================================
# 常量
# ============================================================

TASKS_DIR = Path("data/memories/main_agent/tasks")
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
    tasks = _load_tasks(session_id)
    kept = {tid: t for tid, t in tasks.items() if t.get("status") == "running"}
    removed = len(tasks) - len(kept)
    _save_tasks(session_id, kept)
    return {
        "ok": True,
        "think": f"已清空 {removed} 个已结束任务",
        "data": {"removed": removed, "kept": len(kept)},
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
        "list": _handle_list,
        "status": _handle_status,
        "cancel": _handle_cancel,
        "collect": _handle_collect,
        "clear": _handle_clear,
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
        "description": "分身任务管理器 —— 创建、追踪、收集并行子任务",
        "input": {
            "action": "create | list | status | cancel | collect | clear",
            "description": "任务简述（create 时）",
            "task": "完整任务描述（create 时）",
            "task_id": "任务 ID（status / cancel 时）",
            "status": "筛选状态（list 时）",
            "wait": "是否等待（collect 时）",
            "timeout": "等待超时秒数（collect 时）",
        },
        "output": {
            "ok": "是否成功",
            "data.tasks": "任务列表",
            "data.done": "已完成任务结果（collect 时）",
            "message": "结果消息",
        }
    }