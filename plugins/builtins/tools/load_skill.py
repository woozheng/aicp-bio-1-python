"""load_skill — 加载技能到当前上下文（完全独立）"""

import json
from pathlib import Path

import core


# ============================================================
# 内部工具函数
# ============================================================

MEMORY_DIR = Path("data/memories/main_agent")
SKILL_DIR = MEMORY_DIR / "active_skills"


def _get_skill_file(session_id: str) -> Path:
    """获取活跃技能文件路径"""
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    return SKILL_DIR / f"{session_id}_skill.txt"


def _load_skill(session_id: str) -> str:
    """加载当前活跃技能"""
    file_path = _get_skill_file(session_id)
    if file_path.exists():
        return file_path.read_text(encoding="utf-8").strip()
    return ""


def _save_skill(session_id: str, content: str):
    """保存活跃技能"""
    file_path = _get_skill_file(session_id)
    file_path.write_text(content, encoding="utf-8")


def _clear_skill(session_id: str):
    """清空活跃技能"""
    file_path = _get_skill_file(session_id)
    if file_path.exists():
        file_path.unlink()


# ============================================================
# 主入口
# ============================================================

async def execute(envelop, agent):
    """加载技能到当前上下文"""
    # ★★★ 兼容两种传参方式 ★★★
    params = envelop.payload.get("params", {})

    if not params:
        params = {k: v for k, v in envelop.payload.items() if k != "action"}

    session_id = (
        envelop.meta.get("session_id")
        or params.get("session_id")
        or "default"
    )

    action = envelop.payload.get("action", "load")
    skill_id = params.get("skill_id", "")
    mode = params.get("mode", "load")  # load / clear

    # ★ 读取当前技能（index.html 弹窗加载）
    if action == "get_active":
        content = _load_skill(session_id)
        envelop.payload = {
            "ok": True,
            "session_id": session_id,
            "content": content,
            "size": len(content),
            "has_skill": bool(content),
        }
        return envelop

    # ★ 保存编辑后的内容（index.html 弹窗保存）
    if action == "save_active":
        new_content = (
            envelop.payload.get("content")
            or params.get("content")
            or ""
        )
        _save_skill(session_id, new_content)
        envelop.payload = {
            "ok": True,
            "message": f"✅ 已保存，{len(new_content)} 字",
            "size": len(new_content),
        }
        return envelop

    # ★ 清空模式
    if mode == "clear":
        _clear_skill(session_id)
        envelop.payload = {
            "ok": True,
            "message": "✅ 技能已卸载",
            "mode": "clear",
        }
        return envelop

    if not skill_id:
        envelop.payload = {"ok": False, "error": "缺少 skill_id 参数"}
        return envelop

    try:
        # ★ 精确匹配：调 skill_loader.detail
        result = await agent.system.call(core.Envelop(
            sender=envelop.sender or "builtins/tools/load_skill",
            receiver="builtins/tools/skill_loader_api",
            payload={"action": "detail", "skill_id": skill_id},
            meta={"session_id": session_id},
        ))

        # ★ 精确匹配失败 → 尝试模糊匹配（用 search 或 list 找候选）
        if not result or not result.payload.get("ok"):
            # 调 search 拿候选（search 前会自动 scan，确保新技能可见）
            search_result = await agent.system.call(core.Envelop(
                sender=envelop.sender or "builtins/tools/load_skill",
                receiver="builtins/tools/skill_loader_api",
                payload={
                    "action": "search",
                    "query": skill_id,
                    "keywords": [skill_id],
                    "limit": 5,
                },
                meta={"session_id": session_id},
            ))

            candidates = []
            if search_result and search_result.payload.get("ok"):
                candidates = search_result.payload.get("data", {}).get("skills", [])

            # ★ 模糊匹配：候选里找 skill_id 包含输入，或 file_path 包含输入的
            lower = skill_id.lower()
            fuzzy_matches = [
                s for s in candidates
                if lower in s.get("skill_id", "").lower()
                or lower in s.get("file_path", "").lower()
                or lower in s.get("title", "").lower()
            ]

            if len(fuzzy_matches) == 1:
                # 唯一匹配 → 用真实 skill_id 重试
                real_skill_id = fuzzy_matches[0]["skill_id"]
                print(f"[load_skill] 模糊匹配: {skill_id} → {real_skill_id}")

                result = await agent.system.call(core.Envelop(
                    sender=envelop.sender or "builtins/tools/load_skill",
                    receiver="builtins/tools/skill_loader_api",
                    payload={"action": "detail", "skill_id": real_skill_id},
                    meta={"session_id": session_id},
                ))
                skill_id = real_skill_id

            elif len(fuzzy_matches) > 1:
                envelop.payload = {
                    "ok": False,
                    "error": f'skill_id "{skill_id}" 匹配到多个技能，请用精确 ID',
                    "candidates": [s["skill_id"] for s in fuzzy_matches],
                }
                return envelop

        # 最终检查
        if not result or not result.payload.get("ok"):
            envelop.payload = {"ok": False, "error": f"技能不存在: {skill_id}"}
            return envelop

        skill_data = result.payload.get("data", {})
        content = skill_data.get("content", "")
        title = skill_data.get("title", skill_id)
        skill_dir = skill_data.get("skill_dir", "")
        file_path = skill_data.get("file_path", "")

        if not content:
            envelop.payload = {"ok": False, "error": f"技能 {skill_id} 内容为空"}
            return envelop

        # ★ 注入运行时目录提示，让 agent 知道相对路径的基准
        if skill_dir:
            runtime_header = (
                f"<!-- ============================================ -->\n"
                f"<!-- 使用工具调用技能运行时信息 -->\n"
                f"<!-- 技能目录: {skill_dir} -->\n"
                f"<!-- 技能文件: {file_path} -->\n"
                f"<!-- 本技能中所有相对路径（如 scripts/xxx.py、editing.md） -->\n"
                f"<!-- 均相对于上述技能目录。 -->\n"
                f"<!-- 使用绝对路径拼接。 -->\n"
                f"<!-- ============================================ -->\n\n"
            )
            content = runtime_header + content

        # ★ 写入活跃技能文件（下一轮 build_user 拼进 prompt）
        _save_skill(session_id, content)

        envelop.payload = {
            "ok": True,
            "message": f"✅ 技能已加载：{title}，{len(content)} 字",
            "skill_id": skill_id,
            "title": title,
            "skill_dir": skill_dir,  
            "size": len(content),
        }
        return envelop

    except Exception as e:
        envelop.payload = {"ok": False, "error": f"加载技能失败: {e}"}
        return envelop


def help():
    return {
        "route": "builtins/tools/load_skill",
        "description": "加载技能到当前上下文（下一轮 LLM 思考时可看到技能全文）",
        "input": {
            "skill_id": "技能 ID（从 skill_loader.search 获取）",
            "mode": "load / clear（可选，默认 load；clear 表示卸载）",
            "action": "load / get_active / save_active（可选，默认 load）",
            "content": "action=save_active 时传入的新内容",
            "session_id": "可选，优先取 meta.session_id",
        },
        "output": {
            "ok": "是否成功",
            "message": "结果消息",
            "skill_id": "已加载的技能 ID",
            "title": "技能标题",
            "size": "技能内容字数",
            "content": "当前技能全文（action=get_active）",
            "has_skill": "是否已有技能（action=get_active）",
        }
    }