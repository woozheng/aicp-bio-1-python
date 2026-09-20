"""fix_tool — 修复已有工具/应用（完全独立）"""

import json
import re
import time
import asyncio
import shutil
from pathlib import Path
from typing import Optional

import core


def _log(msg: str):
    print(f"[fix_tool] {msg}", flush=True)


async def _purify_project_requirement(agent, session_id: str, user_issue: str) -> dict:
    system_prompt = """
任务规则：
1. 检索对话记录，提取【首次创建项目】的原始结构化需求作为基线。
2. 遍历后续所有指令，筛选有效变更：修复指令、功能修改、新增需求；
   过滤：临时测试、随口设想、已经被用户否定作废的想法。
3. 合并基线需求与有效变更，生成最新生效完整需求 effective_doc，结构和创建工具doc保持一致：
{
  "title":"",
  "description":"",
  "features":[],
  "api_actions":[],
  "ui_requirements":[],
  "style":""
}
4. 判断本次用户指令类型：
- bugfix_only：只修复错误，无新增功能、无逻辑调整
- bugfix_with_feature：修复问题同时要求新增功能/调整原有逻辑

输出严格JSON，不要附加任何解释。
"""
    user_prompt = f"""
当前会话历史内的项目操作记录，本次用户最新指令：
{user_issue}

请输出合并后的有效需求文档与运行模式。
"""
    try:
        result = await agent.llm.chat_json([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ], role="code")
        effective_doc = result.get("effective_doc", {})
        run_mode = result.get("run_mode", "bugfix_only")
        if run_mode not in ("bugfix_only", "bugfix_with_feature"):
            run_mode = "bugfix_only"
        return {
            "effective_doc": effective_doc,
            "run_mode": run_mode
        }
    except Exception as e:
        _log(f"_purify_project_requirement 提纯异常 {e}，降级为空文档")
        return {
            "effective_doc": {},
            "run_mode": "bugfix_only"
        }


async def _get_contract(agent, target: str) -> dict:
    try:
        result = await agent.system.call(core.Envelop(
            sender="builtins/tools/fix_tool",
            receiver="builtins/agents/contract_agent",
            payload={"action": "get", "plugin": target}
        ))
        if result and result.payload.get("ok"):
            return result.payload.get("contract", {})
    except Exception as e:
        _log(f"_get_contract 失败: {e}")
    return {}


async def _update_contract(agent, target: str) -> bool:
    try:
        plugin_name = target.split("/")[-1]
        contract_file = Path(f"plugins/{target}.contract.json")
        if contract_file.exists():
            contract_file.unlink()
            _log(f"   🗑️ 删除旧契约: {contract_file}")

        result = await agent.system.call(core.Envelop(
            sender="builtins/tools/fix_tool",
            receiver="builtins/agents/contract_agent",
            payload={"action": "get", "plugin": target}
        ))
        if result and result.payload.get("ok"):
            _log(f"   ✅ 契约已更新: {plugin_name}")
            return True
        else:
            error = result.payload.get("error", "未知错误") if result else "无响应"
            _log(f"   ⚠️ 契约更新失败: {plugin_name} - {error}")
            return False
    except Exception as e:
        _log(f"   ⚠️ 契约更新异常: {e}")
        return False


def _extract_project_name(target: str) -> str:
    parts = target.split("/")
    if len(parts) >= 3:
        return parts[1]
    elif len(parts) == 2:
        return parts[1]
    else:
        return target


