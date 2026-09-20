"""create_tool — 创建新工具/应用（独立版）"""

import json
import re
import time
import asyncio
from pathlib import Path
from typing import Optional

import core


# ============================================================
# 独立工具函数（从 main_agent_handlers 复制过来）
# ============================================================

def _log(msg: str):
    print(f"[create_tool] {msg}", flush=True)


def _make_project_name_fallback(title: str) -> str:
    """兜底：从标题提取英文名"""
    english_words = re.findall(r'[a-zA-Z][a-zA-Z0-9_\-]*', title)
    if english_words:
        if len(english_words) >= 2 and len(english_words[0]) < 4:
            name = english_words[0] + '_' + english_words[1]
        else:
            name = english_words[0]
        name = re.sub(r'[^a-zA-Z0-9_-]', '_', name.lower())
        return name[:30] if name else f"app_{int(time.time())}"
    return f"app_{int(time.time())}"


async def _make_project_name_with_llm(agent, title: str) -> str:
    """用 LLM 从标题提取简短英文项目名"""
    if not agent or not hasattr(agent, 'llm') or not agent.llm:
        return _make_project_name_fallback(title)
    
    try:
        result = await asyncio.wait_for(
            agent.llm.chat_json([
                {"role": "system", "content": "你是一个项目命名助手。从标题中提取简短、有意义的英文项目名（小写，用下划线分隔，只包含字母数字下划线）。只返回 JSON: {\"name\": \"xxx\"}"},
                {"role": "user", "content": f"标题: {title}"}
            ]),
            timeout=5.0
        )
        name = result.get("name", "").strip().lower()
        if name and re.match(r'^[a-z][a-z0-9_\-]*$', name) and len(name) >= 2:
            return name[:30]
    except asyncio.TimeoutError:
        _log("_make_project_name_with_llm 超时，使用兜底")
    except Exception as e:
        _log(f"_make_project_name_with_llm 异常: {e}")
    
    return _make_project_name_fallback(title)


async def _summarize_project(agent, session_id: str, project_name: str) -> str:
    """为项目生成 README.md（独立实现，不依赖 main_agent_handlers）"""
    app_dir = Path(f"plugins/applications/{project_name}")
    www_dir = Path(f"www/{project_name}")

    if not app_dir.exists():
        return f"❌ 项目目录不存在: {app_dir}"

    backend_files = []
    api_summary = ""
    for f in sorted(app_dir.glob("*.py")):
        if f.stem == "_init":
            continue
        backend_files.append(f.name)
        code = f.read_text(encoding="utf-8")
        align = re.search(r'# @AICP_ALIGN:.*', code)
        if align:
            api_summary += f"\n- **{f.name}**: {align.group(0)}"

    frontend_files = []
    if www_dir.exists():
        frontend_files = [f.name for f in sorted(www_dir.glob("*")) if f.is_file()]

    app_yaml = ""
    yaml_path = app_dir / "app.yaml"
    if yaml_path.exists():
        app_yaml = yaml_path.read_text(encoding="utf-8")

    dir_tree = f"plugins/applications/{project_name}/\n"
    dir_tree += f"  ├── app.yaml\n"
    for f in backend_files:
        dir_tree += f"  ├── {f}\n"
    dir_tree += f"  └── README.md\n\n"
    dir_tree += f"www/{project_name}/\n"
    if frontend_files:
        for f in frontend_files:
            dir_tree += f"  └── {f}\n"
    else:
        dir_tree += "  (无前端文件)\n"

    prompt = (
        f"为项目 **{project_name}** 生成 README.md 文档。\n\n"
        f"## 目录结构\n{dir_tree}\n"
        f"## 项目配置 (app.yaml)\n{app_yaml}\n\n"
        f"## 后端 API 摘要\n{api_summary}\n\n"
        f"## 前端文件\n{', '.join(frontend_files) if frontend_files else '(无)'}\n\n"
        f"请生成标准 README.md，包含以下章节：\n"
        f"1. 项目简介（一句话）\n"
        f"2. 功能列表\n"
        f"3. 目录结构\n"
        f"4. API 接口说明（表格）\n"
        f"5. 前端页面说明\n"
        f"6. 配置说明\n"
        f"直接输出 Markdown，不要用代码块包裹。"
    )

    readme = await agent.llm.chat([
        {"role": "system", "content": "你是技术文档撰写专家，输出简洁清晰、结构完整的 Markdown 文档。"},
        {"role": "user", "content": prompt}
    ])

    readme_path = app_dir / "README.md"
    readme_path.write_text(readme.strip(), encoding="utf-8")
    return f"✅ README.md 已生成"


