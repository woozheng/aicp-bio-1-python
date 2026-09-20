"""
知识库 — 存一切，查一切（纯 JSON 版本 + Chroma 向量增强）
"""
import json
import hashlib
import core
from pathlib import Path
from datetime import datetime

PROJECT = Path(__file__).parent.name
KB_DIR = Path("data/knowledge_base")
KB_DIR.mkdir(parents=True, exist_ok=True)
INDEX_FILE = KB_DIR / "index.json"
CONFIG_FILE = Path(__file__).parent / "config.json"


# ============================================================
# Chroma 相关 — 延迟加载，不影响启动
# ============================================================
CHROMA_AVAILABLE = False
_chroma_client = None
_embedding_model = None
_embedding_model_name = None


def _ensure_chroma():
    """延迟导入 Chroma，只在需要时加载"""
    global CHROMA_AVAILABLE, _chroma_client, _embedding_model
    
    if CHROMA_AVAILABLE:
        return True
    
    config = _load_config()
    if not config.get("chroma", {}).get("enabled", False):
        return False
    
    try:
        import chromadb
        from sentence_transformers import SentenceTransformer
        CHROMA_AVAILABLE = True
        print("[kb] Chroma 已加载")
        return True
    except ImportError as e:
        print(f"[kb] Chroma 未安装: {e}")
        CHROMA_AVAILABLE = False
        return False


def _load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except:
            return {}
    return {}


def _save_config(config):
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_index():
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except:
            return {"entries": [], "version": 1}
    return {"entries": [], "version": 1}


def _save_index(index):
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _generate_id(content, source):
    raw = f"{source}:{content[:200]}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _should_save(content, source):
    """内容过滤"""
    config = _load_config()
    auto_cfg = config.get("auto_save", {})
    
    if not auto_cfg.get("enabled", True):
        return False, "自动保存已禁用"
    
    min_len = auto_cfg.get("min_length", 50)
    if len(content) < min_len:
        return False, f"内容太短 ({len(content)} < {min_len})"
    
    max_len = auto_cfg.get("max_length", 100000)
    if len(content) > max_len:
        return False, f"内容太长 ({len(content)} > {max_len})"
    
    exclude = auto_cfg.get("exclude_patterns", [])
    for pattern in exclude:
        if pattern in content:
            return False, f"匹配排除模式: {pattern}"
    
    return True, "通过"


def _is_duplicate(content, source, index):
    entry_id = _generate_id(content, source)
    for existing in index["entries"]:
        if existing["id"] == entry_id:
            return True, existing["id"]
    return False, None


def _is_quality_content(content):
    if len(content.strip()) < 50:
        return False
    meaningful = ["分析", "报告", "结果", "代码", "总结", "文档", "项目", "实现", "设计"]
    if any(k in content for k in meaningful):
        return True
    if len(content) > 100:
        return True
    return False


# ============================================================
# Chroma 相关操作（仅在 _ensure_chroma() 成功后调用）
# ============================================================
def _get_chroma_client():
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client
    
    if not _ensure_chroma():
        return None
    
    try:
        _chroma_client = chromadb.PersistentClient(
            path=str(KB_DIR / "chroma")
        )
        print("[kb] Chroma 客户端已初始化")
        return _chroma_client
    except Exception as e:
        print(f"[kb] Chroma 初始化失败: {e}")
        return None


def _get_embedding_model():
    global _embedding_model, _embedding_model_name
    
    if _embedding_model is not None:
        return _embedding_model
    
    if not _ensure_chroma():
        return None
    
    config = _load_config()
    chroma_cfg = config.get("chroma", {})
    model_name = chroma_cfg.get("model", "BAAI/bge-m3")
    
    try:
        print(f"[kb] 加载 Embedding 模型: {model_name}")
        _embedding_model = SentenceTransformer(model_name)
        _embedding_model_name = model_name
        print(f"[kb] ✅ Embedding 模型加载完成")
    except Exception as e:
        print(f"[kb] ❌ 模型加载失败: {e}")
        _embedding_model = None
    
    return _embedding_model


