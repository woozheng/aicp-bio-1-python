"""add_task_board — 更新当前任务看板（完全独立）"""

import json
from pathlib import Path
from datetime import datetime

import core


# ============================================================
# 内部工具函数
# ============================================================

MEMORY_DIR = Path("data/memories/main_agent")
TASKBOARD_DIR = MEMORY_DIR / "taskboards"


def _get_taskboard_file(session_id: str) -> Path:
    """获取任务看板文件路径"""
    TASKBOARD_DIR.mkdir(parents=True, exist_ok=True)
    return TASKBOARD_DIR / f"{session_id}_taskboard.txt"


def _load_taskboard(session_id: str) -> str:
    """加载任务看板"""
    file_path = _get_taskboard_file(session_id)
    if file_path.exists():
        return file_path.read_text(encoding="utf-8").strip()
    return ""


def _save_taskboard(session_id: str, content: str):
    """保存任务看板"""
    file_path = _get_taskboard_file(session_id)
    file_path.write_text(content, encoding="utf-8")


# ============================================================
# 主入口
# ============================================================

async def execute(envelop, agent):
    """更新当前任务看板"""
    # ★★★ 兼容两种传参方式 ★★★
    params = envelop.payload.get("params", {})
    
    # 如果 params 为空，从顶层取（兼容 use_tool 展平方式）
    if not params:
        params = {k: v for k, v in envelop.payload.items() if k != "action"}
    
    session_id = envelop.meta.get("session_id", "default")

    content = params.get("content", "")
    mode = params.get("mode", "replace")  # replace / append / clear

    if mode == "clear":
        _save_taskboard(session_id, "")
        envelop.payload = {
            "ok": True,
            "message": "✅ 任务看板已清空",
            "mode": "clear",
            "size": 0
        }
        return envelop

    if not content:
        envelop.payload = {"ok": False, "error": "缺少 content 参数"}
        return envelop

    try:
        current = _load_taskboard(session_id)

        if mode == "append":
            new_content = current + "\n" + content if current else content
        else:
            new_content = content

        _save_taskboard(session_id, new_content)

        envelop.payload = {
            "ok": True,
            "message": f"✅ 任务看板已{'追加' if mode == 'append' else '更新'}，当前 {len(new_content)} 字",
            "mode": mode,
            "size": len(new_content)
        }
        return envelop

    except Exception as e:
        envelop.payload = {"ok": False, "error": f"保存任务看板失败: {e}"}
        return envelop


def help():
    return {
        "route": "builtins/tools/add_task_board",
        "description": "更新当前任务看板（记录当前任务进度和上下文）",
        "input": {
            "content": "任务看板内容（纯文本）",
            "mode": "replace / append / clear（可选，默认 replace）"
        },
        "output": {
            "ok": "是否成功",
            "message": "结果消息",
            "mode": "使用的模式",
            "size": "看板大小"
        }
    }