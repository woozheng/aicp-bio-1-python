# plugins/applications/skill_loader/skill_loader_api.py

import asyncio
import json
import re
from pathlib import Path
from difflib import SequenceMatcher

DATA_DIR = Path("data/skill_loader")
SKILLS_DIR = Path("data/skills")
INDEX_FILE = DATA_DIR / "index.json"

# ============================================================
# 黑名单关键词（命中任一即跳过，不纳入索引）
# ============================================================
BLOCKLIST_KEYWORDS = [
    "godmode",
    "jailbreak",
    "uncensoring",
    "uncensor",
    "red-teaming",
    "redteam",
    "safety-bypass",
    "bypass-safety",
    "refusal-removal",
    "abliteration",
    "guardrail-removal",
    "remove-guardrails",
    "excision",
    "model-surgery",
    "g0dm0d3",
    "obliterator",
    "obliteratus",
    "uncensored",
    "bypass",
    "越狱",
    "绕过",
    "去审查",
    "红队",
]

# ============================================================
# 分类映射（从 tags 推断分类）
# ============================================================
CATEGORY_MAP = {
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
# 核心函数
# ============================================================

def load_index():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_FILE.exists():
        return {"skills": [], "categories": [], "last_scan": None, "skipped": []}
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"skills": [], "categories": [], "last_scan": None, "skipped": []}