def _get_collection():
    client = _get_chroma_client()
    if client is None:
        return None
    
    config = _load_config()
    chroma_cfg = config.get("chroma", {})
    collection_name = chroma_cfg.get("collection", "knowledge_base")
    
    try:
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        print(f"[kb] ✅ Collection 就绪: {collection_name}")
        return collection
    except Exception as e:
        print(f"[kb] ❌ Collection 获取失败: {e}")
        return None


def _generate_embedding(text):
    model = _get_embedding_model()
    if model is None:
        return None
    try:
        return model.encode(text).tolist()
    except Exception as e:
        print(f"[kb] 向量生成失败: {e}")
        return None


# ============================================================
# 核心操作
# ============================================================
def help():
    return {
        "route": "/api/builtins/knowledge_base/main",
        "actions": {
            "save": {"params": {"content": "内容", "source": "来源", "type": "类型"}},
            "search": {"params": {"query": "搜索词"}},
            "ask": {"params": {"query": "问题"}},
            "list": {"params": {"type": "类型"}},
            "delete": {"params": {"entry_id": "ID"}},
            "stats": {},
            "config_get": {},
            "config_set": {"params": {"config": "配置对象"}},
            "auto_save": {"params": {"content": "内容", "source": "来源", "title": "标题", "type": "类型", "metadata": "元数据"}},
            "rebuild_chroma": {"params": {}},
        }
    }


async def execute(envelop, agent):
    payload = envelop.payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except:
            payload = {"action": payload}
    if isinstance(payload, dict) and "payload" in payload:
        payload = payload["payload"]

    action = payload.get("action", "search")
    params = payload.get("params", {})

    if not action and params and "action" in params:
        action = params.pop("action")
    
    if action == "config_get":
        envelop.payload = {"ok": True, "config": _load_config()}
        return envelop

    elif action == "config_set":
        config = params.get("config", {})
        if config:
            _save_config(config)
        envelop.payload = {"ok": True, "message": "配置已保存"}
        return envelop

    elif action == "auto_save":
        envelop.payload = await _auto_save(params)
        return envelop

    elif action == "save":
        envelop.payload = await _save(params)
    elif action == "search":
        envelop.payload = await _search(params)
    elif action == "ask":
        envelop.payload = await _ask(params, agent)
    elif action == "list":
        envelop.payload = await _list(params)
    elif action == "delete":
        envelop.payload = await _delete(params)
    elif action == "file":
        return await _serve_file(envelop, agent)
    elif action == "get":
        envelop.payload = await _get(params)
    elif action == "stats":
        envelop.payload = await _stats()
    elif action == "rebuild_chroma":
        envelop.payload = await _rebuild_chroma(params)
    else:
        envelop.payload = {"ok": False, "error": f"未知操作: {action}"}

    return envelop


async def _get(params):
    entry_id = params.get("entry_id", "")
    if not entry_id:
        return {"ok": False, "error": "缺少 entry_id"}
    
    index = _load_index()
    for entry in index["entries"]:
        if entry["id"] == entry_id:
            return {"ok": True, "entry": entry}
    
    return {"ok": False, "error": "未找到该条目"}


async def _save_to_chroma(entry):
    """将条目同步存入 Chroma"""
    if not _ensure_chroma():
        return
    
    collection = _get_collection()
    if collection is None:
        return

    content = entry.get("content", "")
    if not content:
        return

    vector = _generate_embedding(content)
    if vector is None:
        return

    try:
        collection.add(
            ids=[entry["id"]],
            embeddings=[vector],
            documents=[content],
            metadatas=[{
                "title": entry.get("title", ""),
                "type": entry.get("type", "text"),
                "source": entry.get("source", "unknown"),
                "created_at": entry.get("created_at", ""),
            }]
        )
    except Exception as e:
        print(f"[kb] Chroma 存储失败: {e}")


