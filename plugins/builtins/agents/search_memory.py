# plugins/builtins/agents/search_memory.py

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

MEMORY_DIR = Path("data/memories/main_agent")


def _parse_date(date_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except:
        return None


def _get_flow_files(
    session_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
) -> List[Path]:
    """获取日期范围内的所有 flow 文件路径（根目录 + 日期目录）"""
    files = []

    # 1. 先读根目录（当前会话完整数据）
    root_file = MEMORY_DIR / f"{session_id}_flow.json"
    if root_file.exists():
        files.append(root_file)

    # 2. 再读日期目录（历史归档）
    flows_dir = MEMORY_DIR / "flows"
    if flows_dir.exists():
        today = datetime.now()
        start = _parse_date(date_from) if date_from else (today - timedelta(days=30))
        end = _parse_date(date_to) if date_to else today

        if start and end:
            current = start
            while current <= end:
                date_key = current.strftime("%Y-%m-%d")
                day_dir = flows_dir / date_key
                flow_file = day_dir / f"{session_id}.json"
                if flow_file.exists() and flow_file not in files:
                    files.append(flow_file)
                current += timedelta(days=1)

    return files


def _get_all_entries(
    session_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
) -> List[Dict]:
    """获取所有条目"""
    files = _get_flow_files(session_id, date_from, date_to)
    if not files:
        return []

    all_entries = []
    for file_path in files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
                if isinstance(entries, list):
                    all_entries.extend(entries)
        except:
            continue

    # 按时间排序
    all_entries.sort(key=lambda x: x.get("timestamp", ""))
    return all_entries


def _get_searchable_text(entry: Dict[str, Any]) -> str:
    """
    只提取用于匹配的文本内容（不含 timestamp 等元数据）。
    避免用户搜年份/时间字符串时命中所有条目。
    """
    parts = []

    content = entry.get("content", "")
    if content:
        parts.append(str(content))

    summary = entry.get("summary", "")
    if summary:
        parts.append(str(summary))

    # 用户消息直接取 content
    # AI 消息可能有 args.params.content（工具参数），但那是工具内容，不是回复，不纳入匹配
    # system 消息一般不参与回答，但内容里可能有用户相关的信息，可纳入匹配
    if entry.get("from") == "system":
        result = entry.get("result", "")
        if result:
            parts.append(str(result))

    return " ".join(parts).lower()


def _score_entry(entry: Dict[str, Any], keywords: List[str]) -> int:
    """
    打分：命中关键词的数量。
    返回 0 表示不匹配。
    """
    if not keywords:
        return 0
    text = _get_searchable_text(entry)
    if not text:
        return 0
    score = 0
    for kw in keywords:
        if kw and kw.lower() in text:
            score += 1
    return score


def _extract_ai_content(entry: Dict[str, Any]) -> str:
    """
    从 AI 条目中提取实际回复内容。
    注意：不再从 args.params.content 取，避免误取工具参数。
    """
    content = entry.get("content", "")
    if content:
        return content

    summary = entry.get("summary", "")
    if summary:
        return summary

    return ""


def _deduplicate_entries(entries: List[Dict]) -> List[Dict]:
    """按 (timestamp, from, content 全串) 去重"""
    seen = set()
    result = []
    for entry in entries:
        content = entry.get("content", "") or entry.get("summary", "")
        key = (
            entry.get("timestamp", ""),
            entry.get("from", ""),
            content
        )
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


def _build_clean_entry(entry: Dict[str, Any]) -> Dict:
    """构建干净的条目（只保留关键信息）"""
    from_user = entry.get("from", "")

    clean = {
        "timestamp": entry.get("timestamp", ""),
        "from": from_user
    }

    if from_user == "user":
        clean["content"] = entry.get("content", "")

    elif from_user == "ai":
        content = _extract_ai_content(entry)
        clean["content"] = content if content else entry.get("summary", "")

    return clean


def _search_entries_with_context(
    session_id: str,
    keywords: List[str],
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 30
) -> tuple[List[Dict], int]:
    """
    搜索匹配条目，补全因果链，去重，过滤噪音。
    """
    # 0. 关键词去重 + 小写
    kws = list(set(kw.strip() for kw in keywords if kw and kw.strip()))

    # 1. 获取所有条目
    all_entries = _get_all_entries(session_id, date_from, date_to)
    if not all_entries:
        return [], 0

    total = len(all_entries)

    # 2. 打分 + 找出匹配的索引
    scored = []
    for i, entry in enumerate(all_entries):
        score = _score_entry(entry, kws)
        if score > 0:
            scored.append((score, i))

    if not scored:
        return [], total

    # 3. 按分数排序，取前 N 个作为锚点（避免低分命中稀释上下文）
    scored.sort(key=lambda x: x[0], reverse=True)
    # 最多取 30 个锚点，避免上下文过长
    anchor_indices = [idx for _, idx in scored[:30]]

    # 4. 补全因果链（步长放宽到 20）
    expanded_indices = set()
    for idx in anchor_indices:
        entry = all_entries[idx]
        from_user = entry.get("from", "")

        # 匹配到 AI → 补全前面的用户消息
        if from_user == "ai":
            for prev_idx in range(idx - 1, max(0, idx - 20), -1):
                if all_entries[prev_idx].get("from") == "user":
                    expanded_indices.add(prev_idx)
                    break
            expanded_indices.add(idx)

        # 匹配到用户 → 补全后面的 AI 回复
        elif from_user == "user":
            expanded_indices.add(idx)
            for next_idx in range(idx + 1, min(len(all_entries), idx + 20)):
                if all_entries[next_idx].get("from") == "ai":
                    expanded_indices.add(next_idx)
                    break

        # 匹配到 system（reply 结果）→ 补全用户消息
        elif from_user == "system":
            action = entry.get("action", "")
            if "reply" in action:
                for prev_idx in range(idx - 1, max(0, idx - 20), -1):
                    if all_entries[prev_idx].get("from") == "user":
                        expanded_indices.add(prev_idx)
                        break
                expanded_indices.add(idx)

    # 5. 去重
    unique_indices = sorted(set(expanded_indices))

    # 6. 构建干净条目（只保留 user 和 ai）
    clean_entries = []
    for idx in unique_indices:
        entry = all_entries[idx]
        from_user = entry.get("from", "")

        if from_user in ["user", "ai"]:
            clean_entry = _build_clean_entry(entry)
            clean_entries.append(clean_entry)
        # system 直接跳过，不进入上下文

    # 7. 再次去重（构建后可能有重复）
    clean_entries = _deduplicate_entries(clean_entries)

    # 8. 按时间排序
    clean_entries.sort(key=lambda x: x.get("timestamp", ""))

    # 9. 截断限制
    if len(clean_entries) > limit:
        clean_entries = clean_entries[:limit]

    return clean_entries, total


async def _generate_answer(agent, query: str, entries: List[Dict], date_from: str, date_to: str) -> str:
    """根据干净的因果链生成回答"""
    if not entries:
        return f"在 {date_from} ~ {date_to} 期间，未找到与「{query}」相关的记录。"

    items_json = json.dumps(entries, ensure_ascii=False, indent=2)

    prompt = f"""用户想问：{query}

时间范围：{date_from} ~ {date_to}
匹配到的对话记录（已按时间排序，已过滤系统噪音）：

{items_json}

请根据这些记录，回答用户的问题。
要求：
- 如果记录中有明确答案，直接回答
- 如果记录中包含关键信息（如邮箱、地址、账号、网址等），一定要提取出来
- 按时间顺序还原事件过程
- 如果记录不足以回答问题，说明"当前记录中未找到足够信息"
- 不要输出原始记录，直接回答问题
"""

    try:
        raw = await agent.llm.chat([{"role": "user", "content": prompt}])
        if not raw or not raw.strip():
            return f"找到 {len(entries)} 条相关记录，但生成回答时返回空。\n\n匹配到的记录摘要：\n{items_json[:1500]}"
        return raw.strip()
    except Exception as e:
        return f"生成回答失败：{e}"


async def execute(envelop, agent):
    payload = envelop.payload

    session_id = payload.get("session_id")
    if not session_id:
        envelop.payload = {"ok": False, "error": "缺少 session_id"}
        return envelop

    query = payload.get("query")
    if not query:
        envelop.payload = {"ok": False, "error": "缺少 query 参数"}
        return envelop

    keywords = payload.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [keywords]
    if not keywords:
        envelop.payload = {"ok": False, "error": "缺少 keywords 参数"}
        return envelop

    date_from = payload.get("date_from")
    date_to = payload.get("date_to")

    today = datetime.now().strftime("%Y-%m-%d")
    default_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    date_from = date_from or default_from
    date_to = date_to or today

    # 搜索并补全因果链
    entries, total = _search_entries_with_context(
        session_id, keywords, date_from, date_to, limit=50
    )

    # 生成回答
    answer = await _generate_answer(agent, query, entries, date_from, date_to)

    envelop.payload = {
        "ok": True,
        "answer": answer,
        "matched_count": len(entries),
        "total_in_range": total,
        "range": f"{date_from} ~ {date_to}",
        "keywords": keywords
    }

    return envelop


def help() -> dict:
    return {
        "route": "builtins/agents/search_memory",
        "description": "搜索历史记忆，根据关键词过滤，补全因果链，返回详细回答",
        "input": {
            "session_id": "会话ID（必填）",
            "query": "用户想问的问题（必填）",
            "keywords": "关键词列表或字符串（必填），匹配任意一个即命中",
            "date_from": "开始日期 YYYY-MM-DD（可选，默认30天前）",
            "date_to": "结束日期 YYYY-MM-DD（可选，默认今天）"
        },
        "output": {
            "ok": "是否成功",
            "answer": "LLM 生成的详细回答",
            "matched_count": "匹配到的记录数",
            "total_in_range": "时间范围内的总记录数",
            "range": "搜索的时间范围",
            "keywords": "使用的关键词"
        }
    }