def save_index(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_blocked_skill(skill: dict) -> bool:
    """检查技能是否命中黑名单"""
    title = skill.get("title", "").lower()
    desc = skill.get("description", "").lower()
    tags = " ".join(skill.get("tags", [])).lower()
    combined = f"{title} {desc} {tags}"
    for kw in BLOCKLIST_KEYWORDS:
        if kw in combined:
            return True
    return False


def determine_category_from_tags(tags, file_path):
    """从 tags 或 file_path 推断分类"""
    # 1. 从 tags 匹配
    for tag in tags:
        tag_lower = tag.lower()
        if tag_lower in CATEGORY_MAP:
            return CATEGORY_MAP[tag_lower]
    
    # 2. 从 file_path 推断
    parts = Path(file_path).parts
    if len(parts) >= 2:
        # hermes-skills/software-development/xxx → software-development
        dir_name = parts[0]
        if dir_name in CATEGORY_MAP.values():
            return dir_name
        # 尝试匹配目录名到分类
        for key, cat in CATEGORY_MAP.items():
            if key in dir_name.lower():
                return cat
        return dir_name
    
    return "未分类"


def parse_skill_md(file_path):
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError:
        return None
    
    lines = content.splitlines()
    title = ""
    description = ""
    category = "未分类"
    tags = []
    in_frontmatter = False
    fm_end = 0
    
    if lines and lines[0].strip() == "---":
        in_frontmatter = True
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                fm_end = i + 1
                break
            line = lines[i].strip()
            if line.startswith("title:"):
                title = line[6:].strip().strip('"').strip("'")
            elif line.startswith("description:"):
                description = line[12:].strip().strip('"').strip("'")
            elif line.startswith("category:"):
                category = line[9:].strip().strip('"').strip("'")
            elif line.startswith("tags:"):
                tag_str = line[5:].strip()
                if tag_str.startswith("[") and tag_str.endswith("]"):
                    tags = [t.strip().strip('"').strip("'") for t in tag_str[1:-1].split(",") if t.strip()]
    
    if not title:
        for i in range(fm_end, len(lines)):
            if lines[i].startswith("# "):
                title = lines[i][2:].strip()
                break
    if not title:
        title = file_path.stem
    
    if not description:
        for i in range(fm_end, len(lines)):
            line = lines[i].strip()
            if line and not line.startswith("#"):
                description = line[:200]
                break
    
    rel_path = file_path.relative_to(SKILLS_DIR).as_posix()
    # ★ 用目录名作为 skill_id（去掉末尾的 /SKILL.md 或 SKILL.md）
    _id_base = re.sub(r'/SKILL\.md$', '', rel_path, flags=re.IGNORECASE)
    _id_base = re.sub(r'^SKILL\.md$', '', _id_base, flags=re.IGNORECASE)
    skill_id = re.sub(r'[^a-zA-Z0-9_]+', '_', _id_base.replace("/", "_")).lower()
    # 兜底
    if not skill_id:
        skill_id = re.sub(r'[^a-zA-Z0-9_]+', '_', file_path.stem).lower() or "unknown"

    # 分类补全
    if category == "未分类" or not category:
        category = determine_category_from_tags(tags, rel_path)

    return {
        "skill_id": skill_id,
        "title": title,
        "description": description,
        "category": category,
        "tags": tags,
        "file_path": rel_path,
        "skill_dir": str(file_path.parent.resolve()),   # ← 新增：绝对路径
        "content": content,
    }


def scan_skills():
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    md_files = list(SKILLS_DIR.rglob("SKILL.md"))
    skills = []
    categories_set = set()
    skipped = []
    blocked_titles = []

    for f in md_files:
        skill = parse_skill_md(f)
        if not skill:
            continue
        
        # 黑名单过滤
        if is_blocked_skill(skill):
            blocked_titles.append(skill.get("title", f.name))
            continue
        
        skills.append(skill)
        categories_set.add(skill["category"])
    
    categories = sorted(categories_set)
    import time
    data = {
        "skills": skills,
        "categories": categories,
        "last_scan": time.strftime("%Y-%m-%d %H:%M:%S"),
        "skipped": blocked_titles,
    }
    save_index(data)
    return data


def fuzzy_match(query, text, threshold=0.3):
    query = query.lower()
    text = text.lower()
    if query in text:
        return 1.0
    ratio = SequenceMatcher(None, query, text).ratio()
    return ratio if ratio >= threshold else 0


def search_skills(index, keywords, category=None, limit=50):
    """
    关键词粗筛：必须传入 keywords 列表，为空直接返回空结果
    匹配任意一个关键词即命中（OR 关系）
    """
    if not keywords:
        return [], 0

    query = " ".join(keywords).strip()
    if not query:
        return [], 0

    results = []
    for skill in index["skills"]:
        if category and skill["category"] != category:
            continue

        title_score = fuzzy_match(query, skill["title"])
        desc_score = fuzzy_match(query, skill["description"]) * 0.8
        tag_scores = [fuzzy_match(query, t) * 0.7 for t in skill["tags"]]
        content_score = fuzzy_match(query, skill["content"][:2000]) * 0.5
        score = max(title_score, desc_score, max(tag_scores) if tag_scores else 0, content_score)

        if score <= 0:
            continue

        results.append({
            "skill_id": skill["skill_id"],
            "title": skill["title"],
            "description": skill["description"],
            "category": skill["category"],
            "tags": skill["tags"],
            "file_path": skill["file_path"],
            "skill_dir": skill.get("skill_dir", ""),   # ← 新增
            "score": round(score, 3),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    total = len(results)
    return results[:limit], total


async def recommend_skills(agent, index, query, keywords, top_k=10):
    """LLM 精排：必须先经过粗筛，只处理 Top 30 候选"""
    if not keywords:
        return {"skills": [], "total": 0, "error": "keywords 为空"}

    # 粗筛 30 个候选
    candidates, _ = search_skills(index, keywords, limit=30)
    if not candidates:
        return {"skills": [], "total": 0}

    # 精简候选集
    briefs = [
        {
            "skill_id": s["skill_id"],
            "title": s["title"],
            "description": s["description"][:200],
            "tags": s["tags"][:5],
            "score": s["score"],
        }
        for s in candidates
    ]

    system_prompt = (
        "你是一个技能推荐专家。用户提出问题并给出关键词，你需要从候选技能中选出最匹配的。\n"
        "候选技能已经经过初步关键词筛选，你只需要做语义精排。\n"
        "返回JSON格式：{\"skills\": [{\"skill_id\": \"xxx\", \"reason\": \"推荐理由\"}]}\n"
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
        result = await agent.llm.chat_json([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        ranked = result.get("skills", []) if isinstance(result, dict) else []
        return {"skills": ranked[:top_k], "total": len(ranked)}
    except Exception as e:
        # LLM 失败时降级返回粗筛结果
        fallback = candidates[:top_k]
        return {
            "skills": [{"skill_id": s["skill_id"], "reason": "关键词匹配（LLM降级）"} for s in fallback],
            "total": len(fallback),
            "error": str(e),
        }


async def execute(envelop, agent):
    action = envelop.payload.get("action", "list")
    index = load_index()

    if action == "scan":
        data = await asyncio.get_event_loop().run_in_executor(None, scan_skills)
        envelop.payload = {"ok": True, "data": {
            "total": len(data["skills"]),
            "categories": data["categories"],
            "last_scan": data["last_scan"],
            "skipped": data.get("skipped", []),
        }}
        return envelop

    elif action == "list":
        await asyncio.get_event_loop().run_in_executor(None, scan_skills)
        index = load_index()
        category = envelop.payload.get("category")
        limit = envelop.payload.get("limit", 100)
        skills = []
        for s in index["skills"]:
            if category and s["category"] != category:
                continue
            skills.append({
                "skill_id": s["skill_id"],
                "title": s["title"],
                "description": s["description"],
                "category": s["category"],
                "tags": s["tags"],
                "file_path": s["file_path"],
                "skill_dir": s.get("skill_dir", ""),     # ← 新增
            })
            if len(skills) >= limit:
                break
        envelop.payload = {"ok": True, "data": {
            "skills": skills,
            "total": len(skills),
            "categories": index["categories"],
            "last_scan": index.get("last_scan"),
        }}
        return envelop

    elif action == "search":
        # ★ 先 scan，把新技能纳入索引
        await asyncio.get_event_loop().run_in_executor(None, scan_skills)
        index = load_index()   # ★ 重新读索引
        
        query = envelop.payload.get("query", "").strip()
        keywords = envelop.payload.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [keywords] if keywords.strip() else []

        if not query:
            envelop.payload = {"ok": False, "error": "query 不能为空"}
            return envelop

        if not keywords:
            envelop.payload = {"ok": False, "error": "keywords 不能为空"}
            return envelop

        category = envelop.payload.get("category")
        limit = envelop.payload.get("limit", 10)

        # 粗筛
        candidates, total = await asyncio.get_event_loop().run_in_executor(
            None, lambda: search_skills(index, keywords, category=category, limit=30)
        )

        if not candidates:
            envelop.payload = {"ok": True, "data": {
                "skills": [],
                "total": 0,
                "query": query,
                "keywords": keywords,
            }}
            return envelop

        # LLM 精排
        briefs = [
            {
                "skill_id": s["skill_id"],
                "title": s["title"],
                "description": s["description"][:200],
                "tags": s["tags"][:5],
            }
            for s in candidates
        ]

        system_prompt = (
            "你是一个技能推荐专家。用户提出问题并给出关键词，你需要从候选技能中选出最匹配的。\n"
            "候选技能已经经过初步关键词筛选，你只需要做语义精排。\n"
            "返回JSON格式：{\"skills\": [{\"skill_id\": \"xxx\", \"reason\": \"推荐理由\"}]}\n"
            "只返回最相关的 top_k 个，按相关度从高到低排序。"
        )

        user_prompt = (
            f"用户问题：{query}\n"
            f"关键词：{', '.join(keywords)}\n"
            f"返回数量：{limit}\n"
            f"候选技能（共{len(briefs)}个）：\n"
            + json.dumps(briefs, ensure_ascii=False)
        )

        try:
            result = await agent.llm.chat_json([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ])
            ranked = result.get("skills", []) if isinstance(result, dict) else []
            final_skills = []
            for item in ranked[:limit]:
                skill_id = item.get("skill_id")
                detail = next((s for s in index["skills"] if s["skill_id"] == skill_id), None)
                if detail:
                    final_skills.append({
                        "skill_id": detail["skill_id"],
                        "title": detail["title"],
                        "description": detail["description"],
                        "category": detail["category"],
                        "tags": detail["tags"],
                        "file_path": detail["file_path"],
                        "skill_dir": detail.get("skill_dir", ""),   # ← 新增
                        "reason": item.get("reason", ""),
                    })
            envelop.payload = {"ok": True, "data": {
                "skills": final_skills,
                "total": len(final_skills),
                "query": query,
                "keywords": keywords,
            }}
        except Exception as e:
            # LLM 失败降级
            fallback = candidates[:limit]
            envelop.payload = {"ok": True, "data": {
                "skills": fallback,
                "total": len(fallback),
                "query": query,
                "keywords": keywords,
                "fallback": True,
                "error": str(e),
            }}
        return envelop

    elif action == "detail":
        skill_id = envelop.payload.get("skill_id", "")
        skill = next((s for s in index["skills"] if s["skill_id"] == skill_id), None)
        if not skill:
            envelop.payload = {"ok": False, "error": "技能不存在"}
            return envelop
        envelop.payload = {"ok": True, "data": skill}
        return envelop

    elif action == "categories":
        envelop.payload = {"ok": True, "data": {
            "categories": index["categories"],
            "total": len(index["categories"]),
        }}
        return envelop

    elif action == "stats":
        category_count = {}
        for s in index["skills"]:
            c = s["category"]
            category_count[c] = category_count.get(c, 0) + 1
        envelop.payload = {"ok": True, "data": {
            "total_skills": len(index["skills"]),
            "total_categories": len(index["categories"]),
            "last_scan": index.get("last_scan"),
            "skipped": index.get("skipped", []),
            "category_count": category_count,
        }}
        return envelop

    else:
        envelop.payload = {"ok": False, "error": "未知 action"}
        return envelop


# @AICP_ALIGN: actions=scan,list,search,detail,categories,stats | output_fields=skills,total,categories,last_scan,skill_id,title,description,category,tags,file_path,score,mode,error,total_skills,total_categories | input_fields=action,query,category,limit,mode,skill_id | type_values=