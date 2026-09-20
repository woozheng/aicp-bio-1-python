"""add_experience — 沉淀经验到背包（内置总结 LLM 归并）"""

from datetime import datetime
from pathlib import Path


# ============================================================
# 路径
# ============================================================

MEMORY_DIR = Path("data/memories/main_agent")
EXPERIENCE_DIR = MEMORY_DIR / "experiences"
HISTORY_DIR = EXPERIENCE_DIR / "history"


# ============================================================
# 基础读写
# ============================================================

def _get_experience_file(session_id: str) -> Path:
    EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)
    return EXPERIENCE_DIR / f"{session_id}_backpack.txt"


def _load_experience(session_id: str) -> str:
    file_path = _get_experience_file(session_id)
    if file_path.exists():
        return file_path.read_text(encoding="utf-8").strip()
    return ""


def _save_experience(session_id: str, content: str):
    file_path = _get_experience_file(session_id)
    file_path.write_text(content, encoding="utf-8")


def _backup_experience(session_id: str, content: str) -> str:
    if not content:
        return ""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    revision = f"{session_id}_{ts}"
    (HISTORY_DIR / f"{revision}.txt").write_text(content, encoding="utf-8")
    return revision


# ============================================================
# 内置总结 LLM
# ============================================================

MERGE_SYSTEM_PROMPT = """你是经验背包的归并器（Experience Backpack Merger）。

你的唯一职责：把主 agent 提交的新版本，安全地归并进当前背包。

规则：
1. 以【当前背包】为基础，把【新版本】里真正的新增、修正合并进去。
2. 保留当前背包里未被新版本触及的内容。
3. 除非新版本明确表示某条已废弃，否则不要删除当前背包里的内容。
4. 如果新版本只是换了措辞但语义相同，保留更清晰、更完整的版本。
5. 如果新版本和当前背包内容完全重复，去重。
6. 保持原有格式风格（段落、标题、编号），不要强行重排结构。
7. 不要输出任何解释、前言、后记。

只输出归并后的完整背包正文。"""


async def _merge_with_llm(current: str, proposed: str, agent) -> str:
    """
    调用总结 LLM 归并 current 和 proposed。
    失败时回退为「保留 current + 追加 proposed」，绝不丢内容。
    """
    prompt = f"""{MERGE_SYSTEM_PROMPT}

【当前背包】
{current}

【主 agent 提交的新版本】
{proposed}"""

    try:
        merged = await agent.llm.chat([{"role": "user", "content": prompt}])
        merged = (merged or "").strip()
        if not merged:
            raise ValueError("归并结果为空")
        return merged
    except Exception as e:
        # 回退：保守拼接，保证不丢
        try:
            print(f"[add_experience] 归并失败，回退保守合并: {e}")
        except Exception:
            pass
        if current and proposed:
            return current + "\n\n" + proposed
        return current or proposed


# ============================================================
# 主入口
# ============================================================

async def execute(envelop, agent):
    """沉淀经验到经验背包"""
    # ★★★ 兼容两种传参方式 ★★★
    params = envelop.payload.get("params", {})

    if not params:
        params = {k: v for k, v in envelop.payload.items() if k != "action"}

    session_id = envelop.meta.get("session_id", "default")

    experience = params.get("experience", "")
    mode = params.get("mode", "append")  # append / replace

    if not experience:
        envelop.payload = {"ok": False, "error": "缺少 experience 参数"}
        return envelop

    try:
        current = _load_experience(session_id)

        revision = ""
        diff_summary = ""

        if mode == "replace":
            if current:
                revision = _backup_experience(session_id, current)
                new_content = await _merge_with_llm(current, experience, agent)

                if new_content == current:
                    diff_summary = "无实质变化"
                elif experience in new_content:
                    diff_summary = "已归并新版本，保留原有经验"
                else:
                    diff_summary = "已归并，内容有调整"
            else:
                new_content = experience
                diff_summary = "首次写入"
        else:
            # append 路径不变
            if current:
                new_content = current + "\n" + experience
            else:
                new_content = experience

        _save_experience(session_id, new_content)

        envelop.payload = {
            "ok": True,
            "message": f"✅ 经验已{'归并更新' if mode == 'replace' else '追加'}，当前背包 {len(new_content)} 字",
            "mode": mode,
            "size": len(new_content),
            "revision": revision,
            "diff": diff_summary,
        }
        return envelop

    except Exception as e:
        envelop.payload = {"ok": False, "error": f"保存经验失败: {e}"}
        return envelop


def help():
    return {
        "route": "builtins/tools/add_experience",
        "description": "沉淀经验到经验背包",
        "input": {
            "experience": "经验内容",
            "mode": "append / replace（可选，默认 append）"
        },
        "output": {
            "ok": "是否成功",
            "message": "结果消息",
            "mode": "使用的模式",
            "size": "背包大小"
        }
    }