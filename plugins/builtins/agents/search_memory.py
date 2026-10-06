# plugins/builtins/agents/search_memory.py

import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

MEMORY_DIR = Path("data/memories/main_agent")
EXPERIENCE_DIR = MEMORY_DIR / "experiences"
REFLECTION_DIR = MEMORY_DIR / "reflections"


# ============================================================
# 基础：日期与 flow 读取
# ============================================================

def _parse_date(date_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return None


def _get_flow_files(
    session_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
) -> List[Path]:
    files = []

    root_file = MEMORY_DIR / f"{session_id}_flow.json"
    if root_file.exists():
        files.append(root_file)

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
        except Exception:
            continue

    all_entries.sort(key=lambda x: x.get("timestamp", ""))
    return all_entries


# ============================================================
# 基础：打分、去重、条目清洗
# ============================================================

def _get_searchable_text(entry: Dict[str, Any]) -> str:
    parts = []
    content = entry.get("content", "")
    if content:
        parts.append(str(content))
    summary = entry.get("summary", "")
    if summary:
        parts.append(str(summary))
    if entry.get("from") == "system":
        result = entry.get("result", "")
        if result:
            parts.append(str(result))
    return " ".join(parts).lower()


def _score_entry(entry: Dict[str, Any], keywords: List[str]) -> int:
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
    content = entry.get("content", "")
    if content:
        return content
    summary = entry.get("summary", "")
    if summary:
        return summary
    return ""


def _deduplicate_entries(entries: List[Dict]) -> List[Dict]:
    seen = set()
    result = []
    for entry in entries:
        content = entry.get("content", "") or entry.get("summary", "")
        key = (entry.get("timestamp", ""), entry.get("from", ""), content)
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


def _build_clean_entry(entry: Dict[str, Any]) -> Dict:
    from_user = entry.get("from", "")
    clean = {"timestamp": entry.get("timestamp", ""), "from": from_user}
    if from_user == "user":
        clean["content"] = entry.get("content", "")
    elif from_user == "ai":
        content = _extract_ai_content(entry)
        clean["content"] = content if content else entry.get("summary", "")
    return clean


# ============================================================
# 搜索 + 因果链补全（get 用，完全不变）
# ============================================================

def _search_entries_with_context(
    session_id: str,
    keywords: List[str],
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 30
) -> tuple[List[Dict], int]:
    kws = list(set(kw.strip() for kw in keywords if kw and kw.strip()))

    all_entries = _get_all_entries(session_id, date_from, date_to)
    if not all_entries:
        return [], 0

    total = len(all_entries)

    scored = []
    for i, entry in enumerate(all_entries):
        score = _score_entry(entry, kws)
        if score > 0:
            scored.append((score, i))

    if not scored:
        return [], total

    scored.sort(key=lambda x: x[0], reverse=True)
    anchor_indices = [idx for _, idx in scored[:30]]

    expanded_indices = set()
    for idx in anchor_indices:
        entry = all_entries[idx]
        from_user = entry.get("from", "")

        if from_user == "ai":
            for prev_idx in range(idx - 1, max(0, idx - 20), -1):
                if all_entries[prev_idx].get("from") == "user":
                    expanded_indices.add(prev_idx)
                    break
            expanded_indices.add(idx)

        elif from_user == "user":
            expanded_indices.add(idx)
            for next_idx in range(idx + 1, min(len(all_entries), idx + 20)):
                if all_entries[next_idx].get("from") == "ai":
                    expanded_indices.add(next_idx)
                    break

        elif from_user == "system":
            action = entry.get("action", "")
            if "reply" in action:
                for prev_idx in range(idx - 1, max(0, idx - 20), -1):
                    if all_entries[prev_idx].get("from") == "user":
                        expanded_indices.add(prev_idx)
                        break
                expanded_indices.add(idx)

    unique_indices = sorted(set(expanded_indices))

    clean_entries = []
    for idx in unique_indices:
        entry = all_entries[idx]
        if entry.get("from") in ["user", "ai"]:
            clean_entries.append(_build_clean_entry(entry))

    clean_entries = _deduplicate_entries(clean_entries)
    clean_entries.sort(key=lambda x: x.get("timestamp", ""))

    if len(clean_entries) > limit:
        clean_entries = clean_entries[:limit]

    return clean_entries, total


# ============================================================
# 原功能：回答问题（完全不变）
# ============================================================

async def _generate_answer(agent, query: str, entries: List[Dict], date_from: str, date_to: str) -> str:
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


# ============================================================
# 窗口化：取一整段 flow
# ============================================================

def _get_entries_in_window(
    session_id: str,
    after_ts: str,
    before_ts: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 400
) -> tuple[List[Dict], int]:
    all_entries = _get_all_entries(session_id, date_from, date_to)
    if not all_entries:
        return [], 0

    total = len(all_entries)

    windowed = []
    for e in all_entries:
        ts = e.get("timestamp", "")
        if after_ts and ts < after_ts:
            continue
        if before_ts and ts > before_ts:
            continue
        windowed.append(e)

    clean = []
    for e in windowed:
        if e.get("from") in ["user", "ai"]:
            clean.append(_build_clean_entry(e))

    clean = _deduplicate_entries(clean)
    clean.sort(key=lambda x: x.get("timestamp", ""))

    if len(clean) > limit:
        clean = clean[-limit:]

    return clean, total


# ============================================================
# 工具：时间戳校验 + 文件头解析
# ============================================================

def _is_valid_ts(ts: str) -> bool:
    """判断字符串是否像一个合法时间戳，避免把 '- focus:' 这类垃圾值当时间用。"""
    if not ts or not isinstance(ts, str):
        return False
    ts = ts.strip()
    # 必须以数字开头，且含日期分隔符
    if not re.match(r"^\d{4}-\d{2}-\d{2}", ts):
        return False
    try:
        datetime.fromisoformat(ts)
        return True
    except Exception:
        return False


def _parse_reflection_header(head: str) -> Dict[str, str]:
    """
    按行解析反思文件头里的 '- key: value' 字段。
    只取第一个冒号后的内容，value 为空则记为 ''。
    """
    meta = {}
    for line in head.split("\n"):
        line = line.strip()
        if not line.startswith("- "):
            continue
        body = line[2:]
        if ":" not in body:
            continue
        k, v = body.split(":", 1)
        k = k.strip()
        v = v.strip()
        if k and k not in meta:
            meta[k] = v
    return meta


# ============================================================
# 自动窗口：after_ts 不传时，接续上次实际跑到的终点
# ============================================================

def _resolve_auto_after_ts(session_id: str, default_hours: int = 12) -> str:
    """
    自动窗口起点，按优先级：
    1. 最近一条反思文件头里的 window_end（上次实际跑到哪）
    2. 最近一条反思的 before_ts（手动窗口的终点）
    3. 最近一条反思的 after_ts + default_hours
    4. 一条历史都没有，默认 now - default_hours
    只用合法时间戳，非法值跳过。
    """
    if REFLECTION_DIR.exists():
        files = sorted(
            REFLECTION_DIR.glob(f"{session_id}_*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for fp in files:
            try:
                head = fp.read_text(encoding="utf-8")[:1500]
            except Exception:
                continue

            meta = _parse_reflection_header(head)

            for key in ("window_end", "before_ts"):
                val = meta.get(key, "")
                if _is_valid_ts(val):
                    return val

            after_val = meta.get("after_ts", "")
            if _is_valid_ts(after_val):
                try:
                    dt = datetime.fromisoformat(after_val) + timedelta(hours=default_hours)
                    return dt.strftime("%Y-%m-%dT%H:%M:%S")
                except Exception:
                    return after_val

    return (datetime.now() - timedelta(hours=default_hours)).strftime("%Y-%m-%dT%H:%M:%S")


# ============================================================
# 信号识别：规则初筛（只用于标注重点，不做硬过滤）
# ============================================================

ERROR_MARKERS = [
    "error", "失败", "报错", "异常", "traceback", "timeout", "超时",
    "重试", "retry", "invalid", "缺少", "未找到", "not found",
    "failed", "failure", "refused", "blocked", "crash", "exception",
    "无法", "不能", "denied", "forbidden",
]

CORRECTION_MARKERS = [
    "不对", "错了", "不是", "应该", "其实", "纠正", "别", "不要",
    "重新", "停", "有问题", "wrong", "incorrect",
    "我说的是", "我的意思是",
]

RECURSION_MARKERS = [
    "递归", "深度", "深度过高", "已达上限", "执行深度",
    "recursion", "depth", "max depth", "limit reached",
]


def _has_any(text: str, markers: List[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(m.lower() in low for m in markers)


def _detect_signals(entries: List[Dict]) -> List[Dict]:
    """
    规则初筛：给每条打信号标签。
    只用于“标注重点”，不决定条目是否进入复盘。
    """
    tagged = []
    for e in entries:
        parts = []
        for k in ("content", "summary", "result", "message", "text"):
            v = e.get(k)
            if v:
                parts.append(str(v))
        text = " ".join(parts)

        signals = []
        if _has_any(text, ERROR_MARKERS):
            signals.append("error")
        if e.get("from") == "user" and _has_any(text, CORRECTION_MARKERS):
            signals.append("correction")
        if _has_any(text, RECURSION_MARKERS):
            signals.append("recursion")

        if signals:
            tagged.append({
                "timestamp": e.get("timestamp", ""),
                "from": e.get("from", ""),
                "signals": signals,
            })

    return tagged


# ============================================================
# 信号识别：LLM 复核（只用于标注重点）
# ============================================================

SIGNAL_REVIEW_SYSTEM_PROMPT = """你是执行记录的信号复核器。你的职责：从一批“规则初筛出的候选异常条目”里，判断哪些真正构成异常信号。

## 判断标准

真正构成异常信号：
- 出现了实际错误、报错、异常、traceback
- 出现了重试、超时、工具调用失败
- 用户明确纠正了 agent 的做法
- 递归过深、执行深度触顶
- 决策质量明显有问题（即使没报错）

不构成异常信号：
- 只是正常提到某个词，没有造成实际问题
- 用户说“好的”“谢谢”“不对不对我说错了”这类口头语，不构成纠正
- 正常技术讨论里出现“error”这个词，但只是在讲概念

## 输出格式（严格 JSON，不要解释、不要 markdown 代码块）

{
  "items": [
    {
      "timestamp": "...",
      "from": "...",
      "signals": ["error"|"correction"|"recursion"|"other"],
      "reason": "一句话说明为什么构成信号"
    }
  ]
}

如果全部不构成信号，输出：
{"items": []}"""


async def _detect_signals_llm(agent, entries: List[Dict]) -> List[Dict]:
    """
    规则初筛 + LLM 复核。
    结果只用于给事件打 is_flagged 标记，不决定条目是否进入复盘。
    LLM 失败时回退规则结果。
    """
    tagged = _detect_signals(entries)
    if not tagged:
        return []

    candidates = tagged[-80:]
    items_json = json.dumps(candidates, ensure_ascii=False, indent=2)

    prompt = f"""下面是规则初筛出的候选异常条目。请判断哪些真正构成异常信号。

【候选条目】
{items_json}

请按规则输出 JSON。"""

    try:
        raw = await agent.llm.chat([
            {"role": "system", "content": SIGNAL_REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])
        raw = (raw or "").strip()

        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        data = json.loads(raw)
        items = data.get("items", []) if isinstance(data, dict) else []
        if isinstance(items, list):
            result = []
            for it in items:
                result.append({
                    "timestamp": it.get("timestamp", ""),
                    "from": it.get("from", ""),
                    "signals": it.get("signals", ["other"]),
                })
            return result
    except Exception as e:
        try:
            print(f"[search_memory] LLM 信号复核失败，回退规则结果: {e}")
        except Exception:
            pass

    return tagged


# ============================================================
# 事件切分
# ============================================================

def _group_into_episodes(entries: List[Dict], gap_seconds: int = 300) -> List[Dict]:
    """
    按时间间隔把条目聚成“轮次/事件”。间隔超过 gap_seconds 视为新事件。
    """
    if not entries:
        return []

    def _ts(s):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    episodes = []
    current = {"start": entries[0].get("timestamp", ""), "items": [entries[0]]}
    prev_t = _ts(entries[0].get("timestamp", ""))

    for e in entries[1:]:
        t = _ts(e.get("timestamp", ""))
        if prev_t and t and (t - prev_t).total_seconds() > gap_seconds:
            episodes.append(current)
            current = {"start": e.get("timestamp", ""), "items": [e]}
        else:
            current["items"].append(e)
        prev_t = t or prev_t

    episodes.append(current)

    for ep in episodes:
        sigs = set()
        for it in ep["items"]:
            for s in it.get("signals", []):
                sigs.add(s)
        ep["signals"] = sorted(sigs)
        ep["is_flagged"] = bool(sigs)

    return episodes


def _filter_by_focus(episodes: List[Dict], focus: str) -> List[Dict]:
    """
    focus 为枚举，不传则全保留。
    传了 error/correction/recursion 时，只保留对应信号的事件。
    """
    if not focus:
        return episodes
    focus = focus.strip().lower()
    if focus in ("", "window", "all"):
        return episodes
    return [ep for ep in episodes if focus in ep.get("signals", [])]


# ============================================================
# 反思：读经验背包相关片段
# ============================================================

def _get_backpack_file(session_id: str) -> Path:
    EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)
    return EXPERIENCE_DIR / f"{session_id}_backpack.txt"


def _load_backpack(session_id: str) -> str:
    file_path = _get_backpack_file(session_id)
    if file_path.exists():
        return file_path.read_text(encoding="utf-8").strip()
    return ""


def _save_backpack(session_id: str, content: str):
    file_path = _get_backpack_file(session_id)
    file_path.write_text(content, encoding="utf-8")


def _backup_backpack(session_id: str, content: str) -> str:
    if not content:
        return ""
    history_dir = EXPERIENCE_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    revision = f"{session_id}_{ts}"
    (history_dir / f"{revision}.txt").write_text(content, encoding="utf-8")
    return revision


def _extract_relevant_backpack(backpack: str, keywords: List[str], max_lines: int = 60) -> str:
    if not backpack:
        return ""
    kws = [kw.strip().lower() for kw in keywords if kw and kw.strip()]
    if not kws:
        lines = backpack.split("\n")
        return "\n".join(lines[:max_lines])

    lines = backpack.split("\n")
    hit_idx = set()
    for i, line in enumerate(lines):
        low = line.lower()
        if any(kw in low for kw in kws):
            for j in (i - 1, i, i + 1):
                if 0 <= j < len(lines):
                    hit_idx.add(j)

    if not hit_idx:
        return ""
    ordered = sorted(hit_idx)
    return "\n".join([lines[i] for i in ordered[:max_lines]])


# ============================================================
# 反思：生成（全量事件 + 异常标注 + 沉淀自判断）
# ============================================================

REFLECT_SYSTEM_PROMPT = """你是决策复盘器。你的唯一职责：回看一段 agent 的执行记录（已按事件切分），并对照当前经验背包，产出复盘结果，并判断本次是否值得沉淀。

## 输入

1. 【执行事件】：本次窗口内的事件（可能包含多轮、多任务）。
   - `is_flagged: true` 的事件命中了异常信号，重点复盘。
   - `is_flagged: false` 的事件是正常流程，作为上下文，也可从中提炼正向经验。
2. 【经验背包（相关片段）】：当前背包里与本次记录相关的规则。

## 处理要点

- 每个事件单独复盘，不要把不同事件混成一条。
- 跨事件反复出现的同一个坑，合并成一条，不要按事件重复输出。
- 异常事件优先；正常流程里若有明显可固化做法或用户偏好，也值得提炼。
- 只输出真正值得长期保留的条目，宁少勿滥。

## 关于「可固化做法」的限定

「可固化做法」指的是：以后遇到同类任务时可以直接复用的操作规则，不是本次具体怎么配了某个任务。
- 算：某类任务走固定三步、某类场景默认前台运行、某类查询优先查契约。
- 不算：这次给某插件配了定时任务、这次把 receiver 改成了谁。

只描述“这次做了什么”的，不算可固化做法。

## 输出：三部分，缺一不可

### A. 新发现
记录里出现、但背包里没有对应规则的「坑」「可优化点」或「可固化做法」。

### B. 旧规则复查
背包里已有的、和本次记录相关的规则，逐条判断它在本次执行中的情况：
- 仍然有效：这次遵守了，且没出问题。
- 已过时：这次遵守了，但仍出问题；或场景已不复存在。
- 需修正：规则方向对，但触发场景/应对方式描述不准，需改。

### C. 沉淀判断
本次复盘有没有真正值得写进长期背包的内容？
- 有：输出 SETTLE: YES
- 没有（只是复述现象、没有可执行应对、或全是“仍然有效”无需改动）：输出 SETTLE: NO

## 硬性规则

1. 只看记录和背包里真实存在的内容，不臆测、不补充。
2. 每条结果必须锚定具体位置：第几步 / 调了什么工具 / 报了什么错；或背包里的哪一条规则。
3. 语言具体可执行，不要空话（如「要更小心」）。
4. 新发现最多 5 条，旧规则复查最多 5 条。事件越多，越要合并同类。
5. 没有内容就写 NONE，不要硬凑。
6. 如果整体无值得沉淀的东西，只输出：
NO_EXPERIENCE

## 输出格式（严格照此）

【新发现】
- [坑/优化] <描述> | 触发场景：<...> | 应对：<...>
（无则写 NONE）

【旧规则复查】
- [仍然有效/已过时/需修正] <引用背包规则的关键句> | 本次情况：<...> | 建议：<...>
（无则写 NONE）

SETTLE: YES/NO

只输出上述内容，不要解释、前言、后记。"""


def _parse_settle(reflection: str) -> bool:
    """
    从复盘输出里解析 SETTLE: YES/NO。
    解析不到时保守返回 True（宁可沉淀，不丢内容）。
    """
    if not reflection:
        return False
    m = re.search(r"SETTLE\s*:\s*(YES|NO)", reflection, re.IGNORECASE)
    if not m:
        return True
    return m.group(1).upper() == "YES"


def _strip_settle_line(reflection: str) -> str:
    """把 SETTLE 行从正文里去掉，避免落盘/归并时带上控制标记。"""
    if not reflection:
        return reflection
    lines = reflection.split("\n")
    kept = [ln for ln in lines if not re.match(r"^\s*SETTLE\s*:", ln, re.IGNORECASE)]
    return "\n".join(kept).strip()


async def _generate_reflection(agent, episodes: List[Dict], backpack_snippet: str) -> str:
    if not episodes:
        return "NO_EXPERIENCE"

    payload = []
    for ep in episodes:
        payload.append({
            "start": ep.get("start", ""),
            "is_flagged": ep.get("is_flagged", False),
            "signals": ep.get("signals", []),
            "items": ep.get("items", []),
        })

    items_json = json.dumps(payload, ensure_ascii=False, indent=2)

    prompt = f"""【执行事件】（窗口内事件，按时间排序）：

{items_json}

【经验背包（相关片段）】：

{backpack_snippet if backpack_snippet else "（无相关片段）"}

请按规则复盘，输出【新发现】【旧规则复查】和 SETTLE 判断。"""

    try:
        raw = await agent.llm.chat([
            {"role": "system", "content": REFLECT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])
        return (raw or "").strip() or "NO_EXPERIENCE"
    except Exception as e:
        return f"REFLECT_FAILED: {e}"


# ============================================================
# 反思 → 背包：归并
# ============================================================

MERGE_SYSTEM_PROMPT = """你是经验背包的归并器（Experience Backpack Merger）。

你的职责：把本次复盘产出的经验，安全地归并进当前背包。

规则：
1. 以【当前背包】为基础，把【本次复盘】里真正值得长期保留的内容合并进去。
2. 只合并「具体、可执行、带触发场景和应对」的条目；只是描述现象、没有应对的，丢弃。
3. 保留当前背包里未被触及的内容，不要因为本次复盘就删掉旧条目。
4. 旧规则复查里标记为「需修正」的，按建议修正原条目；标记为「已过时」的，删除或标注废弃。
5. 「仍然有效」的，保持原样。
6. 新发现与背包已有条目语义重复的，去重，保留更清晰、更完整的版本。
7. 保持原有格式风格（段落、标题、编号），不要强行重排结构。
8. 不要输出任何解释、前言、后记。

只输出归并后的完整背包正文。"""


async def _merge_reflection_into_backpack(agent, current: str, reflection: str) -> tuple[str, str]:
    prompt = f"""{MERGE_SYSTEM_PROMPT}

【当前背包】
{current if current else "（空）"}

【本次复盘】
{reflection}"""

    try:
        merged = await agent.llm.chat([{"role": "user", "content": prompt}])
        merged = (merged or "").strip()
        if not merged:
            raise ValueError("归并结果为空")
        return merged, "已归并"
    except Exception as e:
        try:
            print(f"[search_memory.reflect] 归并失败，回退保守合并: {e}")
        except Exception:
            pass
        if current and reflection:
            return current + "\n\n" + reflection, "归并失败，已保守追加"
        return (current or reflection), "归并失败，保留原内容或直接写入"


# ============================================================
# 反思：落盘 + 去重
# ============================================================

def _is_duplicate_reflection(session_id: str, after_ts: str, recent: int = 5) -> Optional[str]:
    if not REFLECTION_DIR.exists():
        return None

    # after_ts 非法时不做查重，避免垃圾值导致永不命中
    if not _is_valid_ts(after_ts):
        return None

    files = sorted(
        REFLECTION_DIR.glob(f"{session_id}_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:recent]

    if not files:
        return None

    for fp in files:
        try:
            head = fp.read_text(encoding="utf-8")[:800]
        except Exception:
            continue
        meta = _parse_reflection_header(head)
        if meta.get("after_ts", "") == after_ts:
            return str(fp)

    return None


def _save_reflection(session_id: str, content: str, meta: Dict) -> str:
    REFLECTION_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    file_path = REFLECTION_DIR / f"{session_id}_{ts}.md"

    header = (
        f"# 反思 {ts}\n"
        f"- session: {session_id}\n"
        f"- after_ts: {meta.get('after_ts', '')}\n"
        f"- before_ts: {meta.get('before_ts', '')}\n"
        f"- window_end: {meta.get('window_end', '')}\n"
        f"- focus: {meta.get('focus', '')}\n"
        f"- episodes: {meta.get('episode_count', 0)}\n"
        f"- flagged: {meta.get('flagged_count', 0)}\n"
        f"- signals: {','.join(meta.get('signals', []))}\n"
        f"- settle: {meta.get('settle', '')}\n"
        f"- window: {meta.get('after_ts', '')} ~ {meta.get('before_ts', '') or 'now'}\n\n"
    )
    file_path.write_text(header + content, encoding="utf-8")
    return str(file_path)


# ============================================================
# 主入口
# ============================================================

async def execute(envelop, agent):
    payload = envelop.payload
    action = payload.get("action", "get")

    session_id = payload.get("session_id")
    if not session_id:
        envelop.payload = {"ok": False, "error": "缺少 session_id"}
        return envelop

    date_from = payload.get("date_from")
    date_to = payload.get("date_to")
    today = datetime.now().strftime("%Y-%m-%d")
    default_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    date_from = date_from or default_from
    date_to = date_to or today

    keywords = payload.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [keywords]

    # ============================================================
    # action: reflect —— 全量事件复盘 + 异常标注 + 自判断沉淀
    # ============================================================
    if action == "reflect":
        after_ts = payload.get("after_ts", "")
        before_ts = payload.get("before_ts", "")
        focus = payload.get("focus", "").strip().lower()

        # 自动窗口：after_ts 不传或非法时，接续上次实际跑到的终点
        if not _is_valid_ts(after_ts):
            after_ts = _resolve_auto_after_ts(session_id, default_hours=12)

        # 本次窗口实际终点：手动传了 before_ts 就用它，否则用当前时刻
        window_end = before_ts if before_ts else datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        # 1. 窗口内全量 flow
        entries, total = _get_entries_in_window(
            session_id, after_ts, before_ts,
            date_from=date_from, date_to=date_to,
            limit=400,
        )

        if not entries:
            envelop.payload = {
                "ok": True,
                "answer": "NO_EXPERIENCE",
                "matched_count": 0,
                "total_in_range": total,
                "window": f"{after_ts} ~ {before_ts or 'now'}",
                "saved": False,
                "merged": False,
                "message": "窗口内无可复盘记录",
            }
            return envelop

        # 2. 落盘前查重（同窗口只反思一次）
        existing = _is_duplicate_reflection(session_id, after_ts)
        if existing:
            envelop.payload = {
                "ok": True,
                "answer": "",
                "matched_count": len(entries),
                "total_in_range": total,
                "window": f"{after_ts} ~ {before_ts or 'now'}",
                "saved": False,
                "skipped": True,
                "skip_reason": "该窗口已反思过",
                "existing_reflection": existing,
                "merged": False,
            }
            return envelop

        # 3. 全量事件切分（不先筛信号）
        base_entries = [
            {"timestamp": e.get("timestamp", ""), "from": e.get("from", ""),
             "content": e.get("content", "")}
            for e in entries
        ]
        all_episodes = _group_into_episodes(base_entries)

        # 4. 信号识别只用于标注重点
        tagged = await _detect_signals_llm(agent, entries)
        signal_ts = {t.get("timestamp") for t in tagged if t.get("timestamp")}

        for ep in all_episodes:
            ep_sigs = set()
            for it in ep["items"]:
                if it.get("timestamp") in signal_ts:
                    for t in tagged:
                        if t.get("timestamp") == it.get("timestamp"):
                            for s in t.get("signals", []):
                                ep_sigs.add(s)
            ep["signals"] = sorted(ep_sigs)
            ep["is_flagged"] = bool(ep_sigs)

        # 5. focus 枚举：传了就只留对应信号事件；不传则全量
        episodes = _filter_by_focus(all_episodes, focus)

        if not episodes:
            envelop.payload = {
                "ok": True,
                "answer": "NO_EXPERIENCE",
                "matched_count": 0,
                "total_in_range": total,
                "window": f"{after_ts} ~ {before_ts or 'now'}",
                "saved": False,
                "merged": False,
                "message": "窗口内无满足 focus 的事件",
            }
            return envelop

        # 6. 背包相关片段
        backpack = _load_backpack(session_id)
        if keywords:
            backpack_snippet = _extract_relevant_backpack(backpack, keywords)
        else:
            backpack_snippet = backpack

        # 7. 复盘（含 SETTLE 判断）
        reflection_raw = await _generate_reflection(agent, episodes, backpack_snippet)

        if (not reflection_raw
                or reflection_raw.startswith("NO_EXPERIENCE")
                or reflection_raw.startswith("REFLECT_FAILED")):
            envelop.payload = {
                "ok": True,
                "answer": reflection_raw,
                "matched_count": len(entries),
                "total_in_range": total,
                "window": f"{after_ts} ~ {before_ts or 'now'}",
                "saved": False,
                "merged": False,
            }
            return envelop

        # 8. 解析沉淀判断，并从正文里去掉 SETTLE 行
        should_settle = _parse_settle(reflection_raw)
        reflection = _strip_settle_line(reflection_raw)

        signals = sorted({s for ep in episodes for s in ep.get("signals", [])})
        flagged_count = sum(1 for ep in episodes if ep.get("is_flagged"))

        # 9. 落盘（无论是否沉淀，反思都留档；记录 window_end 供下次接续）
        file_path = _save_reflection(session_id, reflection, {
            "after_ts": after_ts,
            "before_ts": before_ts,
            "window_end": window_end,
            "focus": focus,
            "episode_count": len(episodes),
            "flagged_count": flagged_count,
            "signals": signals,
            "settle": "YES" if should_settle else "NO",
        })

        # 10. 只有在 SETTLE: YES 时才归并进背包
        if not should_settle:
            envelop.payload = {
                "ok": True,
                "answer": reflection,
                "matched_count": len(entries),
                "total_in_range": total,
                "window": f"{after_ts} ~ {before_ts or 'now'}",
                "episode_count": len(episodes),
                "flagged_count": flagged_count,
                "signals": signals,
                "focus": focus,
                "saved": True,
                "reflection_file": file_path,
                "settled": False,
                "merged": False,
                "merge_note": "复盘判定本次无需沉淀，仅落盘反思",
                "backpack_revision": "",
                "backpack_size": len(backpack) if backpack else 0,
            }
            return envelop

        # 11. 归并进背包
        merged_ok = False
        merge_note = ""
        revision = ""

        if backpack:
            revision = _backup_backpack(session_id, backpack)

        new_backpack, merge_note = await _merge_reflection_into_backpack(
            agent, backpack, reflection
        )

        try:
            _save_backpack(session_id, new_backpack)
            merged_ok = True
        except Exception as e:
            merge_note = f"背包落盘失败: {e}"
            merged_ok = False

        envelop.payload = {
            "ok": True,
            "answer": reflection,
            "matched_count": len(entries),
            "total_in_range": total,
            "window": f"{after_ts} ~ {before_ts or 'now'}",
            "episode_count": len(episodes),
            "flagged_count": flagged_count,
            "signals": signals,
            "focus": focus,
            "saved": True,
            "reflection_file": file_path,
            "settled": True,
            "merged": merged_ok,
            "merge_note": merge_note,
            "backpack_revision": revision,
            "backpack_size": len(new_backpack) if merged_ok else 0,
        }
        return envelop

    # ============================================================
    # action: get —— 原功能，回答问题（完全不变）
    # ============================================================
    query = payload.get("query")
    if not query:
        envelop.payload = {"ok": False, "error": "缺少 query 参数"}
        return envelop

    if not keywords:
        envelop.payload = {"ok": False, "error": "缺少 keywords 参数"}
        return envelop

    entries, total = _search_entries_with_context(
        session_id, keywords, date_from, date_to, limit=50
    )

    answer = await _generate_answer(agent, query, entries, date_from, date_to)

    envelop.payload = {
        "ok": True,
        "answer": answer,
        "matched_count": len(entries),
        "total_in_range": total,
        "range": f"{date_from} ~ {date_to}",
        "keywords": keywords,
    }
    return envelop


def help() -> dict:
    return {
        "route": "builtins/agents/search_memory",
        "description": "搜索历史记忆（get）或全量事件复盘并沉淀经验（reflect）",
        "input": {
            "action": "get（默认）| reflect",
            "session_id": "会话ID（必填）",
            "query": "get 模式：用户想问的问题",
            "after_ts": "reflect 模式：窗口起点（可选，不传或非法则自动接续上次窗口终点）",
            "before_ts": "reflect 模式：窗口终点（可选）",
            "focus": "reflect 模式：可选枚举（error/correction/recursion/window），不传则全量复盘",
            "keywords": "get 必填；reflect 可选（用于裁剪背包相关片段）",
            "date_from": "开始日期 YYYY-MM-DD（可选）",
            "date_to": "结束日期 YYYY-MM-DD（可选）",
        },
        "output": {
            "ok": "是否成功",
            "answer": "get：回答；reflect：反思内容",
            "window": "reflect：复盘窗口",
            "episode_count": "reflect：事件数",
            "flagged_count": "reflect：命中异常信号的事件数",
            "signals": "reflect：命中信号类型",
            "saved": "reflect：反思是否落盘",
            "skipped": "reflect：是否因同窗口已反思而跳过",
            "settled": "reflect：本次是否判定需要沉淀",
            "reflection_file": "reflect：反思文件路径",
            "merged": "reflect：是否已归并进经验背包",
            "merge_note": "reflect：归并说明",
            "backpack_revision": "reflect：归并前备份 revision",
            "backpack_size": "reflect：归并后背包大小",
        }
    }