async def _analyze_impact(agent, target: str, issue: str, project_name: str,
                          contract_snapshot: dict = None) -> str:
    frontend_dir = Path(f"www/{project_name}")
    if not frontend_dir.exists():
        return "api_only"

    file_path = Path(f"plugins/{target}.py")
    source_code = file_path.read_text(encoding="utf-8")[:3000] if file_path.exists() else ""

    if contract_snapshot:
        contract = contract_snapshot
    else:
        contract = await _get_contract(agent, target)

    prompt = f"""
后端 API: {target}
修改需求: {issue}

当前源码（关键部分）:
{source_code if source_code else '(无法读取源码)'}

当前契约:
{json.dumps(contract, ensure_ascii=False, indent=2) if contract else '(无契约)'}

请判断这次修改是否会影响前端调用：
- 影响前端：修改了 action 名、参数名、返回字段、或新增了前端需要调用的 action
- 不影响前端：只修改了内部计算逻辑，接口不变

只回答一个词：frontend_affected 或 frontend_not_affected
"""
    try:
        response = await agent.llm.chat([{"role": "user", "content": prompt}], role="code")
        if "frontend_affected" in response.lower():
            return "api_with_frontend"
        return "api_only"
    except Exception as e:
        _log(f"_analyze_impact 失败: {e}")
        return "api_with_frontend"


async def _fix_single_file(agent, file_path: Path, issue: str, readme_content: str,
                            session_id: str, api_context: str = "") -> str:
    if not file_path.exists():
        return f"⚠️ 文件不存在: {file_path}"

    content = file_path.read_text(encoding="utf-8")

    fix_payload = {
        "action": "fix",
        "plugin_name": str(file_path),
        "code": content,
        "error": issue,
        "task": issue,
        "readme": readme_content,
        "session_id": session_id,
    }

    if api_context:
        fix_payload["api_context"] = api_context

    fix_result = await agent.system.call(core.Envelop(
        sender="builtins/tools/fix_tool",
        receiver="builtins/generator",
        payload=fix_payload
    ))

    if fix_result and fix_result.payload.get("ok"):
        fixed_code = fix_result.payload["code"]
        if fixed_code and fixed_code != content:
            bak_path = file_path.with_suffix(file_path.suffix + ".bak")
            if bak_path.exists():
                bak_path.unlink()
            file_path.rename(bak_path)
            file_path.write_text(fixed_code, encoding="utf-8")
            return f"✅ 已修复: {file_path} ({len(fixed_code)} 字符)"
        elif fixed_code == content:
            return f"⚠️ {file_path}: 修复后代码与原始完全一致，可能未生效"
        else:
            return f"⚠️ {file_path}: 修复后代码为空"
    else:
        error = fix_result.payload.get("error", "未知") if fix_result else "无响应"
        return f"❌ {file_path}: {error}"


async def _fix_backend_and_frontend(agent, target: str, issue: str, project_name: str,
                                     readme_content: str, session_id: str,
                                     backend_file_path: Path) -> str:
    results = []

    backend_path = backend_file_path
    if not backend_path.exists():
        return f"❌ 后端文件不存在: {backend_path}"

    result = await _fix_single_file(agent, backend_path, issue, readme_content, session_id)
    results.append(result)

    backend_code = backend_path.read_text(encoding="utf-8") if backend_path.exists() else ""

    frontend_dir = Path(f"www/{project_name}")
    if frontend_dir.exists():
        frontend_files = []
        for ext in ["*.html", "*.js", "*.css", "*.json"]:
            frontend_files.extend(frontend_dir.glob(ext))

        for file_path in frontend_files:
            frontend_issue = f"同步后端 API 变更：{issue}\n\n后端代码参考：\n{backend_code[:2000]}"
            result = await _fix_single_file(
                agent, file_path, frontend_issue, readme_content, session_id,
                api_context=backend_code[:3000]
            )
            results.append(f"   {result}")

    return "\n".join(results)


async def _get_project_list(agent) -> list:
    try:
        result = await agent.system.call(core.Envelop(
            sender="builtins/tools/fix_tool",
            receiver="builtins/agents/cogitor",
            payload={"action": "get_map"}
        ))
        if result and result.payload:
            data = result.payload.get("data", {})
            projects = data.get("projects", {})
            project_list = []
            for category, proj_dict in projects.items():
                for proj_name in proj_dict.keys():
                    if proj_name not in project_list:
                        project_list.append(proj_name)
            return project_list
        return []
    except Exception:
        return []


