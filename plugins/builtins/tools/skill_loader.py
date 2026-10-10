"""
skill_loader — 技能加载器（搜索 + 加载 + 管理）

与 Java 版 os/skill_loader.java 对齐。

action：
- scan：扫 data/skills/，建索引
- list：列技能（自动 scan）
- search：搜技能（自动 scan + 粗筛 + LLM 精排）
- detail：查详情
- categories：列分类
- stats：统计
- load：加载技能到 active_skills（支持模糊匹配）
- clear：清空当前技能
- get_active：查看当前技能
- save_active：保存当前技能

用法：
    from .skill_loader import execute, help
    # 或在插件系统里自动注册
"""

import json
import re
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import core
from runtime._llm import is_llm_error_string


# ============================================================
# 常量
# ============================================================

DATA_DIR = Path("data/skill_loader")
SKILLS_DIR = Path("data/skills")
INDEX_FILE = DATA_DIR / "index.json"

ACTIVE_SKILL_DIR = Path("data/memories/main_agent/active_skills")

# ============================================================
# 黑名单关键词
# ============================================================

BLOCKLIST_KEYWORDS = {
    "godmode", "jailbreak", "uncensoring", "uncensor", "red-teaming",
    "redteam", "safety-bypass", "bypass-safety", "refusal-removal",
    "abliteration", "guardrail-removal", "remove-guardrails", "excision",
    "model-surgery", "g0dm0d3", "obliterator", "obliteratus", "uncensored",
    "bypass", "越狱", "绕过", "去审查", "红队",
}

# ============================================================
# 分类映射
# ============================================================

CATEGORY_MAP: Dict[str, str] = {
    "debugging": "software-development",
    "testing": "software-development",
    "tdd": "software-development",
    "code-review": "software-development",
    "quality": "software-development",
    "development": "software-development",
    "planning": "software-development",
    "implementation": "software-development",
    "workflow": "software-development",
    "documentation": "software-development",
    "subagent": "software-development",
    "delegation": "software-development",
    "parallel": "software-development",
    "mlops": "mlops",
    "fine-tuning": "mlops",
    "training": "mlops",
    "evaluation": "mlops",
    "benchmarking": "mlops",
    "inference": "mlops",
    "huggingface": "mlops",
    "peft": "mlops",
    "lora": "mlops",
    "qlora": "mlops",
    "trl": "mlops",
    "rlhf": "mlops",
    "design": "design",
    "ui": "design",
    "ux": "design",
    "brand": "design",
    "visual": "design",
    "inclusive": "design",
    "github": "devops",
    "git": "devops",
    "devops": "devops",
    "webhook": "devops",
    "sync": "devops",
    "backup": "devops",
    "deployment": "devops",
    "creative": "creative",
    "generative-art": "creative",
    "p5js": "creative",
    "creative-coding": "creative",
    "interactive": "creative",
    "visualization": "creative",
    "canvas": "creative",
    "shaders": "creative",
    "animation": "creative",
    "ascii": "creative",
    "legal": "legal",
    "contract": "legal",
    "policy": "legal",
    "compliance": "legal",
    "gaming": "gaming",
    "pokemon": "gaming",
    "emulator": "gaming",
    "gameplay": "gaming",
    "hermes": "hermes",
    "autonomous": "autonomous-ai",
    "agent": "autonomous-ai",
    "multi-agent": "autonomous-ai",
    "spawning": "autonomous-ai",
    "gateway": "autonomous-ai",
    "unity": "unity",
    "blender": "blender",
    "paid-media": "paid-media",
    "tracking": "paid-media",
    "attribution": "paid-media",
    "engineering": "engineering",
    "media": "media",
    "audio": "media",
    "spectrogram": "media",
    "research": "research",
    "productivity": "productivity",
    "google": "productivity",
    "gmail": "productivity",
    "calendar": "productivity",
    "drive": "productivity",
    "sheets": "productivity",
    "apple": "apple",
    "imessage": "apple",
    "macos": "apple",
}


# ============================================================
# 索引读写
# ============================================================

def _load_index() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_FILE.exists():
        return _empty_index()
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _empty_index()


def _empty_index() -> Dict[str, Any]:
    return {
        "skills": [],
        "categories": [],
        "last_scan": None,
        "skipped": [],
    }