async def _save(params):
    content = params.get("content", "")
    source = params.get("source", "unknown")
    entry_type = params.get("type", "text")
    metadata = params.get("metadata", {})
    title = params.get("title", source)

    if not content or len(content) < 10:
        return {"ok": False, "error": "内容太短"}

    index = _load_index()
    entry_id = _generate_id(content, source)

    for existing in index["entries"]:
        if existing["id"] == entry_id:
            existing["content"] = content
            existing["metadata"] = metadata
            existing["updated_at"] = datetime.now().isoformat()
            
            summary_val = metadata.get("summary") if metadata else None
            if summary_val:
                existing.setdefault("summaries", []).append({
                    "query": metadata.get("query", "auto_save"),
                    "summary": summary_val,
                    "generated_at": datetime.now().isoformat()
                })
            
            _save_index(index)
            await _save_to_chroma(existing)
            return {"ok": True, "id": entry_id, "action": "updated"}

    entry = {
        "id": entry_id,
        "title": title,
        "content": content,
        "type": entry_type,
        "source": source,
        "metadata": metadata,
        "summaries": [],
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }

    summary_val = metadata.get("summary") if metadata else None
    if summary_val:
        entry["summaries"].append({
            "query": metadata.get("query", "auto_save"),
            "summary": summary_val,
            "generated_at": datetime.now().isoformat()
        })

    index["entries"].append(entry)
    _save_index(index)

    await _save_to_chroma(entry)

    return {"ok": True, "id": entry_id, "action": "created"}


async def _vector_search(params):
    """语义向量搜索（带相似度阈值过滤）"""
    if not _ensure_chroma():
        return {"ok": True, "results": [], "total": 0, "vector": False}
    
    query = params.get("query", "").strip()
    if not query:
        return {"ok": False, "error": "请输入搜索内容"}

    n_results = params.get("n_results", 10)
    
    config = _load_config()
    auto_cfg = config.get("auto_save", {})
    threshold = auto_cfg.get("similarity_threshold", 0.5)

    query_vector = _generate_embedding(query)
    if query_vector is None:
        print("[kb] ⚠️ 向量生成失败，降级到关键词搜索")
        return await _search_keyword(params)

    collection = _get_collection()
    if collection is None:
        return await _search_keyword(params)

    try:
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=n_results,
            include=["documents", "metadatas", "distances"]
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return await _search_keyword(params)

    entries = []
    if results and results.get("ids") and len(results["ids"]) > 0:
        ids = results["ids"][0]
        for i, doc_id in enumerate(ids):
            meta = results["metadatas"][0][i] if results.get("metadatas") else {}
            distance = results["distances"][0][i] if results.get("distances") else 0
            similarity = 1 - distance
            
            if similarity < threshold:
                continue
            
            doc_content = results["documents"][0][i] if results.get("documents") else ""
            entries.append({
                "id": doc_id,
                "title": meta.get("title", ""),
                "type": meta.get("type", "text"),
                "source": meta.get("source", "unknown"),
                "content_preview": doc_content[:300] if doc_content else "",
                "score": similarity,
                "created_at": meta.get("created_at", ""),
            })
    else:
        print("[kb] ⚠️ 向量搜索无结果")

    print(f"[kb] 最终返回 {len(entries)} 条结果")
    return {"ok": True, "results": entries, "total": len(entries), "vector": True}