async def _find_project(agent, target: str) -> Optional[str]:
    if not target:
        return None

    if "applications/" in target:
        parts = target.split("applications/")[-1].split("/")
        if parts:
            candidate = parts[0].strip()
            if candidate and Path(f"plugins/applications/{candidate}").exists():
                return candidate

    projects = await _get_project_list(agent)
    if not projects:
        projects = [d.name for d in Path("plugins/applications").iterdir() if d.is_dir()]

    if target in projects:
        return target

    target_lower = target.lower()
    for p in projects:
        if p.lower() == target_lower:
            return p

    for p in projects:
        if target in p or p in target:
            return p

    return None


async def _summarize_project(agent, session_id: str, project_name: str) -> str:
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
    for f in backend_files:
        dir_tree += f"  ├── {f}\n"
    dir_tree += f"www/{project_name}/\n"
    if frontend_files:
        for f in frontend_files:
            dir_tree += f"  └── {f}\n"

    prompt = (
        f"为项目 **{project_name}** 生成 README.md 文档。\n\n"
        f"## 目录结构\n{dir_tree}\n"
        f"## 项目配置 (app.yaml)\n{app_yaml}\n"
        f"## 后端 API 摘要\n{api_summary}\n"
        f"## 前端文件\n{', '.join(frontend_files) if frontend_files else '(无)'}\n\n"
        f"请生成标准 README.md。直接输出 Markdown。"
    )

    readme = await agent.llm.chat([
        {"role": "system", "content": "你是技术文档撰写专家。"},
        {"role": "user", "content": prompt}
    ], role="code")

    readme_path = app_dir / "README.md"
    readme_path.write_text(readme.strip(), encoding="utf-8")
    return f"✅ README.md 已生成"


