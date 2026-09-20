"""main_agent/utils.py — 日志 + 辅助函数 + 经验背包"""

import os
import time
import traceback
from pathlib import Path
from typing import Optional

# ============================================================
# 日志
# ============================================================

class Logger:
    _debug_enabled = os.environ.get("DEBUG", "").lower() in ("true", "1", "yes")

    @staticmethod
    def info(msg: str):
        print(f"[INFO] {msg}", flush=True)

    @staticmethod
    def debug(msg: str):
        if Logger._debug_enabled:
            print(f"[DEBUG] {msg}", flush=True)

    @staticmethod
    def error(msg: str, exc: Optional[Exception] = None):
        error_msg = f"[ERROR] {msg}"
        if exc:
            error_msg += f"\n{''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}"
        print(error_msg, flush=True)

    @staticmethod
    def warn(msg: str):
        print(f"[WARN] {msg}", flush=True)

    @staticmethod
    def warning(msg: str):
        Logger.warn(msg)


def _log(msg: str):
    Logger.info(msg)


def _now():
    from datetime import datetime
    return datetime.now().isoformat()


# ============================================================
# 项目名提取
# ============================================================

def _extract_project_name(target: str) -> str:
    """从 target 中提取项目名"""
    parts = target.split("/")
    if len(parts) >= 3:
        return parts[1]
    elif len(parts) == 2:
        return parts[1]
    else:
        return target


def _make_project_name_fallback(title: str) -> str:
    """兜底：从标题提取英文名"""
    import re
    english_words = re.findall(r'[a-zA-Z][a-zA-Z0-9_\-]*', title)
    if english_words:
        if len(english_words) >= 2 and len(english_words[0]) < 4:
            name = english_words[0] + '_' + english_words[1]
        else:
            name = english_words[0]
        name = re.sub(r'[^a-zA-Z0-9_-]', '_', name.lower())
        return name[:30] if name else f"app_{int(time.time())}"
    return f"app_{int(time.time())}"


async def _make_project_name_with_llm(agent, title: str) -> str:
    """用 LLM 从标题提取简短英文项目名"""
    import re
    import asyncio
    if not agent or not hasattr(agent, 'llm') or not agent.llm:
        return _make_project_name_fallback(title)
    
    try:
        result = await asyncio.wait_for(
            agent.llm.chat_json([
                {"role": "system", "content": "你是一个项目命名助手。从标题中提取简短、有意义的英文项目名（小写，用下划线分隔，只包含字母数字下划线）。只返回 JSON: {\"name\": \"xxx\"}"},
                {"role": "user", "content": f"标题: {title}"}
            ], role="code"),
            timeout=5.0
        )
        name = result.get("name", "").strip().lower()
        if name and re.match(r'^[a-z][a-z0-9_\-]*$', name) and len(name) >= 2:
            return name[:30]
    except Exception:
        pass
    
    return _make_project_name_fallback(title)


# ============================================================
# 经验背包读写
# ============================================================

MEMORY_DIR = Path("data/memories/main_agent")
EXPERIENCE_DIR = MEMORY_DIR / "experiences"


def _get_experience_file(session_id: str) -> Path:
    """获取经验背包文件路径"""
    EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)
    return EXPERIENCE_DIR / f"{session_id}_backpack.txt"


def _load_experience_backpack(session_id: str) -> str:
    """加载经验背包"""
    file_path = _get_experience_file(session_id)
    if file_path.exists():
        return file_path.read_text(encoding="utf-8").strip()
    return ""


def _save_experience_backpack(session_id: str, content: str):
    """保存经验背包"""
    file_path = _get_experience_file(session_id)
    file_path.write_text(content, encoding="utf-8")


def _get_taskboard_file(session_id: str) -> Path:
    """获取任务看板文件路径"""
    TASKBOARD_DIR = MEMORY_DIR / "taskboards"
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