async def _search_keyword(params):
    """关键词搜索"""
    query = params.get("query", "").strip()
    if not query:
        return {"ok": False, "error": "请输入搜索内容"}

    index = _load_index()
    results = []
    query_lower = query.lower()

    for entry in index["entries"]:
        score = 0
        content_lower = entry["content"].lower()
        for word in query_lower.split():
            if word in content_lower:
                score += 1
        if query_lower in entry["title"].lower():
            score += 5
        if score > 0:
            results.append({
                "id": entry["id"],
                "title": entry["title"],
                "type": entry["type"],
                "source": entry["source"],
                "content_preview": entry["content"][:300],
                "score": score,
                "created_at": entry["created_at"]
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"ok": True, "results": results[:20], "total": len(results), "vector": False}


async def _search(params):
    query = params.get("query", "").strip()
    if not query:
        return {"ok": False, "error": "请输入搜索内容"}

    keyword_result = await _search_keyword(params)
    if keyword_result.get("results") and len(keyword_result["results"]) > 0:
        print(f"[kb] 关键词搜索命中 {len(keyword_result['results'])} 条")
        return keyword_result

    config = _load_config()
    chroma_cfg = config.get("chroma", {})
    if chroma_cfg.get("enabled", False):
        print("[kb] 关键词无结果，尝试向量搜索")
        return await _vector_search(params)
    else:
        return keyword_result


async def _ask(params, agent):
    query = params.get("query", "").strip()
    if not query:
        return {"ok": False, "error": "请输入问题"}

    if not agent.llm:
        return {"ok": False, "error": "LLM 不可用"}

    search_result = await _search({"query": query, "n_results": 20})
    if not search_result.get("ok") or not search_result.get("results"):
        return {"ok": True, "answer": "知识库里没有找到相关内容", "sources": []}

    fragments = search_result["results"]

    index = _load_index()
    id_to_entry = {e["id"]: e for e in index["entries"]}
    
    summaries = []
    for frag in fragments:
        entry = id_to_entry.get(frag["id"])
        if not entry:
            continue
        
        frag_summaries = entry.get("summaries", [])
        existing = next((s for s in frag_summaries if s.get("query") == query), None)
        
        if existing:
            summary = existing["summary"]
        else:
            content = entry.get("content", "")
            prompt = f"请用一句话（50字以内）概括以下内容，针对问题：{query}\n\n{content[:2000]}"
            summary = await agent.llm.chat([{"role": "user", "content": prompt}])
            summary = summary.strip()
            frag_summaries.append({"query": query, "summary": summary})
            entry["summaries"] = frag_summaries
            _save_index(index)
        
        summaries.append({
            "id": frag["id"],
            "title": entry.get("title", "未命名"),
            "source": entry.get("source", "未知"),
            "department": entry.get("metadata", {}).get("department", ""),
            "created_at": entry.get("created_at", ""),
            "summary": summary,
        })

    summary_text = "\n".join([
        f"{i+1}. [{s['source']}] {s['title']}\n   {s['summary']}"
        for i, s in enumerate(summaries)
    ])
    
    menu_prompt = f"""
用户问题：{query}

以下是从知识库中检索到的相关内容摘要：
{summary_text}

请将这些摘要按来源/部门分组，生成一个结构化的索引菜单。
不混合不同来源，不生成通用答案。让用户选择查看完整内容。
"""
    menu = await agent.llm.chat([{"role": "user", "content": menu_prompt}])

    return {
        "ok": True,
        "answer": menu,
        "sources": summaries,
        "total": len(summaries)
    }


async def _list(params):
    index = _load_index()
    entry_type = params.get("type", "all")

    entries = index.get("entries", [])
    if entry_type != "all":
        entries = [e for e in entries if e.get("type") == entry_type]

    entries.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    return {
        "ok": True,
        "total": len(entries),
        "entries": entries[:50]
    }


async def _delete(params):
    entry_id = params.get("entry_id", "")
    if not entry_id:
        return {"ok": False, "error": "缺少 entry_id"}

    index = _load_index()
    original_len = len(index["entries"])
    index["entries"] = [e for e in index["entries"] if e["id"] != entry_id]

    if len(index["entries"]) == original_len:
        return {"ok": False, "error": "未找到该条目"}

    _save_index(index)

    if _ensure_chroma():
        collection = _get_collection()
        if collection is not None:
            try:
                collection.delete(ids=[entry_id])
            except Exception as e:
                print(f"[kb] Chroma 删除失败: {e}")

    return {"ok": True, "deleted": True}


async def _stats():
    index = _load_index()
    entries = index.get("entries", [])

    type_count = {}
    for e in entries:
        t = e.get("type", "unknown")
        type_count[t] = type_count.get(t, 0) + 1

    chroma_stats = {"available": False, "count": 0}
    if _ensure_chroma():
        try:
            collection = _get_collection()
            if collection is not None:
                result = collection.get()
                chroma_stats = {
                    "available": True,
                    "count": len(result.get("ids", []))
                }
        except Exception as e:
            chroma_stats = {"available": False, "error": str(e)}

    return {
        "ok": True,
        "total": len(entries),
        "by_type": type_count,
        "sources": len(set(e.get("source", "unknown") for e in entries)),
        "chroma": chroma_stats
    }


async def _auto_save(params):
    content = params.get("content", "")
    source = params.get("source", "unknown")
    metadata = params.get("metadata", {})
    title = params.get("title", source)
    entry_type = params.get("type", "auto")

    if entry_type in ["image", "screenshot"]:
        result = await _save({
            "content": content,
            "source": source,
            "type": entry_type,
            "title": title,
            "metadata": metadata
        })
        if result.get("ok"):
            print(f"[kb] ✅ auto_save: {result.get('id')}")
        return {"ok": True, "id": result.get("id"), "action": "saved"}

    ok, reason = _should_save(content, source)
    if not ok:
        print(f"[kb] auto_save 跳过: {reason}")
        return {"ok": False, "reason": reason, "action": "skipped"}

    index = _load_index()
    is_dup, existing_id = _is_duplicate(content, source, index)
    if is_dup:
        print(f"[kb] auto_save 跳过: 重复 ({existing_id})")
        return {"ok": False, "reason": "重复", "existing_id": existing_id, "action": "skipped"}

    if not _is_quality_content(content):
        print(f"[kb] auto_save 跳过: 质量不足")
        return {"ok": False, "reason": "质量不足", "action": "skipped"}

    config = _load_config()
    auto_cfg = config.get("auto_save", {})
    similarity_threshold = auto_cfg.get("dedup_threshold", 0.85)
    chroma_cfg = config.get("chroma", {})
    
    if chroma_cfg.get("enabled", False) and _ensure_chroma():
        query_vector = _generate_embedding(content)
        if query_vector is not None:
            collection = _get_collection()
            if collection is not None:
                try:
                    similar = collection.query(
                        query_embeddings=[query_vector],
                        n_results=1,
                        include=["distances"]
                    )
                    if similar and similar.get("distances") and len(similar["distances"][0]) > 0:
                        distance = similar["distances"][0][0]
                        similarity = 1 - distance
                        if similarity >= similarity_threshold:
                            print(f"[kb] auto_save 跳过: 相似度 {similarity:.2f} >= {similarity_threshold}")
                            return {"ok": False, "reason": f"相似度 {similarity:.2f} 超过阈值", "action": "skipped"}
                except Exception as e:
                    print(f"[kb] 相似度检查失败: {e}")

    result = await _save({
        "content": content,
        "source": source,
        "type": entry_type,
        "title": title,
        "metadata": metadata
    })

    if result.get("ok"):
        print(f"[kb] ✅ auto_save: {result.get('id')}")

    return {"ok": True, "id": result.get("id"), "action": "saved"}


async def _serve_file(envelop, agent):
    """提供知识库文件下载（通过网关 FileResponse）"""
    payload = envelop.payload
    file_id = payload.get("file_id", "")
    thumb = payload.get("thumb", False)
    
    if not file_id:
        envelop.payload = {"ok": False, "error": "缺少 file_id"}
        return envelop
    
    files_dir = KB_DIR / "files"
    target_dir = files_dir / file_id
    if not target_dir.exists():
        envelop.payload = {"ok": False, "error": "文件不存在"}
        return envelop
    
    if thumb:
        file_path = target_dir / "thumb.jpg"
        if not file_path.exists():
            file_path = target_dir / "thumb.png"
    else:
        for f in target_dir.iterdir():
            if f.is_file() and f.name not in ["thumb.jpg", "thumb.png"]:
                file_path = f
                break
        else:
            envelop.payload = {"ok": False, "error": "没有找到文件"}
            return envelop
    
    if not file_path.exists():
        envelop.payload = {"ok": False, "error": "文件不存在"}
        return envelop
    
    import mimetypes
    envelop.meta["response_type"] = "file"
    envelop.meta["file_path"] = str(file_path)
    envelop.meta["content_type"] = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    envelop.meta["file_name"] = file_path.name
    envelop.meta["content_disposition"] = "inline" if thumb else "attachment"
    
    envelop.payload = {"ok": True}
    return envelop


async def _rebuild_chroma(params):
    """从 JSON 重建 Chroma 索引"""
    if not _ensure_chroma():
        return {"ok": False, "error": "Chroma 不可用"}

    index = _load_index()
    collection = _get_collection()
    if collection is None:
        return {"ok": False, "error": "Collection 不可用"}

    try:
        collection.delete(ids=[e["id"] for e in index["entries"]])
    except:
        pass

    count = 0
    for entry in index["entries"]:
        await _save_to_chroma(entry)
        count += 1
        if count % 100 == 0:
            print(f"[kb] 重建中: {count}/{len(index['entries'])}")

    return {"ok": True, "message": f"重建完成，共 {count} 条"}