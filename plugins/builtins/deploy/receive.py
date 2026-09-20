"""部署接收插件 — 接收文件并写入本地"""
from pathlib import Path

async def execute(envelop, agent):
    payload = envelop.payload.get("payload", envelop.payload)
    action = payload.get("action", "receive")
    
    if action == "receive":
        app_id = payload.get("app_id", "")
        files = payload.get("files", {})
        
        if not app_id or not files:
            envelop.payload = {"ok": False, "error": "Missing app_id or files"}
            return envelop
        
        try:
            for filepath, content in files.items():
                full_path = Path(filepath)
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content, encoding="utf-8")
            
            envelop.payload = {"ok": True, "app_id": app_id, "files_count": len(files)}
        except Exception as e:
            envelop.payload = {"ok": False, "error": str(e)}
        
        return envelop
    
    envelop.payload = {"ok": False, "error": f"Unknown action: {action}"}
    return envelop