async def execute(envelop, agent):
    """修复已有工具"""
    params = envelop.payload.get("params", {})

    if not params:
        params = {k: v for k, v in envelop.payload.items() if k != "action"}

    session_id = envelop.meta.get("session_id", "default")

    target = params.get("target", "")
    issue = params.get("issue", "")
    effective_doc = params.get("effective_doc", {})
    run_mode = params.get("run_mode", "bugfix_only")
    contract_snapshot = params.get("contract_snapshot", {})

    if not effective_doc and issue:
        purified = await _purify_project_requirement(agent, session_id, issue)
        effective_doc = purified.get("effective_doc", {})
        run_mode = purified.get("run_mode", "bugfix_only")
        _log(f"📝 提纯需求: mode={run_mode}, doc_keys={list(effective_doc.keys())}")

    if not target:
        envelop.payload = {"ok": False, "error": "缺少 target 参数"}
        return envelop

    if not issue:
        envelop.payload = {"ok": False, "error": "缺少 issue 参数"}
        return envelop

    _log(f"🔧 fix_tool: target={target}, issue={issue[:100]}...")

    project_name = _extract_project_name(target)
    if not project_name:
        envelop.payload = {"ok": False, "error": f"无法从 target 提取项目名: {target}"}
        return envelop

    _log(f"   project_name: {project_name}")

    backend_dir = Path(f"plugins/applications/{project_name}")
    www_dir = Path(f"www/{project_name}")

    def get_backend_files():
        if not backend_dir.exists():
            return []
        return [f for f in backend_dir.glob("*.py") if f.name != "_init.py"]

    backend_files = get_backend_files()
    has_backend = len(backend_files) > 0
    has_frontend = www_dir.exists() and any(www_dir.glob("*.html"))

    file_type = None
    file_path = None
    answer = None
    force_sync_frontend = False

    if target.endswith("_api"):
        file_path = Path(f"plugins/{target}.py")
        if not file_path.exists():
            envelop.payload = {"ok": False, "error": f"文件不存在: {target}"}
            return envelop
        file_type = "backend"
        answer = "backend_only"
        _log(f"   ✅ 定位到后端文件: {file_path}")

    elif target.startswith("www/"):
        file_path = Path(target)
        if not file_path.exists():
            envelop.payload = {"ok": False, "error": f"文件不存在: {target}"}
            return envelop
        file_type = "frontend"
        answer = "frontend_only"
        _log(f"   ✅ 定位到前端文件: {file_path}")

    else:
        if not has_backend and not has_frontend:
            envelop.payload = {"ok": False, "error": f"项目 {project_name} 不存在"}
            return envelop

        judgment_prompt = f"""
项目: {project_name}
修复需求: {issue}

项目结构：
- 后端: {'存在' if has_backend else '不存在'}
- 前端: {'存在' if has_frontend else '不存在'}

判断规则：
1. 如果修复需求描述的是"页面样式"、"点击事件"、"按钮"、"表单"、"弹窗"、"显示"、"渲染" → frontend_only
2. 如果修复需求描述的是"API接口"、"参数"、"返回值"、"数据格式"、"后端逻辑" → backend_only
3. 两者都有 → both
4. 不确定时，默认 frontend_only

只回答一个词：backend_only / frontend_only / both
"""
        try:
            response = await agent.llm.chat([{"role": "user", "content": judgment_prompt}], role="code")
            answer = response.strip().lower()
            _log(f"   LLM 判断修复范围: {answer}")
        except Exception as e:
            _log(f"   LLM 判断失败，回退到 frontend_only: {e}")
            answer = "frontend_only"

        if answer == "frontend_only":
            if not has_frontend:
                envelop.payload = {"ok": False, "error": f"项目 {project_name} 没有前端文件"}
                return envelop
            file_path = www_dir / "index.html"
            if not file_path.exists():
                html_files = list(www_dir.glob("*.html"))
                file_path = html_files[0] if html_files else None
            if not file_path:
                envelop.payload = {"ok": False, "error": f"未找到前端 HTML 文件"}
                return envelop
            file_type = "frontend"
            _log(f"   ✅ 定位到前端文件: {file_path}")

        else:
            if has_backend:
                if len(backend_files) == 1:
                    file_path = backend_files[0]
                else:
                    file_list = "\n".join([f"- {f.name}" for f in backend_files])
                    locate_prompt = f"""
项目: {project_name}
修复需求: {issue}

后端文件：
{file_list}

请判断需要修改哪个文件？只回答文件名。
"""
                    try:
                        locate_response = await agent.llm.chat([{"role": "user", "content": locate_prompt}], role="code")
                        target_filename = locate_response.strip()
                        matched = None
                        for f in backend_files:
                            if f.name == target_filename:
                                matched = f
                                break
                        if not matched:
                            for f in backend_files:
                                if target_filename in f.name or f.name in target_filename:
                                    matched = f
                                    break
                        file_path = matched if matched else backend_files[0]
                        _log(f"   ✅ 匹配到: {file_path.name}")
                    except Exception:
                        file_path = backend_files[0]
                file_type = "backend"
                force_sync_frontend = (answer == "both" and has_frontend)
                _log(f"   ✅ 定位到后端文件: {file_path}")

            if file_path is None and has_frontend:
                file_path = www_dir / "index.html"
                if not file_path.exists():
                    html_files = list(www_dir.glob("*.html"))
                    file_path = html_files[0] if html_files else None
                if file_path:
                    file_type = "frontend"
                    answer = "frontend_only"
                    _log(f"   ✅ 回退到前端文件: {file_path}")

            if file_path is None:
                envelop.payload = {"ok": False, "error": f"项目 {project_name} 没有可修复的文件"}
                return envelop

    readme_content = ""

    backend_context = ""
    if has_backend:
        backend_code_parts = []
        for py_file in backend_dir.glob("*.py"):
            if py_file.name != "_init.py":
                code = py_file.read_text(encoding="utf-8")
                backend_code_parts.append(f"=== {py_file.name} ===\n{code[:3000]}")
        if backend_code_parts:
            backend_context = "\n\n".join(backend_code_parts)[:8000]

    if file_type == "backend":
        impact = await _analyze_impact(agent, target, issue, project_name, contract_snapshot)
        if force_sync_frontend:
            impact = "api_with_frontend"
    else:
        impact = "frontend_only"
    _log(f"   影响分析: {impact}")

    if file_type == "backend" and impact == "api_with_frontend":
        result = await _fix_backend_and_frontend(
            agent, target, issue, project_name, readme_content, session_id,
            backend_file_path=file_path
        )
    else:
        if file_type == "frontend" and backend_context:
            result = await _fix_single_file(
                agent, file_path, issue, readme_content, session_id,
                api_context=backend_context
            )
        else:
            result = await _fix_single_file(
                agent, file_path, issue, readme_content, session_id
            )

    base_url = getattr(agent, 'base_url', 'http://127.0.0.1:9000')

    if has_backend and has_frontend:
        project_type = "fullstack"
    elif has_frontend:
        project_type = "frontend"
    else:
        project_type = "backend"

    if has_backend:
        if len(backend_files) == 1:
            primary_api = backend_files[0].stem
        else:
            primary_api = file_path.stem if file_type == "backend" else backend_files[0].stem
        receiver = f"applications/{project_name}/{primary_api}"
    elif has_frontend:
        receiver = f"www/{project_name}/index.html"
    else:
        receiver = ""

    actions = []
    if has_backend:
        for f in backend_files:
            code = f.read_text(encoding="utf-8")
            m = re.search(r'ACTIONS_SCHEMA\s*=\s*\{', code)
            if m:
                schema_text = code[m.start():]
                action_names = re.findall(r'"(\w+)"\s*:\s*\{', schema_text)
                actions.extend(action_names)

    files = []
    if backend_dir.exists():
        files.extend(str(f) for f in backend_dir.rglob("*") if f.is_file())
    if www_dir.exists() and has_frontend:
        files.extend(str(f) for f in www_dir.rglob("*") if f.is_file())

    url = f"{base_url}/{project_name}/" if has_frontend else ""

    msg = f"✅ 修复完成：`{project_name}`\n"
    msg += f"类型：{project_type}\n"
    msg += f"影响范围：{impact}\n"
    if receiver:
        msg += f"调用路径：{receiver}\n"
    if actions:
        msg += f"支持 action：{', '.join(actions)}\n"
    if url:
        msg += f"访问地址：{url}\n"
    msg += f"\n{result}"

    _log(f"✅ fix_tool 完成: {project_name}")
    envelop.payload = {
        "ok": True,
        "data": {
            "project_name": project_name,
            "receiver": receiver,
            "type": project_type,
            "actions": actions,
            "files": files,
            "changes": [issue],
            "impact": impact,
            "url": url,
            "hot_reloaded": True,
        },
        "message": msg.strip(),
    }
    return envelop


def help():
    return {
        "route": "builtins/tools/fix_tool",
        "description": "修复已有工具/应用（支持前后端、全栈）",
        "input": {
            "target": "项目名或文件路径",
            "issue": "修复需求描述",
            "effective_doc": "有效需求文档（可选）",
            "run_mode": "bugfix_only / bugfix_with_feature（可选）",
            "contract_snapshot": "契约快照（可选）"
        },
        "output": {
            "ok": "是否成功",
            "data.project_name": "项目名",
            "data.receiver": "完整调用路径（后续用这个调）",
            "data.type": "backend / frontend / fullstack",
            "data.actions": "支持的 action 列表",
            "data.files": "项目文件列表",
            "data.changes": "本次修改内容",
            "data.impact": "影响范围（backend_only / frontend_only / both）",
            "data.url": "前端访问地址（如有）",
            "data.hot_reloaded": "是否已热重载生效",
            "message": "结果消息"
        }
    }