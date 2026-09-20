import json
from pathlib import Path

CAPABILITY_INDEX_PATH = Path("data/capability_index.json")

def _load():
    if CAPABILITY_INDEX_PATH.exists():
        return json.loads(CAPABILITY_INDEX_PATH.read_text(encoding="utf-8"))
    return {"entries": [], "version": 1}

def _save(index):
    CAPABILITY_INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

async def execute(envelop, agent):
    action = envelop.payload.get("action")
    params = envelop.payload.get("params", {})

    if action == "list":
        index = _load()
        envelop.payload = {"ok": True, "entries": index["entries"], "total": len(index["entries"])}

    elif action == "add":
        entry = params.get("entry")
        if not entry:
            envelop.payload = {"ok": False, "error": "缺少 entry"}
            return envelop
        index = _load()
        # 简单去重，按 name 去重
        index["entries"] = [e for e in index["entries"] if e.get("name") != entry.get("name")]
        index["entries"].append(entry)
        _save(index)
        envelop.payload = {"ok": True, "message": "添加成功"}

    elif action == "update":
        name = params.get("name")
        updates = params.get("updates")
        if not name or not updates:
            envelop.payload = {"ok": False, "error": "缺少 name 或 updates"}
            return envelop
        index = _load()
        for e in index["entries"]:
            if e.get("name") == name:
                e.update(updates)
                break
        _save(index)
        envelop.payload = {"ok": True, "message": "更新成功"}

    elif action == "delete":
        name = params.get("name")
        if not name:
            envelop.payload = {"ok": False, "error": "缺少 name"}
            return envelop
        index = _load()
        index["entries"] = [e for e in index["entries"] if e.get("name") != name]
        _save(index)
        envelop.payload = {"ok": True, "message": "删除成功"}

    else:
        envelop.payload = {"ok": False, "error": f"未知操作: {action}"}
    return envelop