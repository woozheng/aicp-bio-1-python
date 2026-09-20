# plugins/applications/studio/engine/failure_learner.py
"""
失败模式自学习 — 从 os_agent 提取
错误指纹提取 + 记录 + 警告注入
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional


# ============================================================
# 配置
# ============================================================

FAILURE_PATTERNS_PATH = Path("data/agents/failure_patterns.json")
MAX_PATTERNS = 50


# ============================================================
# 错误归一化
# ============================================================

def _normalize_error(error_msg: str) -> str:
    """归一化错误信息，去掉变量部分，只保留结构"""
    if not error_msg:
        return ""
    normalized = error_msg

    # 文件路径 → <path>
    normalized = re.sub(r'File ".*?", line \d+', 'File "<path>", line <n>', normalized)

    # 十六进制地址 → <addr>
    normalized = re.sub(r'0x[0-9a-fA-F]+', '0x<addr>', normalized)

    # 数字 → <n>
    normalized = re.sub(r'\b\d+\b', '<n>', normalized)

    # 引号内的值 → <var>
    normalized = re.sub(r"'[^']*'", "'<var>'", normalized)
    normalized = re.sub(r'"[^"]*"', '"<var>"', normalized)

    # 多余空白压缩
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    return normalized


# ============================================================
# 指纹提取
# ============================================================

def extract_fingerprint(error_msg: str) -> Optional[dict]:
    """
    从错误信息中提取失败指纹。
    相似错误（如路径不同但类型相同）会生成相同指纹，自动合并。
    
    输入: 错误信息字符串
    输出: {"fingerprint": "auto_a1b2c3d4", "hint": "...", "normalized": "..."}
    如果 error_msg 为空，返回 None
    """
    if not error_msg:
        return None

    normalized = _normalize_error(error_msg)
    # 取前 300 字符做 hash，保证足够的信息量
    short_hash = hashlib.md5(normalized[:300].encode()).hexdigest()[:8]

    return {
        "fingerprint": f"auto_{short_hash}",
        "hint": error_msg[:300],
        "normalized": normalized[:200],
    }


# ============================================================
# 记录失败模式
# ============================================================

def record(fingerprint: dict, plugin_name: str, fix_success: bool):
    """
    记录一次失败模式。
    相同指纹自动合并，权重（count）累加。
    
    输入:
      - fingerprint: extract_fingerprint 的返回值
      - plugin_name: 出错的插件名
      - fix_success: 最终是否修复成功
    """
    if not fingerprint:
        return

    FAILURE_PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 加载已有记录
    patterns = []
    if FAILURE_PATTERNS_PATH.exists():
        try:
            patterns = json.loads(FAILURE_PATTERNS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            patterns = []

    # 查找是否已存在相同指纹
    existing = next(
        (p for p in patterns if p["fingerprint"] == fingerprint["fingerprint"]),
        None,
    )

    if existing:
        existing["count"] += 1
        existing["last_seen"] = datetime.now().isoformat()
        existing["status"] = "fixed" if fix_success else "unresolved"
        existing["last_plugin"] = plugin_name
    else:
        patterns.append({
            "fingerprint": fingerprint["fingerprint"],
            "hint": fingerprint["hint"],
            "normalized": fingerprint.get("normalized", ""),
            "count": 1,
            "first_seen": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat(),
            "status": "fixed" if fix_success else "unresolved",
            "last_plugin": plugin_name,
        })

    # 按优先级排序：未解决 > 已解决，次数多 > 次数少
    patterns.sort(key=lambda x: (
        0 if x["status"] == "unresolved" else 1,
        -x["count"],
        x["last_seen"],
    ))

    # 只保留前 MAX_PATTERNS 条
    patterns = patterns[:MAX_PATTERNS]

    FAILURE_PATTERNS_PATH.write_text(
        json.dumps(patterns, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============================================================
# 获取活跃警告
# ============================================================

def get_active_warnings(limit: int = 5) -> str:
    """
    获取权重最高的失败模式，供 generator 生成时注入提示。
    
    输入: limit — 最多返回几条
    输出: 一段可直接注入 system prompt 的文本
          如果没有警告，返回空字符串
    """
    if not FAILURE_PATTERNS_PATH.exists():
        return ""

    try:
        patterns = json.loads(FAILURE_PATTERNS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    if not patterns:
        return ""

    selected = patterns[:limit]
    hints = []
    for p in selected:
        tag = "🔴" if p["status"] == "unresolved" else "🟡"
        hints.append(
            f"{tag} {p['hint'][:200]} "
            f"(已出现{p['count']}次, 最近插件: {p['last_plugin']})"
        )

    header = "⚠️【系统最近踩过的坑，生成代码时务必避免】"
    return header + "\n" + "\n".join(f"- {h}" for h in hints)


# ============================================================
# 清空记录
# ============================================================

def clear():
    """清空所有失败记录"""
    if FAILURE_PATTERNS_PATH.exists():
        FAILURE_PATTERNS_PATH.unlink()


__all__ = [
    "extract_fingerprint",
    "record",
    "get_active_warnings",
    "clear",
]