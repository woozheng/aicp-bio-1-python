# plugins/builtins/studio/api.py
"""Studio API — 项目文件管理（需 token 鉴权）"""
import os
import re
from pathlib import Path
from core import Envelop


async def execute(envelop, agent):
    STUDIO_TOKEN = agent.config.get("studio_token", "")
    token = envelop.payload.get("token", "")
    
    if not STUDIO_TOKEN or token != STUDIO_TOKEN:
        return Envelop(receiver=envelop.sender, payload={"ok": False, "error": "无权限"})
    
    action = envelop.payload.get("action", "")
    app_id = envelop.payload.get("app_id", "")
    
    # 列出所有项目
    if action == "list_projects":
        apps_dir = Path("plugins/applications")
        if not apps_dir.exists():
            return Envelop(receiver=envelop.sender, payload={"ok": True, "projects": []})
        apps = [d.name for d in apps_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        return Envelop(receiver=envelop.sender, payload={"ok": True, "projects": sorted(apps)})
    
    # 列出项目文件树
    if action == "list_files":
        if not app_id:
            return Envelop(receiver=envelop.sender, payload={"ok": False, "error": "缺少 app_id"})
        result = {}
        for base, label in [("plugins/applications", "plugins"), ("www", "www")]:
            root = Path(f"{base}/{app_id}")
            if not root.exists():
                result[label] = []
                continue
            tree = []
            for item in sorted(root.rglob("*")):
                if item.name in ("__pycache__", ".git") or item.suffix == ".pyc" or item.name == "__init__.py":
                    continue
                rel = str(item.relative_to(root)).replace("\\", "/")
                tree.append({"name": rel, "dir": item.is_dir(), "path": f"{base}/{app_id}/{rel}"})
            result[label] = tree
        return Envelop(receiver=envelop.sender, payload={"ok": True, "tree": result})
    
    # 读文件
    if action == "read_file":
        path = envelop.payload.get("path", "")
        if not path:
            return Envelop(receiver=envelop.sender, payload={"ok": False, "error": "缺少 path"})
        full = Path(path).resolve()
        allowed = [Path("plugins/applications").resolve(), Path("www").resolve(), Path("plugins/_cache").resolve()]
        if not any(str(full).startswith(str(p)) for p in allowed):
            return Envelop(receiver=envelop.sender, payload={"ok": False, "error": "路径越权"})
        if not full.exists() or full.is_dir():
            return Envelop(receiver=envelop.sender, payload={"ok": False, "error": "文件不存在"})
        content = full.read_text(encoding="utf-8", errors="replace")
        return Envelop(receiver=envelop.sender, payload={"ok": True, "content": content})
    
    # 写单个文件（编辑器保存用）
    if action == "write_file":
        path = envelop.payload.get("path", "")
        content = envelop.payload.get("content", "")
        if not path:
            return Envelop(receiver=envelop.sender, payload={"ok": False, "error": "缺少 path"})
        full = Path(path).resolve()
        allowed = [Path("plugins/applications").resolve(), Path("www").resolve(), Path("plugins/_cache").resolve()]
        if not any(str(full).startswith(str(p)) for p in allowed):
            return Envelop(receiver=envelop.sender, payload={"ok": False, "error": "路径越权"})
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return Envelop(receiver=envelop.sender, payload={"ok": True, "path": str(full)})
    
    # 保存代码块（书签用，从 AI 回复中提取 === TYPE: path === 块）
    if action == "save_blocks":
        text = envelop.payload.get("text", "")
        app_id = envelop.payload.get("app_id", "")
        if not text:
            return Envelop(receiver=envelop.sender, payload={"ok": False, "error": "缺少 text"})
        
        saved = []
        pattern = r'===\s*(PLUGIN|HTML|CSS|JS|APP|YAML):\s*(\S+)\s*===\s*\n(.*?)=== END ==='
        for match in re.finditer(pattern, text, re.DOTALL):
            filepath = match.group(2).strip()
            code = match.group(3).strip()
            
            if app_id:
                filepath = filepath.replace("{project}", app_id)
            
            # 如果 AI 已经输出了完整路径，直接使用
            if '/' in filepath and (filepath.startswith('plugins/') or filepath.startswith('www/')):
                full = Path(filepath)
            else:
                # 只有文件名，根据项目拼路径
                if filepath.endswith(('.css', '.js', '.yaml', '.yml', '.json', '.xml', '.svg', '.html', '.htm')):
                    full = Path(f"www/{app_id}") / filepath if app_id else Path(f"www/{filepath}")
                else:
                    full = Path(f"plugins/_cache") / filepath if not app_id else Path(f"plugins/applications/{app_id}") / filepath
            
            full = full.resolve()
            allowed = [Path("plugins/applications").resolve(), Path("www").resolve(), Path("plugins/_cache").resolve()]
            if not any(str(full).startswith(str(p)) for p in allowed):
                continue
            
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(code, encoding="utf-8")
            saved.append(str(full))
        
        return Envelop(receiver=envelop.sender, payload={"ok": True, "saved": saved, "count": len(saved)})
    # 删除文件
    if action == "delete_file":
        path = envelop.payload.get("path", "")
        if not path:
            return Envelop(receiver=envelop.sender, payload={"ok": False, "error": "缺少 path"})
        full = Path(path).resolve()
        allowed = [Path("plugins/applications").resolve(), Path("www").resolve(), Path("plugins/_cache").resolve()]
        if not any(str(full).startswith(str(p)) for p in allowed):
            return Envelop(receiver=envelop.sender, payload={"ok": False, "error": "路径越权"})
        if full.exists():
            full.unlink()
        return Envelop(receiver=envelop.sender, payload={"ok": True})
    
    # 获取协议内容
    if action == "get_protocol":
        protocol_path = Path("plugins/builtins/studio/aicp.md")
        if protocol_path.exists():
            protocol = protocol_path.read_text(encoding="utf-8")
            return Envelop(receiver=envelop.sender, payload={"ok": True, "protocol": protocol})
        return Envelop(receiver=envelop.sender, payload={"ok": False, "error": "协议文件不存在"})
    
    return Envelop(receiver=envelop.sender, payload={"ok": False, "error": f"未知操作: {action}"})