def _save_index(data: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============================================================
# 黑名单 / 分类
# ============================================================

def _is_blocked_skill(skill: Dict[str, Any]) -> bool:
    title = str(skill.get("title", "")).lower()
    desc = str(skill.get("description", "")).lower()
    tags = " ".join(skill.get("tags", [])).lower()
    combined = f"{title} {desc} {tags}"
    for kw in BLOCKLIST_KEYWORDS:
        if kw in combined:
            return True
    return False


def _determine_category_from_tags(tags: List[str], file_path: str) -> str:
    for tag in tags:
        t = tag.lower()
        if t in CATEGORY_MAP:
            return CATEGORY_MAP[t]

    parts = file_path.split("/")
    if len(parts) >= 2:
        dir_name = parts[0]
        known_cats = set(CATEGORY_MAP.values())
        if dir_name in known_cats:
            return dir_name
        for key, cat in CATEGORY_MAP.items():
            if key in dir_name.lower():
                return cat
        return dir_name

    return "未分类"


# ============================================================
# 解析 SKILL.md
# ============================================================

def _strip_quotes(s: str) -> str:
    if len(s) >= 2:
        if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
            return s[1:-1]
    return s


def _parse_skill_md(file_path: Path) -> Optional[Dict[str, Any]]:
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return None

    lines = content.split("\n")
    title = ""
    description = ""
    category = "未分类"
    tags: List[str] = []
    fm_end = 0

    # 解析 frontmatter
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                fm_end = i + 1
                break
            line = lines[i].strip()
            if line.startswith("title:"):
                title = _strip_quotes(line[6:].strip())
            elif line.startswith("description:"):
                description = _strip_quotes(line[12:].strip())
            elif line.startswith("category:"):
                category = _strip_quotes(line[9:].strip())
            elif line.startswith("tags:"):
                tag_str = line[5:].strip()
                if tag_str.startswith("[") and tag_str.endswith("]"):
                    inner = tag_str[1:-1]
                    for t in inner.split(","):
                        cleaned = _strip_quotes(t.strip())
                        if cleaned:
                            tags.append(cleaned)

    # 从正文提取 title
    if not title:
        for i in range(fm_end, len(lines)):
            if lines[i].startswith("# "):
                title = lines[i][2:].strip()
                break
    if not title:
        title = file_path.stem

    # 从正文提取 description
    if not description:
        for i in range(fm_end, len(lines)):
            line = lines[i].strip()
            if line and not line.startswith("#"):
                description = line[:200]
                break

    # 相对路径 + skill_id
    try:
        rel = file_path.relative_to(SKILLS_DIR)
    except ValueError:
        rel = file_path
    rel_path = str(rel).replace("\\", "/")

    skill_id = re.sub(r"(?i)/SKILL\.md$", "", rel_path)
    skill_id = re.sub(r"(?i)^SKILL\.md$", "", skill_id)
    skill_id = skill_id.replace("/", "_").lower()
    skill_id = re.sub(r"[^a-z0-9_]+", "_", skill_id)
    if not skill_id:
        skill_id = file_path.stem.lower() or "unknown"

    # 推断 category
    if category == "未分类" or not category:
        category = _determine_category_from_tags(tags, rel_path)

    return {
        "skill_id": skill_id,
        "title": title,
        "description": description,
        "category": category,
        "tags": tags,
        "file_path": rel_path,
        "skill_dir": str(file_path.parent),
        "content": content,
    }


# ============================================================
# 扫描技能
# ============================================================

def _walk_skills(dir_path: Path, out: List[Path]) -> None:
    if not dir_path.exists():
        return
    try:
        for entry in dir_path.iterdir():
            if entry.is_dir():
                _walk_skills(entry, out)
            elif entry.name == "SKILL.md":
                out.append(entry)
    except Exception:
        pass


def _scan_skills() -> Dict[str, Any]:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    md_files: List[Path] = []
    _walk_skills(SKILLS_DIR, md_files)

    skills: List[Dict[str, Any]] = []
    categories_set = set()
    blocked_titles: List[str] = []

    for f in md_files:
        skill = _parse_skill_md(f)
        if skill is None:
            continue
        if _is_blocked_skill(skill):
            blocked_titles.append(skill.get("title", str(f)))
            continue
        skills.append(skill)
        categories_set.add(skill["category"])

    categories = sorted(categories_set)

    data = {
        "skills": skills,
        "categories": categories,
        "last_scan": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "skipped": blocked_titles,
    }
    _save_index(data)
    return data


# ============================================================
# 模糊匹配 + 搜索
# ============================================================

def _fuzzy_match(query: str, text: str, threshold: float = 0.3) -> float:
    q = query.lower()
    t = text.lower()
    if not q or not t:
        return 0.0
    if q in t:
        return 1.0

    max_len = 0
    for i in range(len(q)):
        for j in range(i + 1, len(q) + 1):
            sub = q[i:j]
            if sub in t:
                max_len = max(max_len, len(sub))
    ratio = (2.0 * max_len) / (len(q) + len(t))
    return ratio if ratio >= threshold else 0.0


def _search_skills(
    index: Dict[str, Any],
    keywords: List[str],
    category: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    if not keywords:
        return []

    query = " ".join(keywords).strip()
    if not query:
        return []

    results: List[Dict[str, Any]] = []
    for skill in index.get("skills", []):
        if category and skill.get("category") != category:
            continue

        title_score = _fuzzy_match(query, skill.get("title", ""))
        desc_score = _fuzzy_match(query, skill.get("description", "")) * 0.8
        tag_scores = [
            _fuzzy_match(query, t) * 0.7
            for t in skill.get("tags", [])
        ]
        tag_max = max(tag_scores) if tag_scores else 0.0
        content_score = _fuzzy_match(query, skill.get("content", "")[:2000]) * 0.5

        score = max(title_score, desc_score, tag_max, content_score)
        if score <= 0:
            continue

        results.append({
            "skill_id": skill.get("skill_id"),
            "title": skill.get("title"),
            "description": skill.get("description"),
            "category": skill.get("category"),
            "tags": skill.get("tags"),
            "file_path": skill.get("file_path"),
            "skill_dir": skill.get("skill_dir", ""),
            "score": round(score, 3),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


# ============================================================
# LLM 精排
# ============================================================

def _extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """从 LLM 回复里抠出第一个完整 JSON 对象"""
    if not raw:
        return None
    start = raw.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    end = -1

    for i in range(start, len(raw)):
        ch = raw[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end == -1:
        return None

    try:
        return json.loads(raw[start:end + 1])
    except Exception:
        return None


async def _llm_rank(
    agent,
    query: str,
    keywords: List[str],
    candidates: List[Dict[str, Any]],
    top_k: int = 10,
) -> Dict[str, Any]:
    briefs = []
    for s in candidates:
        desc = str(s.get("description", ""))[:200]
        briefs.append({
            "skill_id": s.get("skill_id"),
            "title": s.get("title"),
            "description": desc,
            "tags": list(s.get("tags", []))[:5],
        })

    system_prompt = (
        "你是一个技能推荐专家。用户提出问题并给出关键词，你需要从候选技能中选出最匹配的。\n"
        "候选技能已经经过初步关键词筛选，你只需要做语义精排。\n"
        '返回JSON格式：{"skills": [{"skill_id": "xxx", "reason": "推荐理由"}]}\n'
        "只返回最相关的 top_k 个，按相关度从高到低排序。"
    )

    user_prompt = (
        f"用户问题：{query}\n"
        f"关键词：{', '.join(keywords)}\n"
        f"返回数量：{top_k}\n"
        f"候选技能（共{len(briefs)}个）：\n"
        + json.dumps(briefs, ensure_ascii=False)
    )

    try:
        if not agent.llm:
            raise RuntimeError("LLM 未配置")

        raw = await agent.llm.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])

        parsed = _extract_json_object(raw)
        ranked = parsed.get("skills", []) if isinstance(parsed, dict) else []

        out: List[Dict[str, Any]] = []
        for item in ranked[:top_k]:
            sid = item.get("skill_id")
            for c in candidates:
                if c.get("skill_id") == sid:
                    enriched = dict(c)
                    enriched["reason"] = item.get("reason")
                    out.append(enriched)
                    break

        return {"skills": out, "total": len(out)}
    except Exception as e:
        fallback = candidates[:top_k]
        out = [
            {"skill_id": s.get("skill_id"), "reason": "关键词匹配（LLM降级）"}
            for s in fallback
        ]
        return {"skills": out, "total": len(out), "error": str(e)}


# ============================================================
# 活跃技能读写
# ============================================================

def _active_skill_file(session_id: str) -> Path:
    return ACTIVE_SKILL_DIR / f"{session_id}_skill.txt"


def _load_active_skill(session_id: str) -> str:
    f = _active_skill_file(session_id)
    if not f.exists():
        return ""
    try:
        return f.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _save_active_skill(session_id: str, content: str) -> None:
    ACTIVE_SKILL_DIR.mkdir(parents=True, exist_ok=True)
    _active_skill_file(session_id).write_text(content, encoding="utf-8")


def _clear_active_skill(session_id: str) -> None:
    f = _active_skill_file(session_id)
    if f.exists():
        try:
            f.unlink()
        except Exception:
            pass


# ============================================================
# 主入口
# ============================================================

async def execute(envelop, agent) -> Any:
    payload = envelop.payload or {}
    meta = envelop.meta or {}

    # 支持嵌套 params
    params = payload.get("params") if isinstance(payload.get("params"), dict) else None
    if not params:
        params = payload

    action = str(payload.get("action", "list"))

    session_id = str(meta.get("session_id", ""))
    if not session_id:
        session_id = str(params.get("session_id", ""))
    if not session_id:
        session_id = "default"

    # ============ 搜索类 action ============

    if action == "scan":
        data = _scan_skills()
        out = {
            "total": len(data.get("skills", [])),
            "categories": data.get("categories"),
            "last_scan": data.get("last_scan"),
            "skipped": data.get("skipped", []),
        }
        envelop.payload = {"ok": True, "data": out}
        return envelop

    if action == "list":
        _scan_skills()
        fresh_index = _load_index()

        category = str(params.get("category", ""))
        limit = int(params.get("limit", 100) or 100)

        skills: List[Dict[str, Any]] = []
        for s in fresh_index.get("skills", []):
            if category and s.get("category") != category:
                continue
            skills.append({
                "skill_id": s.get("skill_id"),
                "title": s.get("title"),
                "description": s.get("description"),
                "category": s.get("category"),
                "tags": s.get("tags"),
                "file_path": s.get("file_path"),
                "skill_dir": s.get("skill_dir", ""),
            })
            if len(skills) >= limit:
                break

        envelop.payload = {
            "ok": True,
            "data": {
                "skills": skills,
                "total": len(skills),
                "categories": fresh_index.get("categories"),
                "last_scan": fresh_index.get("last_scan"),
            },
        }
        return envelop

    if action == "search":
        _scan_skills()
        fresh_index = _load_index()

        query = str(params.get("query", "")).strip()
        keywords_raw = params.get("keywords", [])
        keywords: List[str] = []
        if isinstance(keywords_raw, list):
            keywords = [str(k) for k in keywords_raw]
        elif isinstance(keywords_raw, str) and keywords_raw.strip():
            keywords = [keywords_raw.strip()]

        if not query:
            envelop.payload = {"ok": False, "error": "query 不能为空"}
            return envelop

        # 兜底：keywords 为空时用 query 代替
        if not keywords and query:
            keywords = [w for w in query.split() if w] or [query]

        if not keywords:
            envelop.payload = {"ok": False, "error": "keywords 不能为空"}
            return envelop

        category = params.get("category")
        limit = int(params.get("limit", 10) or 10)

        candidates = _search_skills(fresh_index, keywords, category, 30)

        if not candidates:
            envelop.payload = {
                "ok": True,
                "data": {
                    "skills": [],
                    "total": 0,
                    "query": query,
                    "keywords": keywords,
                },
            }
            return envelop

        rank_result = await _llm_rank(agent, query, keywords, candidates, limit)
        final_skills = rank_result.get("skills", [])

        data = {
            "skills": final_skills,
            "total": len(final_skills),
            "query": query,
            "keywords": keywords,
        }
        if "error" in rank_result:
            data["fallback"] = True
            data["error"] = rank_result["error"]

        envelop.payload = {"ok": True, "data": data}
        return envelop

    # ============ 其余 action 读 index ============

    index = _load_index()

    if action == "detail":
        skill_id = str(params.get("skill_id", ""))
        for s in index.get("skills", []):
            if s.get("skill_id") == skill_id:
                envelop.payload = {"ok": True, "data": s}
                return envelop
        envelop.payload = {"ok": False, "error": "技能不存在"}
        return envelop

    if action == "categories":
        cats = index.get("categories", [])
        envelop.payload = {
            "ok": True,
            "data": {
                "categories": cats,
                "total": len(cats),
            },
        }
        return envelop

    if action == "stats":
        category_count: Dict[str, int] = {}
        for s in index.get("skills", []):
            cat = s.get("category", "")
            category_count[cat] = category_count.get(cat, 0) + 1

        envelop.payload = {
            "ok": True,
            "data": {
                "total_skills": len(index.get("skills", [])),
                "total_categories": len(index.get("categories", [])),
                "last_scan": index.get("last_scan"),
                "skipped": index.get("skipped", []),
                "category_count": category_count,
            },
        }
        return envelop

    # ============ 加载类 action ============

    if action == "get_active":
        content = _load_active_skill(session_id)
        envelop.payload = {
            "ok": True,
            "session_id": session_id,
            "content": content,
            "size": len(content),
            "has_skill": bool(content),
        }
        return envelop

    if action == "save_active":
        content = payload.get("content") or params.get("content") or ""
        _save_active_skill(session_id, content)
        envelop.payload = {
            "ok": True,
            "message": f"✅ 已保存，{len(content)} 字",
            "size": len(content),
        }
        return envelop

    if action == "clear":
        _clear_active_skill(session_id)
        envelop.payload = {"ok": True, "message": "✅ 技能已卸载", "mode": "clear"}
        return envelop

    if action == "load":
        skill_id = str(params.get("skill_id", ""))
        mode = str(params.get("mode", "load"))

        if mode == "clear":
            _clear_active_skill(session_id)
            envelop.payload = {"ok": True, "message": "✅ 技能已卸载", "mode": "clear"}
            return envelop

        if not skill_id:
            envelop.payload = {"ok": False, "error": "缺少 skill_id 参数"}
            return envelop

        skill = None

        # 精确匹配
        for s in index.get("skills", []):
            if s.get("skill_id") == skill_id:
                skill = s
                break

        # 模糊匹配
        if skill is None:
            lower = skill_id.lower()
            candidates = []
            for s in index.get("skills", []):
                sid = str(s.get("skill_id", "")).lower()
                sdir = str(s.get("skill_dir", "")).lower()
                fpath = str(s.get("file_path", "")).lower()
                if lower in sid or lower in sdir or lower in fpath:
                    candidates.append(s)

            if len(candidates) == 1:
                skill = candidates[0]
            elif len(candidates) > 1:
                ids = [c.get("skill_id") for c in candidates]
                envelop.payload = {
                    "ok": False,
                    "error": f'skill_id "{skill_id}" 匹配到多个技能，请用精确 ID',
                    "candidates": ids,
                }
                return envelop

        if skill is None:
            envelop.payload = {"ok": False, "error": f"技能不存在: {skill_id}"}
            return envelop

        content = skill.get("content", "")
        title = skill.get("title", skill_id)
        skill_dir = skill.get("skill_dir", "")
        file_path = skill.get("file_path", "")

        if not content:
            envelop.payload = {"ok": False, "error": f"技能 {skill_id} 内容为空"}
            return envelop

        # 加运行时 header
        final_content = content
        if skill_dir:
            runtime_header = (
                "<!-- ============================================ -->\n"
                "<!-- 使用工具调用技能运行时信息 -->\n"
                f"<!-- 技能目录: {skill_dir} -->\n"
                f"<!-- 技能文件: {file_path} -->\n"
                "<!-- 本技能中所有相对路径（如 scripts/xxx.py、editing.md） -->\n"
                "<!-- 均相对于上述技能目录。 -->\n"
                "<!-- 使用绝对路径拼接。 -->\n"
                "<!-- ============================================ -->\n\n"
            )
            final_content = runtime_header + content

        _save_active_skill(session_id, final_content)

        envelop.payload = {
            "ok": True,
            "message": f"✅ 技能已加载：{title}，{len(final_content)} 字",
            "skill_id": skill.get("skill_id"),
            "title": title,
            "skill_dir": skill_dir,
            "size": len(final_content),
        }
        return envelop

    envelop.payload = {"ok": False, "error": f"未知 action: {action}"}
    return envelop


# ============================================================
# help
# ============================================================

def help():
    return {
        "route": "builtins/tools/skill_loader",
        "description": "技能加载器 — 搜索 / 加载 / 管理技能",
        "input": {
            "action": "scan | list | search | detail | categories | stats | load | clear | get_active | save_active",
            "query": "搜索问题（search 时）",
            "keywords": "关键词列表（search 时，可选，为空时用 query 兜底）",
            "category": "分类筛选（list / search 时，可选）",
            "limit": "返回数量（默认 10）",
            "skill_id": "技能 ID（detail / load 时）",
            "mode": "load / clear（load 时，默认 load）",
            "content": "新内容（save_active 时）",
        },
        "output": {
            "ok": "是否成功",
            "data": "结果数据",
            "message": "结果消息",
        },
    }