# ============================================================
# 主入口
# ============================================================

async def execute(envelop, agent):
    """创建新工具"""
    params = envelop.payload.get("params", {})

    if not params:
        params = {k: v for k, v in envelop.payload.items() if k != "action"}

    session_id = envelop.meta.get("session_id", "default")

    name = params.get("name") or params.get("title") or "未命名工具"
    description = params.get("description", "")
    features = params.get("features", [])
    api_actions = params.get("api_actions", [])
    ui_requirements = params.get("ui_requirements", [])
    style = params.get("style", "")

    _log(f"📥 收到创建请求: {name}")
    _log(f"   description: {description[:100] if description else '(空)'}...")
    _log(f"   api_actions: {len(api_actions)} 个")
    _log(f"   features: {len(features)} 个")
    _log(f"   ui_requirements: {len(ui_requirements)} 个")

    task_id = f"gen_{int(time.time())}"

    doc = {
        "title": name,
        "description": description,
        "features": features,
        "api_actions": api_actions,
        "ui_requirements": ui_requirements,
        "style": style,
    }

    _log(f"📤 调用 generator.generate_full...")
    _log(f"   doc: {json.dumps(doc, ensure_ascii=False)}")

    gen_result = await agent.system.call(core.Envelop(
        sender="builtins/agents/main_agent",
        receiver="builtins/generator",
        payload={
            "action": "generate_full",
            "document": doc,
            "task_id": task_id,
            "session_id": session_id
        }
    ))

    if not gen_result or not gen_result.payload.get("ok"):
        error = gen_result.payload.get("error", "未知错误") if gen_result else "无响应"
        envelop.payload = {"ok": False, "error": error}
        return envelop

    if gen_result.payload.get("tool_type") == "frontend":
        url = gen_result.payload.get("url", "")
        project_name = gen_result.payload.get("name", name)
        envelop.payload = {
            "ok": True,
            "data": {
                "project_name": project_name,
                "receiver": f"www/{project_name}/index.html",
                "type": "frontend",
                "actions": [],
                "files": [f"www/{project_name}/index.html"],
                "url": url,
                "hot_reloaded": True,
            },
            "message": f"✅ 纯前端应用已生成：`{project_name}`\n访问：{url}\n注意：纯前端在 www/ 目录，list_plugins 查不到是正常的。",
        }
        return envelop

    generated = gen_result.payload.get("generated", [])
    generated_html = gen_result.payload.get("generated_html", {})
    index_html = generated_html.get("index")
    all_ok = all(p.get("ok") for p in generated)

    if not all_ok:
        errors = [f"{p.get('name', '?')}: {p.get('error', '未知错误')}" for p in generated if not p.get("ok")]
        envelop.payload = {"ok": False, "error": f"部分插件生成失败: {'; '.join(errors)}"}
        return envelop

    project_name = name
    if not project_name or not re.match(r'^[a-zA-Z][a-zA-Z0-9_\-]*$', project_name):
        project_name = await _make_project_name_with_llm(agent, name)

    api_plugins_list = [
        p for p in generated
        if p.get("name", "").endswith(".py")
        and not p.get("name", "").endswith("_ui.py")
    ]
    has_api = len(api_plugins_list) > 0
    has_frontend = bool(index_html and len(index_html) > 100)

    _log(f"分类: has_api={has_api} (共 {len(api_plugins_list)} 个), has_frontend={has_frontend}")

    app_dir = Path(f"plugins/applications/{project_name}")
    www_dir = Path(f"www/{project_name}")
    api_names = []

    app_dir.mkdir(parents=True, exist_ok=True)

    if has_api:
        for p in api_plugins_list:
            if p.get("ok") and p.get("code"):
                plugin_name = p["name"].replace(".py", "")
                file_path = app_dir / f"{plugin_name}.py"
                file_path.write_text(p["code"], encoding="utf-8")
                _log(f"   ✅ API写入: {file_path}")
                api_names.append(plugin_name)

    _log("📝 生成 _init.py 和 app.yaml...")
    import yaml

    app_yaml_data = {
        "name": name,
        "id": project_name,
        "version": "1.0",
        "description": description,
        "entry": "_init.py",
    }
    (app_dir / "app.yaml").write_text(
        yaml.dump(app_yaml_data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8"
    )

    init_content = f'''"""
{name} 应用初始化
"""
from pathlib import Path

def init():
    """初始化应用配置"""
    pass
'''
    (app_dir / "_init.py").write_text(init_content, encoding="utf-8")

    if has_frontend:
        www_dir.mkdir(parents=True, exist_ok=True)
        html_out_path = www_dir / "index.html"
        html_out_path.write_text(index_html, encoding="utf-8")
        _log(f"   ✅ HTML写入: {html_out_path} ({len(index_html)} 字符)")

    base_url = getattr(agent, 'base_url', 'http://127.0.0.1:9000')

    if has_frontend and has_api:
        project_type = "fullstack"
    elif has_frontend:
        project_type = "frontend"
    else:
        project_type = "backend"

    if has_api and api_names:
        primary_api = api_names[0]
        receiver = f"applications/{project_name}/{primary_api}"
    elif has_frontend:
        receiver = f"www/{project_name}/index.html"
    else:
        receiver = ""

    actions = []
    for p in api_plugins_list:
        if p.get("ok") and p.get("code"):
            code = p["code"]
            m = re.search(r'ACTIONS_SCHEMA\s*=\s*\{', code)
            if m:
                schema_text = code[m.start():]
                action_names = re.findall(r'"(\w+)"\s*:\s*\{', schema_text)
                actions.extend(action_names)

    files = []
    if app_dir.exists():
        files.extend(str(f) for f in app_dir.rglob("*") if f.is_file())
    if www_dir.exists() and has_frontend:
        files.extend(str(f) for f in www_dir.rglob("*") if f.is_file())

    url = f"{base_url}/{project_name}/" if has_frontend else ""

    msg = f"✅ 工具已生成：`{project_name}`\n"
    msg += f"类型：{project_type}\n"
    if receiver:
        msg += f"调用路径：{receiver}\n"
    if actions:
        msg += f"支持 action：{', '.join(actions)}\n"
    if url:
        msg += f"访问地址：{url}\n"

    _log(f"✅ create_tool 完成: {project_name}")
    envelop.payload = {
        "ok": True,
        "data": {
            "project_name": project_name,
            "receiver": receiver,
            "type": project_type,
            "actions": actions,
            "files": files,
            "url": url,
            "hot_reloaded": True,
        },
        "message": msg.strip(),
    }
    return envelop


def help():
    return {
        "route": "builtins/tools/create_tool",
        "description": "创建新工具/应用（纯前端/前后端/纯后端）",
        "input": {
            "name": "项目名称",
            "description": "需求描述",
            "api_actions": "API action 列表（可选）",
            "features": "功能列表（可选）",
            "ui_requirements": "UI 需求（可选）",
            "style": "样式要求（可选）"
        },
        "output": {
            "ok": "是否成功",
            "data.project_name": "项目名",
            "data.receiver": "完整调用路径（后续用这个调）",
            "data.type": "backend / frontend / fullstack",
            "data.actions": "支持的 action 列表",
            "data.files": "生成的文件列表",
            "data.url": "前端访问地址（如有）",
            "data.hot_reloaded": "是否已热重载生效",
            "message": "结果消息"
        }
    }