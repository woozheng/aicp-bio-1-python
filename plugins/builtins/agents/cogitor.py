"""
cogitor — AICP 系统概览（极简版）
================================
无状态、实时、按需。不需要启动、不需要心跳、不需要分析。
- get_map：实时扫描 _registry + www/，返回系统概览
- 零 token（只正则提取 <title>）
- 毫秒级响应
"""

import core
import re
from pathlib import Path


TITLE_PATTERN = re.compile(r'<title>(.*?)</title>', re.DOTALL)


def _scan_frontend() -> dict:
    """扫描 www/ 目录，提取每个系统的标题"""
    result = {}
    www_dir = Path("www")
    if not www_dir.exists():
        return result
    
    for app_dir in www_dir.iterdir():
        if not app_dir.is_dir():
            continue
        index_html = app_dir / "index.html"
        if not index_html.exists():
            continue
        
        app_name = app_dir.name
        title = app_name
        
        try:
            content = index_html.read_text(encoding="utf-8")
            match = TITLE_PATTERN.search(content)
            if match:
                title = match.group(1).strip()
        except Exception:
            pass
        
        result[app_name] = title
    
    return result


async def execute(envelop, agent):
    action = envelop.payload.get("action", "get_map")
    
    if action == "get_map":
        # 1. 从 _registry 获取后端插件列表
        try:
            result = await agent.system.call(core.Envelop(
                sender="builtins/agents/cogitor",
                receiver="os/_registry",
                payload={"action": "list"}
            ))
            if not result or not result.payload or not result.payload.get("ok"):
                envelop.payload = {"ok": False, "error": "无法获取插件列表"}
                return envelop
            apis = result.payload.get("apis", [])
        except Exception as e:
            envelop.payload = {"ok": False, "error": f"获取插件列表失败: {e}"}
            return envelop
        
        # 2. 扫描 www/ 获取前端系统
        frontend = _scan_frontend()
        
        # 3. 按项目分类后端插件
        projects = {}
        for api in apis:
            name = api.get("name", "")
            if not name:
                continue
            parts = name.split("/")
            if len(parts) >= 2:
                category = parts[0]
                project = parts[1] if len(parts) > 2 else "_root"
            else:
                category = "_root"
                project = "_root"
            if category not in projects:
                projects[category] = {}
            if project not in projects[category]:
                projects[category][project] = []
            projects[category][project].append(name)
        
        # 4. 生成概览
        total_plugins = len(apis)
        total_frontend = len(frontend)
        
        summary_lines = [f"系统共有 {total_plugins} 个后端插件，{total_frontend} 个前端系统。", ""]
        
        # ★ 前端系统
        if frontend:
            summary_lines.append("【纯前端应用】")
            for app_name, title in sorted(frontend.items()):
                summary_lines.append(f"  • {app_name} — {title}")
            summary_lines.append("")
        
        # 后端插件按项目分类
        for category in sorted(projects.keys()):
            if category == "_root":
                continue  # 跳过根目录插件，避免混乱
            summary_lines.append(f"【{category}】")
            for project in sorted(projects[category].keys()):
                plugin_list = projects[category][project]
                summary_lines.append(f"  • {project}（{len(plugin_list)}个插件）")
                for plugin in sorted(plugin_list):
                    summary_lines.append(f"      - {plugin}")
            summary_lines.append("")
        
        envelop.payload = {
            "ok": True,
            "summary": "\n".join(summary_lines),
            "data": {
                "total_plugins": total_plugins,
                "total_frontend": total_frontend,
                "frontend": frontend,
                "projects": projects,
            }
        }
        return envelop
    
    elif action == "list_plugins":
        try:
            result = await agent.system.call(core.Envelop(
                sender="builtins/agents/cogitor",
                receiver="os/_registry",
                payload={"action": "list"}
            ))
            if not result or not result.payload or not result.payload.get("ok"):
                envelop.payload = {"ok": False, "error": "无法获取插件列表"}
                return envelop
            apis = result.payload.get("apis", [])
            plugin_names = [a.get("name", "") for a in apis if a.get("name")]
            envelop.payload = {"ok": True, "plugins": plugin_names, "total": len(plugin_names)}
            return envelop
        except Exception as e:
            envelop.payload = {"ok": False, "error": f"获取插件列表失败: {e}"}
            return envelop
    
    else:
        envelop.payload = {"ok": False, "error": f"未知 action: {action}"}
        return envelop


def help() -> dict:
    return {
        "route": "builtins/agents/cogitor",
        "description": "AICP 系统概览 — 实时扫描 _registry + www/，零 token",
        "input": {
            "action": "get_map | list_plugins",
        },
        "output": {
            "ok": "是否成功",
            "summary": "系统概览文本",
            "data": "结构化数据"
        }
    }