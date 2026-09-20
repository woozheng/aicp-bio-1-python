"""
AICP 统一生成器 — 唯一的代码生成入口
合并 dev_agent + studio/engine/generator 的所有生成逻辑
【重大改造】UI(_ui.py)只用于提取html_content，丢弃ui.py源码，不落地、不execute；html输出到generated_html交给上层拼装libs直接写index.html
"""
import json
import re
import asyncio
from pathlib import Path
import core
from plugins.builtins.engine.protocol import PROTOCOL
from plugins.builtins.engine.contracts import extract_output_fields
from plugins.builtins.engine.failure_learner import get_active_warnings
from plugins.builtins.notify.ws_notify import push as ws_push
LIBS_DIR = Path(__file__).parent / "libs"

# ============================================================
# LLM 输出清洗与质量校验
# ============================================================

_THINK_TAGS = ("think", "thinking", "reasoning", "reflection", "analysis", "internal")


def strip_think_tags(raw: str) -> str:
    """
    剥掉所有已知的思考标签。
    只剥"成对闭合"的标签，不剥"未闭合到文末"的标签。
    原因：HTML 里可能恰好包含类似 <analysis> 的字符串，
         如果按"未闭合全删"，会误伤正常 HTML。
    """
    if not raw:
        return ""
    text = raw
    for tag in _THINK_TAGS:
        # 只处理成对闭合的标签
        text = re.sub(
            rf'<{tag}\b[^>]*>.*?</{tag}>',
            '',
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return text


def strip_markdown_wrapper(raw: str) -> str:
    """剥掉 markdown 代码块包装"""
    if not raw:
        return ""
    text = raw.strip()
    for prefix in ("```html", "```javascript", "```js", "```python", "```py", "```json", "```", "~~~html", "~~~javascript", "~~~js", "~~~python", "~~~py", "~~~json", "~~~"):
        if text.startswith(prefix):
            text = text[len(prefix):].lstrip("\n")
            break
    for suffix in ("```", "~~~"):
        if text.endswith(suffix):
            text = text[:-len(suffix)].rstrip()
            break
    return text.strip()


def clean_llm_output(raw: str) -> str:
    """
    统一的 LLM 输出清洗：
    1. 剥思考标签
    2. 剥 markdown 包装
    3. 剥前言（找第一个 HTML/Python 起始标记）
    4. 剥后记（找最后一个结束标记）
    """
    if not raw:
        return ""
    text = strip_think_tags(raw)
    text = text.strip()

    # 找第一个看起来像"实际内容"的起始位置
    starts = []
    for marker in ("<!DOCTYPE html", "<!doctype html", "<html", "async def execute", "def execute", "=== PLUGIN:"):
        idx = text.lower().find(marker.lower())
        if idx >= 0:
            starts.append(idx)
    if starts:
        text = text[min(starts):]

    # 找最后一个看起来像"实际内容"的结束位置
    ends = []
    for marker in ("</html>", "=== END ==="):
        idx = text.rfind(marker)
        if idx >= 0:
            ends.append(idx + len(marker))
    if ends:
        text = text[:max(ends)]

    text = strip_markdown_wrapper(text)
    return text.strip()


_HTML_PLACEHOLDERS = (
    "<!-- Content -->",
    "<!-- content -->",
    "<!-- 内容 -->",
    "<!-- 你的代码 -->",
    "/* Styles here */",
    "/* styles here */",
    "/* 样式 */",
    "// JS here",
    "// js here",
    "// JavaScript here",
    "// your code here",
    "// 你的代码",
    "// TODO",
    "// todo",
)

def validate_html_quality(html: str) -> tuple[bool, str]:
    """
    校验 HTML 是否合格。
    返回 (是否通过, 原因)
    """
    if not html:
        return False, "empty output"

    size = len(html)
    if size < 2000:
        return False, f"too short ({size} bytes, need >= 2000)"

    lower = html.lower()
    if "<!doctype" not in lower:
        return False, "missing <!DOCTYPE>"
    if "<html" not in lower:
        return False, "missing <html>"
    if "</html>" not in lower:
        return False, "missing </html>"

    for ph in _HTML_PLACEHOLDERS:
        if ph in html:
            return False, f"contains placeholder: {ph}"

    # 至少有一个 <script> 或 <style>
    if "<script" not in lower and "<style" not in lower:
        return False, "no <script> or <style> block"

    return True, "ok"


def validate_python_quality(code: str) -> tuple[bool, str]:
    """校验生成的 Python 插件代码是否合格"""
    if not code:
        return False, "empty output"
    if len(code) < 300:
        return False, f"too short ({len(code)} bytes)"
    if "async def execute" not in code and "def execute" not in code:
        return False, "missing execute() function"
  
    for ph in ("# your code here", "# TODO", "# todo", "pass  # TODO"):
        if ph in code:
            return False, f"contains placeholder: {ph}"
    return True, "ok"
# ============================================================
# JS 库管理（仅用于prompt提示LLM，实际注入由上层调度完成）
# ============================================================

def _load_libs():
    """扫描 libs/ 目录，加载所有 .js 文件，返回拼接后的代码（仅调试查看，生成阶段不运行）"""
    if not LIBS_DIR.exists():
        return ""

    code_parts = []
    for f in sorted(LIBS_DIR.glob("*.js")):
        content = f.read_text(encoding="utf-8").strip()
        if content:
            code_parts.append(f"// ===== {f.stem} =====\n{content}")

    return "\n\n".join(code_parts)





# ============================================================
# 主入口
# ============================================================

async def execute(envelop, agent):
    """统一生成器入口"""
    action = envelop.payload.get("action", "generate")

    if action == "design":
        return await design_architecture(envelop, agent)
    elif action == "generate":
        return await generate_plugin(envelop, agent)
    elif action == "fix":
        return await fix_plugin(envelop, agent)
    elif action == "generate_full":
        return await generate_full(envelop, agent)
    else:
        envelop.payload = {"ok": False, "error": f"Unknown action: {action}"}
        return envelop


# ============================================================
# 架构设计
# ============================================================

async def design_architecture(envelop, agent) -> dict:
    """分析需求，输出架构设计方案"""
    document = envelop.payload.get("document", {})
    task = json.dumps(document, ensure_ascii=False)

    llm = agent.llm
    if not llm:
        envelop.payload = {"ok": False, "error": "LLM 不可用"}
        return envelop

    warnings = get_active_warnings(limit=5)
    warning_section = f"\n\n{warnings}\n" if warnings else ""

    prompt = f"""分析以下任务，输出完整设计方案 JSON。

任务：{task}
═══════════════════════════════════════
判断规则：什么时候生成 UI 插件
═══════════════════════════════════════
以下情况禁止生成 UI：
- 用户需求中没有明确提到“页面、界面、前端、可视化、展示、表格、图表、表单、点击、按钮”
- 用户说的是“创建工具”、“写一个插件”、“提供 API”、“后台服务”
- 用户需求是“任务管理系统”但只描述了 API 功能，没有提界面

不确定时：禁止生成 UI。只生成 API 插件。
═══════════════════════════════════════
架构约束
═══════════════════════════════════════
- 所有 Plugin 必须符合 AICP 标准签名: async def execute(envelop, agent)
- 禁止使用任何 Web 框架，禁止启动 HTTP 服务器
- 涉及 Web 界面必须拆成 API + UI Plugin
- 如果需求或者需求的功能点超过10个，必须拆成相互独立的多个 API Plugin，如果有共享数据，需要输出共享数据结构
命名规则：
- 拆出来的插件名：{{核心名词}}_{{功能}}_api.py
- 例如：task_crud_api.py、task_stats_api.py、task_export_api.py
═══════════════════════════════════════
复杂度判断标准
═══════════════════════════════════════
complexity=0 — 纯前端应用（游戏、工具、可视化），只需1个UI Plugin，不需要API
complexity=1 — 单一操作，1个Plugin（纯后端，无Web界面）
complexity=2 — 有Web界面（API+UI），或有2‑3个独立步骤
complexity=3 — 多API或复杂流水线（3+独立模块）

★ 关键判断：能用前端解决的就不需要后端
  - 迷宫生成、碰撞检测、计时器、游戏逻辑 → complexity=0
  - 翻译、AI处理、图片识别、数据存储 → complexity≥2

═══════════════════════════════════════
输出格式
═══════════════════════════════════════
{{
  "complexity": 0,
  "reason": "纯前端游戏",
  "plugins": [
    {{"name": "game_ui.py", "description": "游戏前端UI插件", "input": {{}}, "output": {{"html":"完整网页字符串"}}, "estimated_lines": 300}}
  ]
}}

规则：
- complexity=0 时只生成1个 UI Plugin，不生成 API
- 插件名带 .py 后缀，UI插件命名后缀 _ui.py
- UI插件输出字段固定携带 html，代表完整网页文本
- 涉及 Web 必须至少 1 个 API + 1 个 UI（complexity=0 除外）
- 只输出 JSON
{warning_section}"""

    raw = await llm.chat([
        {"role": "system", "content": PROTOCOL},
        {"role": "user", "content": prompt},
    ],role="code" )

    try:
        result = _parse_json(raw)
        complexity = min(max(result.get("complexity", 0), 0), 3)
        plugins = result.get("plugins", [])
        if not plugins:
            plugins = [{"name": "main.py", "description": task, "input": {}, "output": {}, "estimated_lines": 200}]

        has_ui = any("_ui" in p.get("name", "") for p in plugins)
        task_lower = task.lower()
        needs_web = any(kw in task_lower for kw in ["界面", "可视化", "web", "前端", "页面", "html"])

        if needs_web and not has_ui and complexity > 0:
            api_name = next((p["name"] for p in plugins if "_api" in p.get("name", "")), plugins[0]["name"])
            ui_name = api_name.replace("_api.py", "_ui.py")
            if ui_name == api_name:
                ui_name = "app_ui.py"
            plugins.append({
                "name": ui_name,
                "description": "前端UI插件，输出html字段，不操作磁盘，该插件仅用于提取html字符串，不会落地保存",
                "input": {},
                "output": {"html": "完整HTML网页字符串"},
                "estimated_lines": 200,
                "key_modules": [],
            })
            complexity = max(complexity, 2)

        envelop.payload = {
            "ok": True, "complexity": complexity,
            "reason": result.get("reason", ""),
            "pipeline": result.get("pipeline", {}),
            "plugins": plugins,
            "workflow": result.get("workflow", True),
            "schedule": result.get("schedule", False),
        }
        return envelop
    except Exception as e:
        envelop.payload = {
            "ok": True, "complexity": 1, "reason": f"分析失败: {e}",
            "pipeline": {},
            "plugins": [{"name": "main.py", "description": task, "input": {}, "output": {}}],
            "workflow": True, "schedule": False,
        }
        return envelop


# ============================================================
# 2. generate_plugin — 把 api_context 传给 _build_context
# ============================================================

async def generate_plugin(envelop, agent) -> dict:
    """生成单个 Plugin 代码，带自动语法检查与修复重试"""
    task = envelop.payload.get("task", "")
    plugin_spec = envelop.payload.get("plugin_spec", {})
    upstream_spec = envelop.payload.get("upstream_spec", {})
    pipeline = envelop.payload.get("pipeline", {})
    plugin_name = plugin_spec.get("name", "main.py")
    api_context = envelop.payload.get("api_context", "")
    api_sample = envelop.payload.get("api_sample")
    is_ui = plugin_name.endswith("_ui.py")
    session_id = envelop.payload.get("session_id", "default")
    if not task:
        envelop.payload = {"ok": False, "error": "缺少 task"}
        return envelop

    llm = agent.llm
    if not llm:
        envelop.payload = {"ok": False, "error": "LLM 不可用"}
        return envelop

    warnings = get_active_warnings(limit=5)
    system_prefix = PROTOCOL + (f"\n\n{warnings}" if warnings else "")

    # ★ 把 api_context 传给 _build_context
    context = _build_context(task, plugin_name, plugin_spec, upstream_spec, pipeline, api_context)

    if not upstream_spec:
        context = "⚠️ 这是唯一的Plugin，必须完成需求中的全部步骤。\n\n" + context

    if api_sample:
        sample = api_sample.get("sample", {})
        routes = api_sample.get("routes", [])
        context += "\n\n【API 返回示例 — 前端字段必须严格对齐】\n"
        context += json.dumps(sample, ensure_ascii=False, indent=2)
        if routes:
            context += "\n\n【API 路由表】\n"
            for r in routes:
                context += f"  {r.get('method','POST')} action={r.get('path')}\n"

    result = await _generate_with_retry(
        agent, system_prefix, context, plugin_name, is_ui, max_retries=3,
        session_id=session_id
    )

    code = result["code"]
    code = process_plugin_output(plugin_name, code)

    envelop.payload = {
        "ok": True,
        "plugin_name": plugin_name,
        "code": code,
        "output_fields": extract_output_fields(code),
        "retries": result["retries"],
        "syntax_ok": result.get("syntax_ok", True)
    }
    return envelop

def _extract_code_from_markdown(raw: str, plugin_name: str) -> str:
    """从 markdown 代码块中提取 Python 代码"""
    # 尝试 ```python ... ```
    match = re.search(r'```python\s*\n(.*?)```', raw, re.DOTALL)
    if match:
        code = match.group(1).strip()
        if 'async def execute' in code or 'def execute' in code:
            return code
    
    # 尝试 ~~~python ... ~~~
    match = re.search(r'~~~python\s*\n(.*?)~~~', raw, re.DOTALL)
    if match:
        code = match.group(1).strip()
        if 'async def execute' in code or 'def execute' in code:
            return code
    
    # 尝试 ``` ... ```（无语言标记）
    match = re.search(r'```\s*\n(.*?)```', raw, re.DOTALL)
    if match:
        code = match.group(1).strip()
        if 'async def execute' in code or 'def execute' in code:
            return code
    
    return ""

async def _generate_with_retry(agent, system_prefix: str, context: str,
                                plugin_name: str, is_ui: bool, max_retries: int = 3,
                                session_id: str = "default") -> dict:
    """生成插件代码，带自动重试和语法修复，支持流式输出"""
    from plugins.builtins.notify.ws_notify import push as ws_push
    
    llm = agent.llm
    last_code = ""
    last_context = context
    channel = f"pa_{session_id}"

    async def _push_code(chunk: str):
        try:
            await ws_push(agent, channel, {
                "type": "code_stream", "chunk": chunk, "plugin": plugin_name
            })
        except Exception:
            pass

    async def _push_thinking(text: str):
        try:
            await ws_push(agent, channel, {
                "type": "thinking", "content": text, "plugin": plugin_name
            })
        except Exception:
            pass

    for attempt in range(max_retries):
        await _push_thinking(f"正在生成 {plugin_name} (第{attempt+1}次)...")
        
        raw = ""
        try:
            async for token in llm.chat_stream(
    [
        {"role": "system", "content": system_prefix},
        {"role": "user", "content": last_context},
    ],
    role="code"  # ← 加这里
):
                raw += token
                await _push_code(token)
        except Exception:
            raw = await llm.chat([
                {"role": "system", "content": system_prefix},
                {"role": "user", "content": last_context},
            ],role="code" )
            await _push_code(raw)
        
        if is_ui:
            code = raw.strip()
            if code.startswith("```html"): code = code[7:]
            elif code.startswith("```javascript"): code = code[13:]
            elif code.startswith("```js"): code = code[5:]
            elif code.startswith("```"): code = code[3:]
            if code.endswith("```"): code = code[:-3]
            code = code.strip()
        else:
            code = _extract_plugin_code(raw, plugin_name)
            if not code:
                code = _extract_code_from_markdown(raw, plugin_name)
            if not code:
                await _push_thinking(f"未提取到代码块，重试提取...")
                raw2 = await llm.chat([
                    {"role": "system", "content": system_prefix},
                    {"role": "user", "content": f"上次没有生成有效的 === PLUGIN: {plugin_name} === 块。请只输出该块，不要输出任何解释文字，末尾必须有 === END ===。"},
                ],role="code" )
                code = _extract_plugin_code(raw2, plugin_name)
                if not code:
                    code = _extract_code_from_markdown(raw2, plugin_name)
            if not code:
                code = _clean_code(raw)

        if not code:
            print(f"[generator] ⚠️ {plugin_name} 第{attempt+1}次生成失败，代码为空")
            await _push_thinking(f"⚠️ 第{attempt+1}次生成失败，代码为空")
            continue

        last_code = code

        if is_ui:
            await _push_thinking(f"✅ {plugin_name} 生成完成")
            return {"code": code, "retries": attempt + 1, "ok": True, "syntax_ok": True}

        valid, errors = _validate_python_syntax(code)
        if valid:
            # 二次质量校验
            q_ok, q_reason = validate_python_quality(code)
            if not q_ok:
                print(f"[generator] ⚠️ {plugin_name} 质量不合格: {q_reason}")
                await _push_thinking(f"⚠️ 质量不合格: {q_reason}，重试中...")
                last_context = context + f"\n\n【上一次生成的代码质量不合格：{q_reason}】\n请重新生成完整代码。"
                continue

            await _push_thinking(f"✅ {plugin_name} 语法检查通过")
            return {"code": code, "retries": attempt + 1, "ok": True, "syntax_ok": True}

        print(f"[generator] ⚠️ {plugin_name} 语法错误 (第{attempt+1}次): {errors}")
        await _push_thinking(f"⚠️ 语法错误: {'; '.join(errors)}")
        
        fixed_code = _auto_fix_python_syntax(code)
        valid_fixed, errors_fixed = _validate_python_syntax(fixed_code)

        if valid_fixed:
            print(f"[generator] ✅ 自动修复成功 (第{attempt+1}次)")
            await _push_thinking(f"✅ 自动修复成功")
            return {"code": fixed_code, "retries": attempt + 1, "ok": True, "syntax_ok": True}

        print(f"[generator] 🔄 自动修复失败，让 LLM 重试 (第{attempt+1}次)")
        await _push_thinking(f"🔄 自动修复失败，重试中...")
        last_context = context + f"\n\n【上一次生成的代码有语法错误，请修复】\n错误: {'; '.join(errors)}\n请重新生成完整代码，确保语法正确。"

    fixed = _auto_fix_python_syntax(last_code)
    print(f"[generator] ❌ {plugin_name} 全部{max_retries}次重试失败，返回修复后的代码")
    await _push_thinking(f"❌ 全部{max_retries}次重试失败")
    return {"code": fixed, "retries": max_retries, "ok": False, "syntax_ok": False}


# ============================================================
# 完整生成（一把梭）【核心改造点】
# ============================================================
async def _continue_html(agent, partial_html: str, system_prefix: str, 
                          api_context: str, channel: str) -> str:
    """HTML 被截断时，让 LLM 从断点续写到 </html>"""
    
    # 取最后 2000 字符作为上下文
    tail = partial_html[-2000:] if len(partial_html) > 2000 else partial_html
    
    prompt = f"""{system_prefix}

{api_context}

【HTML 被截断了，请从断点续写到 </html>】

以下是已生成的 HTML 末尾部分，请从断点接着写，不要重复已有内容：

{tail}

【续写规则】
1. 从上面的断点继续写，不要重复已有内容
2. 不要再输出 <!DOCTYPE html>、<html>、<head> 等已经存在的标签
3. 如果正在某个函数体内，继续写完该函数
4. 写完所有剩余内容后，依次闭合 </script>、</body>、</html>
5. 直接输出续写内容，不要用 html_content = '''...''' 包裹

只输出从断点开始的续写内容。"""
    
    try:
        raw = await agent.llm.chat([{"role": "user", "content": prompt}],role="code" )
    except Exception as e:
        print(f"[GENERATE_FULL] ❌ 续写失败: {e}")
        return partial_html  # 续写失败就返回原文
    
    if not raw:
        print(f"[GENERATE_FULL] ❌ 续写返回为空")
        return partial_html
    
    continuation = raw.strip() if isinstance(raw, str) else str(raw).strip()
    
    # 清理可能的代码块标记
    for prefix in ["```html", "```javascript", "```js", "```"]:
        if continuation.startswith(prefix):
            continuation = continuation[len(prefix):]
    for suffix in ["```", "~~~"]:
        if continuation.endswith(suffix):
            continuation = continuation[:-len(suffix)]
    continuation = continuation.strip()
    
    # 移除可能的 html_content 包裹
    html_match = re.search(r"""html_content\s*=\s*['"]{3}([\s\S]*?)['"]{3}""", continuation, re.DOTALL)
    if html_match:
        continuation = html_match.group(1).strip()
    
    return partial_html + continuation

def _extract_html_content_from_ui_plugin(code: str) -> str | None:
    """
    从 UI 插件源码中提取 html_content 字符串。
    支持以下写法：

    """
    import re
    print("[EXTRACT_HTML] enter, total code len:", len(code))

    # 允许 html_content = 与三引号之间出现 dedent(...)、textwrap.dedent(...) 等任意前缀
    # 关键：把 dedent( 当作"透明的可选项"，不参与捕获
    _PREFIX = r'(?:[a-zA-Z_\.]*dedent\s*\(|\s*)*'

    # 模式1: '''...'''（完整闭合）
    pat1 = re.compile(
        rf"html_content\s*=\s*{_PREFIX}r?'''([\s\S]*?)'''",
        re.DOTALL,
    )
    m1 = pat1.search(code)
    if m1:
        res = m1.group(1).strip()
        print("[EXTRACT_HTML] match ''' success, len", len(res))
        return res

    # 模式2: """..."""（完整闭合）
    pat2 = re.compile(
        rf'html_content\s*=\s*{_PREFIX}r?"""([\s\S]*?)"""',
        re.DOTALL,
    )
    m2 = pat2.search(code)
    if m2:
        res = m2.group(1).strip()
        print('[EXTRACT_HTML] match """ success, len', len(res))
        return res

    # 模式3: 三引号任意类型（完整闭合，兜底）
    pat_fallback = re.compile(
        rf'html_content\s*=\s*{_PREFIX}r?(["\']{{3}})([\s\S]*?)\1',
        re.DOTALL,
    )
    mf = pat_fallback.search(code)
    if mf:
        res = mf.group(2).strip()
        print("[EXTRACT_HTML] fallback match success, len", len(res))
        return res

    # 模式4: 三引号未闭合（被截断），取 html_content = ''' 之后到末尾
    pat_open = re.compile(
        rf"html_content\s*=\s*{_PREFIX}r?'''([\s\S]*?)$",
        re.DOTALL,
    )
    mo = pat_open.search(code)
    if mo:
        res = mo.group(1).strip()
        # 只在内容"看起来像 HTML 的开头"时才认为是截断
        if res.startswith("<!DOCTYPE") or res.startswith("<html"):
            # 再检查：如果里面已经出现 === END ===，说明这是抓多了，不是截断
            if "=== END ===" in res:
                res = res.split("=== END ===")[0].strip()
            print(f"[EXTRACT_HTML] ⚠️ 三引号未闭合（被截断），提取了 {len(res)} 字符")
            return res

    pat_open2 = re.compile(
        rf'html_content\s*=\s*{_PREFIX}r?"""([\s\S]*?)$',
        re.DOTALL,
    )
    mo2 = pat_open2.search(code)
    if mo2:
        res = mo2.group(1).strip()
        if res.startswith("<!DOCTYPE") or res.startswith("<html"):
            if "=== END ===" in res:
                res = res.split("=== END ===")[0].strip()
            print(f"[EXTRACT_HTML] ⚠️ 三引号未闭合（被截断），提取了 {len(res)} 字符")
            return res

    # 模式5: 最后兜底——直接搜 <!DOCTYPE html> 到 </html>
    # 注意：这里必须抓到 </html> 为止，不能抓到文末，否则会把 === END === 也带进来
    pat_html = re.compile(
        r'(<!DOCTYPE html>[\s\S]*?</html>)',
        re.IGNORECASE,
    )
    mh = pat_html.search(code)
    if mh:
        res = mh.group(1).strip()
        print(f"[EXTRACT_HTML] ⚠️ 直接提取 HTML（到 </html> 截止），长度 {len(res)}")
        return res

    # 模式6: 更宽松——直接搜 <!DOCTYPE 到文末，然后裁到最后一个 </html>
    pat_html2 = re.compile(
        r'(<!DOCTYPE html>[\s\S]*)$',
        re.IGNORECASE,
    )
    mh2 = pat_html2.search(code)
    if mh2:
        res = mh2.group(1).strip()
        # 裁到最后一个 </html>
        last_close = res.lower().rfind("</html>")
        if last_close >= 0:
            res = res[: last_close + len("</html>")]
            print(f"[EXTRACT_HTML] ⚠️ 宽松提取 HTML（裁到 </html>），长度 {len(res)}")
            return res
        else:
            print(f"[EXTRACT_HTML] ⚠️ 宽松提取 HTML（无 </html>，可能真的截断），长度 {len(res)}")
            return res

    print("[EXTRACT_HTML] ALL PATTERN MISS, return None")
    return None

async def generate_full(envelop, agent) -> dict:
    print(f"[GENERATE_FULL] envelop.payload keys: {list(envelop.payload.keys())}")
    print(f"[GENERATE_FULL] session_id from payload: {envelop.payload.get('session_id', 'NOT FOUND')}")
    document = envelop.payload.get("document", {})
    task_id = envelop.payload.get("task_id", "default")
    session_id = envelop.payload.get("session_id", "default")
    channel = f"pa_{session_id}"

    async def _push(step, msg):
        await ws_push(agent, channel, {"type": "progress", "step": step, "msg": msg, "message": msg})

    print(f"[GENERATE_FULL][START] task_id={task_id}, session_id={session_id}")
    await _push("design", "🧠 分析需求，设计架构...")

    design_env = core.Envelop(
        sender="builtins/generator", receiver="builtins/generator",
        payload={"action": "design", "document": document, "task_id": task_id},
    )
    print("[GENERATE_FULL] 调用 design_architecture 开始")
    design_result = await design_architecture(design_env, agent)
    print(f"[GENERATE_FULL] design_architecture 返回 ok={design_result.payload.get('ok')}")
    if not design_result.payload.get("ok"):
        print(f"[GENERATE_FULL] 架构设计失败 payload={design_result.payload}")
        return design_result

    plugins = design_result.payload.get("plugins", [])
    pipeline = design_result.payload.get("pipeline", {})
    complexity = design_result.payload.get("complexity", 0)
    
    print(f"[GENERATE_FULL] 架构完成，complexity={complexity}, plugins数量={len(plugins)}")
    for idx, plg in enumerate(plugins):
        print(f"[GENERATE_FULL] plugin[{idx}] name={plg.get('name')}")

    task = json.dumps(document, ensure_ascii=False)

    # ============================================================
    # ★★★ 纯前端快捷分支（complexity == 0）— 直接生成 HTML ★★★
    # ============================================================
    if complexity == 0:
        print(f"[GENERATE_FULL] 🚀 检测到纯前端应用，直接生成 HTML...")
        await _push("frontend", "🎨 检测到纯前端应用，直接生成页面...")

        # ============================================================
        # 项目名：优先从用户需求提取，禁止用 "main"/"app" 这种通用名
        # ============================================================
        project_name = ""
        for p in plugins:
            name = p.get("name", "")
            if "_ui" in name:
                candidate = name.replace("_ui.py", "").replace(".py", "")
                if candidate and candidate not in ("main", "app", "index", "test"):
                    project_name = candidate
                    break
        if not project_name:
            # 从 task 里提取英文/拼音项目名
            # 简单策略：取 task 里第一段英文单词或拼音
            m = re.search(r'[a-zA-Z][a-zA-Z0-9\-_]{2,30}', task)
            if m:
                project_name = m.group(0).lower()
            else:
                project_name = "app-" + session_id[:6]
        # 清洗项目名
        project_name = re.sub(r'[^a-zA-Z0-9\-_]', '', project_name)
        if not project_name:
            project_name = "app-" + session_id[:6]

        print(f"[GENERATE_FULL] 项目名: {project_name}")

        # ============================================================
        # 生成 HTML（带质量校验和重试）
        # ============================================================
        base_prompt = f"""生成一个完整的 HTML 文件。

需求：{task}

【铁律 — 违反将导致失败】
1. 你的第一行输出必须是 <!DOCTYPE html>，最后一行必须是 </html>
2. 禁止输出任何解释、设计过程、清单、计划、思考标签（<think>等）
3. 禁止用代码块标记（```html 或 ~~~html）
4. 禁止在 HTML 内留占位符（如 <!-- Content -->、/* Styles here */、// your code here）
5. 20 条名言/数据必须直接写在 JS 数组里，不要列在 HTML 外面
6. 用 var 声明变量，用 function 定义函数
7. 禁止箭头函数、const/let、模板字符串
8. 变量名避开: history, location, name, status
9. 响应式布局，移动端友好
10. 结果展示后要有"重新操作"按钮

只输出 HTML。从 <!DOCTYPE html> 到 </html>。"""

        max_attempts = 3
        html = ""
        last_reason = ""
        success = False

        for attempt in range(max_attempts):
            attempt_prompt = base_prompt
            if attempt > 0:
                attempt_prompt = base_prompt + f"\n\n【上一次生成失败，原因：{last_reason}】\n请重新生成，严格遵守所有铁律。"

            raw = ""
            buffer = ""
            last_push = 0.0
            try:
                async for token in agent.llm.chat_stream(
                    [{"role": "user", "content": attempt_prompt}],
                    role="code",
                ):
                    raw += token
                    buffer += token
                    import time as _time
                    now = _time.time()
                    if now - last_push > 0.3 or len(buffer) > 50:
                        try:
                            await ws_push(agent, channel, {
                                "type": "code_stream",
                                "chunk": buffer,
                                "plugin": f"{project_name}.html",
                            })
                        except Exception:
                            pass
                        buffer = ""
                        last_push = now
                if buffer:
                    try:
                        await ws_push(agent, channel, {
                            "type": "code_stream",
                            "chunk": buffer,
                            "plugin": f"{project_name}.html",
                        })
                    except Exception:
                        pass
            except Exception as e:
                print(f"[GENERATE_FULL] 流式生成失败: {e}，尝试非流式...")
                try:
                    raw = await agent.llm.chat(
                        [{"role": "user", "content": attempt_prompt}],
                        role="code",
                    )
                except Exception as e2:
                    print(f"[GENERATE_FULL] 非流式也失败: {e2}")
                    raw = ""

            print(f"[GENERATE_FULL] 第{attempt+1}次生成完成，原始长度: {len(raw)}")

            # 清洗
            cleaned = clean_llm_output(raw)

            # 提取 HTML（在清洗后的文本里找）
            html_match = re.search(r'(<!DOCTYPE html>[\s\S]*?</html>)', cleaned, re.IGNORECASE)
            if html_match:
                html = html_match.group(1)
            else:
                # 尝试宽松提取：从 <!DOCTYPE 到末尾
                loose = re.search(r'(<!DOCTYPE html>[\s\S]*)$', cleaned, re.IGNORECASE)
                if loose:
                    html = loose.group(1)
                else:
                    html = cleaned

            # 质量校验
            ok, reason = validate_html_quality(html)
            if ok:
                success = True
                last_reason = ""
                print(f"[GENERATE_FULL] ✅ 第{attempt+1}次生成通过质量校验，长度={len(html)}")
                break
            else:
                last_reason = reason
                print(f"[GENERATE_FULL] ⚠️ 第{attempt+1}次生成不合格: {reason}")
                await _push("frontend", f"⚠️ 第{attempt+1}次生成不合格: {reason}，重试中...")

        # ============================================================
        # 落盘（只有通过校验才落）
        # ============================================================
        if not success:
            print(f"[GENERATE_FULL] ❌ 全部{max_attempts}次生成失败: {last_reason}")
            await _push("done", f"❌ 生成失败: {last_reason}")
            envelop.payload = {
                "ok": False,
                "all_success": False,
                "error": f"HTML 生成失败: {last_reason}",
                "generated_html": {},
            }
            return envelop

        www_dir = Path(f"www/{project_name}")
        www_dir.mkdir(parents=True, exist_ok=True)
        html_path = www_dir / "index.html"
        html_path.write_text(html, encoding="utf-8")
        print(f"[GENERATE_FULL] ✅ HTML 已落盘: {html_path} ({len(html)} 字符)")

        envelop.payload = {
            "ok": True,
            "all_success": True,
            "name": project_name,
            "url": f"/{project_name}/",
            "saved_to": str(html_path),
            "size": len(html),
            "plugins": [{"name": f"{project_name}_ui.py", "type": "frontend"}],
            "generated": [{"name": f"{project_name}.html", "ok": True}],
            "generated_html": {"index": html},
            "tool_type": "frontend",
            "message": f"✅ 纯前端应用已生成！访问 /{project_name}/",
        }
        await _push("done", f"✅ 纯前端应用已生成！访问 /{project_name}/")
        return envelop
    # ============================================================
    # 以下是 complexity > 0 的原有逻辑
    # ============================================================
    total = len(plugins)
    await _push("design", f"✅ 架构设计完成，共 {total} 个插件")

    api_plugins = [p for p in plugins if "_api" in p.get("name", "").lower()]
    ui_plugins = [p for p in plugins if p["name"].endswith("_ui.py")]
    other_plugins = [p for p in plugins if p not in api_plugins and p not in ui_plugins]

    print(f"[GENERATE_FULL] 分类：api_plugins={len(api_plugins)} ui_plugins={len(ui_plugins)} other_plugins={len(other_plugins)}")

    generated = []
    current = 0
    out_html: str | None = None

    # ============================================================
    # 生成 API/普通插件
    # ============================================================
    for plugin_spec in api_plugins + other_plugins:
        current += 1
        plugin_name = plugin_spec["name"]
        await _push("generate", f"⚡ [{current}/{total}] 生成 {plugin_name}...")
        print(f"[GENERATE_FULL] 开始生成插件 {plugin_name}")

        gen_env = core.Envelop(
            sender="builtins/generator", receiver="builtins/generator",
            payload={
                "action": "generate", "task": task, "plugin_spec": plugin_spec,
                "upstream_spec": {}, "pipeline": pipeline, "task_id": task_id,
                "api_context": "", "session_id": session_id,
            },
        )
        gen_result = await generate_plugin(gen_env, agent)
        print(f"[GENERATE_FULL] plugin={plugin_name} ok={gen_result.payload.get('ok')}")

        if gen_result.payload.get("ok"):
            code = gen_result.payload.get("code", "")
            print(f"[GENERATE_FULL] {plugin_name} 代码长度={len(code)}")
            generated.append({
                "name": plugin_name, "ok": True, "code": code,
                "output_fields": gen_result.payload.get("output_fields", []),
            })
            await _push("generate", f"✅ [{current}/{total}] {plugin_name} 完成")
        else:
            err = gen_result.payload.get("error", "生成失败")
            print(f"[GENERATE_FULL] {plugin_name} 失败 error={err}")
            generated.append({"name": plugin_name, "ok": False, "error": err})
            await _push("generate", f"❌ [{current}/{total}] {plugin_name} 失败")

    # ============================================================
    # ★ 收集所有后端 API 的 execute 函数原文，作为契约
    # ============================================================
    api_code_context = ""
    for gen in generated:
        if gen.get("ok") and "_api" in gen.get("name", "").lower():
            code = gen["code"]
            api_code_context += f"\n\n=== 插件 {gen['name']} ===\n{code}"

    real_contract = f"""
【★ 后端 API 完整源代码 — 前端必须严格按照此代码中的字段名和数据结构来调用 ★】
═══════════════════════════════════════
以下是所有后端 API 插件的完整源代码。
你必须从中提取：
1. 每个 action 的名称（action == "xxx"）
2. 每个 action 需要的参数（envelop.payload.get("xxx")）
3. 每个 action 返回的数据结构——注意追踪变量引用，找到最终 return 的字典结构
4. 所有字段名必须与代码中完全一致，禁止自己编造
5. 特别注意：envelop.payload = {{...}} 中如果有变量引用（如 "file": file_data），
   必须追踪该变量在哪里定义、包含哪些字段

{api_code_context}

═══════════════════════════════════════
【前端 JS 代码规范】
1. 读取后端返回数据时，用 result.data.字段名
2. 字段名必须与上面后端代码中的字段名完全一致
3. 嵌套对象按后端代码中的结构逐层读取
4. 如果后端返回 file.basic.total_lines，前端必须用 file.basic.total_lines
5. 禁止自己编造任何字段名或数据结构
"""

    # ============================================================
    # 生成 UI 插件
    # ============================================================
    for plugin_spec in ui_plugins:
        current += 1
        plugin_name = plugin_spec["name"]
        await _push("generate", f"⚡ [{current}/{total}] 解析UI页面 {plugin_name}...")
        print(f"[GENERATE_FULL] 处理UI插件 {plugin_name}")

        gen_env = core.Envelop(
            sender="builtins/generator", receiver="builtins/generator",
            payload={
                "action": "generate", "task": task, "plugin_spec": plugin_spec,
                "upstream_spec": {}, "pipeline": pipeline, "task_id": task_id,
                "api_context": real_contract,
                "session_id": session_id,
            },
        )
        gen_result = await generate_plugin(gen_env, agent)
        print(f"[GENERATE_FULL] UI插件返回 ok={gen_result.payload.get('ok')}")

        if gen_result.payload.get("ok"):
            ui_plugin_code = gen_result.payload.get("code", "")
            print(f"[GENERATE_FULL] ui插件源码总长度={len(ui_plugin_code)}")
            print(f"[GENERATE_FULL] ui_code preview:\n{ui_plugin_code[:800]}")

            ui_plugin_code = strip_think_tags(ui_plugin_code)
            html_text = _extract_html_content_from_ui_plugin(ui_plugin_code)
            if html_text:
                html_text = strip_think_tags(html_text)
                html_text = strip_markdown_wrapper(html_text)

                # ★ 只要不是明确缺少 </html>，就不续写
                stripped_lower = html_text.strip().lower()
                if stripped_lower.endswith("</html>"):
                    # 完整，不续写
                    pass
                elif "</body>" in stripped_lower:
                    # 有 </body> 没 </html>，只补 </html>
                    html_text = html_text.rstrip() + "\n</html>"
                else:
                    # 真的截断，走续写
                    print(f"[GENERATE_FULL] ⚠️ HTML 真的被截断（没有 </body>），正在续写...")
                    await _push("frontend", "📝 HTML 被截断，正在续写...")
                    html_text = await _continue_html(agent, html_text, PROTOCOL, real_contract, channel)

                # 质量校验
                valid, reason = validate_html_quality(html_text)
                if not valid:
                    print(f"[GENERATE_FULL] ⚠️ UI HTML 质量不合格: {reason}")
                    await _push("generate", f"⚠️ [{current}/{total}] UI HTML 质量不合格: {reason}")
                    out_html = None  # 明确失败
                else:
                    out_html = html_text
                    conflict_vars = ['history', 'location', 'name']
                    for var in conflict_vars:
                        out_html = out_html.replace(f'var {var} =', f'var _game{var.capitalize()} =')
                        out_html = out_html.replace(f'{var}.push', f'_game{var.capitalize()}.push')
                        out_html = out_html.replace(f'{var}.pop', f'_game{var.capitalize()}.pop')
                        out_html = out_html.replace(f'{var}.length', f'_game{var.capitalize()}.length')
                        out_html = out_html.replace(f'{var} = [', f'_game{var.capitalize()} = [')

                    print(f"[GENERATE_FULL] HTML提取成功，html长度={len(out_html)}")
                    await _push("generate", f"✅ [{current}/{total}] UI页面提取完成")
            else:
                print("[GENERATE_FULL] !!! HTML提取返回None，正则匹配失败 !!!")
                await _push("generate", f"⚠️ [{current}/{total}] UI无法提取html_content")
        else:
            print(f"[GENERATE_FULL] UI插件生成阶段失败 {gen_result.payload.get('error')}")
            await _push("generate", f"❌ [{current}/{total}] UI插件生成失败")

    api_ok = all(p.get("ok") for p in generated)
    ui_ok = bool(out_html)
    all_success = api_ok and (len(generated) > 0 or ui_ok)

    print(f"[GENERATE_FULL] 阶段汇总 api_ok={api_ok} ui_ok={ui_ok} all_success={all_success} generated_len={len(generated)}")
    await _push("done", f"{'✅' if all_success else '⚠️'} 代码生成完成 ({current}/{total})")

    envelop.payload = {
        "ok": True,
        "plugins": plugins,
        "generated": generated,
        "generated_html": {"index": out_html},
        "pipeline": pipeline,
        "all_success": all_success,
    }
    print(f"[GENERATE_FULL][END] 返回payload ok={envelop.payload.get('ok')}, all_success={envelop.payload.get('all_success')}")
    return envelop


def process_plugin_output(plugin_name: str, code: str) -> str:
    """不再在插件内部组装HTML外壳；HTML完整字符串由模型直接写在html_content变量"""
    return code


# ============================================================
# 3. fix_plugin — 接收 api_context，注入 prompt
# ============================================================

async def fix_plugin(envelop, agent) -> dict:
    import time as _time
    
    plugin_name = envelop.payload.get("plugin_name", "main.py")
    original_code = envelop.payload.get("code", "")
    error_msg = envelop.payload.get("error", "")
    task = envelop.payload.get("task", "")
    readme = envelop.payload.get("readme", "")
    api_context = envelop.payload.get("api_context", "")
    session_id = envelop.payload.get("session_id", "default")
    channel = f"pa_{session_id}"

    if not original_code:
        envelop.payload = {"ok": False, "error": "缺少待修复代码"}
        return envelop
    if not agent.llm:
        envelop.payload = {"ok": False, "error": "LLM不可用"}
        return envelop

    # ★ 判断文件类型：HTML 还是 Python
    is_html = (
        "<html" in original_code[:2000]
        or "<!DOCTYPE" in original_code[:2000]
        or plugin_name.endswith(".html")
        or ".htm" in plugin_name
        or "www/" in plugin_name
    )

    print(f"[fix_plugin] plugin={plugin_name}, is_html={is_html}, readme长度={len(readme)}")

    try:
        await ws_push(agent, channel, {
            "type": "progress", "step": "fix", "msg": f"🔧 修复 {plugin_name}...",
            "message": f"🔧 修复 {plugin_name}...", "plugin": plugin_name
        })
    except Exception:
        pass

    # ============================================================
    # ★ 构建 prompt（根据文件类型不同）
    # ============================================================
    if is_html:
        prompt = f"""你是代码修复机器人。只输出修复后的完整 HTML，从 <!DOCTYPE html> 到 </html>。
禁止任何对话文本、工具调用、思考过程。
⚠️ **核心原则：默认保持原有功能和样式不变！**
⚠️ **只修改与修复需求直接相关的部分！**
⚠️ **不要擅自优化、重构、或改变未提及的功能！**
⚠️ **不要改变颜色、布局、字体等样式，除非需求明确要求！**

【项目文档】
{readme[:8000] if readme else '(无)'}

【后端 API 字段（前端必须对齐）】
{api_context[:3000] if api_context else '保持与现有代码一致'}

【修复需求】
{error_msg}
{task[:2000]}

【当前代码 — 完整文件，必须全量输出】
{original_code}

【规则】
1. 输出完整 HTML（<!DOCTYPE html> 到 </html>），不要截断任何部分
2. 认真理解修改需求，只修改与修复需求相关的代码，其他代码保持不变
3. 变量名避开: history, location, name, status
4. 确保后端 API 的字段名和前端读取的字段名完全一致
5. 如果需求涉及"同步后端变更"，优先检查 API 字段是否对齐
6. 必须输出完整的 HTML 文件，从 <!DOCTYPE html> 开始，到 </html> 结束
7. 禁止输出省略号、注释说明省略、或任何形式的截断标记"""
    else:
        warnings = get_active_warnings(limit=5)
        system_prefix = PROTOCOL + (f"\n\n{warnings}" if warnings else "")
        prompt = f"""{system_prefix}

你是代码修复机器人。只输出修复后的完整代码。
禁止附带原始代码、禁止做对比、禁止输出对话文本、禁止输出思考过程。
禁止输出其他文件的代码。

⚠️ **核心原则：默认保持原有功能和逻辑不变！**
⚠️ **只修改与修复需求直接相关的部分！**
⚠️ **不要擅自优化、重构、或改变未提及的功能！**
⚠️ **不要改变函数签名、接口、返回值格式，除非需求明确要求！**

【项目文档】
{readme[:8000] if readme else '(无)'}

【修复需求 — 必须严格遵守】
{error_msg}
{task[:2000]}

【当前代码 — 完整文件，必须全量输出】
{original_code}

【重要规则】
1. **只修改与修复需求直接相关的部分**，其他代码保持原样
2. 如果需求是"增加功能"，只新增代码，不改已有逻辑
3. 如果需求是"修复 bug"，只改与 bug 相关的代码行
4. 如果需求涉及"修改接口"（action 名、参数、返回值），确保修改后接口一致
5. 输出 === PLUGIN: {plugin_name} === ... === END === 块
6. 只输出一个代码块，不要输出原始代码，不要对比，不要解释。
7. 必须输出完整的代码，不要截断、省略或使用省略号"""

    # ============================================================
    # 流式输出
    # ============================================================
    raw = ""
    buffer = ""
    last_push = 0.0
    try:
        async for token in agent.llm.chat_stream([
            {"role": "user", "content": prompt}
        ], role="code"):
            raw += token
            buffer += token
            now = _time.time()
            if now - last_push > 0.5 or len(buffer) > 80:
                try:
                    await ws_push(agent, channel, {
                        "type": "code_stream", "chunk": buffer, "plugin": plugin_name
                    })
                except Exception:
                    pass
                buffer = ""
                last_push = now
        if buffer:
            try:
                await ws_push(agent, channel, {
                    "type": "code_stream", "chunk": buffer, "plugin": plugin_name
                })
            except Exception:
                pass
    except Exception:
        resp = await agent.llm.chat([{"role": "user", "content": prompt}], role="code")
        raw = resp.content.strip() if hasattr(resp, 'content') else str(resp).strip()

    fixed = raw.strip() if isinstance(raw, str) else str(raw)
    fixed = strip_think_tags(fixed)
    fixed = strip_markdown_wrapper(fixed)
    # ============================================================
    # 清理工具调用残留
    # ============================================================
    for tag in ['tool_call', 'read', 'path', 'seed:tool_call', 'seed', 'write']:
        fixed = re.sub(rf'</?{tag}>', '', fixed)
        fixed = re.sub(rf'<{tag}>.*?</{tag}>', '', fixed, flags=re.DOTALL)
        fixed = re.sub(r'<!\[CDATA\[.*?\]\]>', '', fixed, flags=re.DOTALL)

    # ============================================================
    # 提取代码块
    # ============================================================
    if is_html:
        if '~~~html' in fixed:
            fixed = fixed.split('~~~html', 1)[1].split('~~~', 1)[0].strip()
        elif '```html' in fixed:
            fixed = fixed.split('```html', 1)[1].split('```', 1)[0].strip()
        elif fixed.startswith('~~~'):
            fixed = fixed.split('~~~', 1)[1].split('~~~', 1)[0].strip()
        elif fixed.startswith('```'):
            fixed = fixed.split('```', 1)[1].split('```', 1)[0].strip()
        fixed = fixed.strip()

        # 去重：只取第一个完整 HTML
        if fixed:
            html_match = re.search(r'(<!DOCTYPE html>[\s\S]*?</html>)', fixed, re.IGNORECASE)
            if html_match:
                fixed = html_match.group(1)
            fixed = re.sub(r'=== PLUGIN:.*?===\s*', '', fixed)
            fixed = re.sub(r'=== END ===', '', fixed)
            fixed = re.sub(r'<!-- @AICP_ALIGN:.*?-->', '', fixed)
    else:
        # ★ 精确匹配当前 plugin_name
        fixed = _extract_plugin_code(fixed, plugin_name)
        if not fixed:
            # 兜底：取第一个 PLUGIN 块，检查文件名
            match2 = re.search(r'=== PLUGIN:\s*(.+?)===\s*\n(.*?)=== END ===', raw, re.DOTALL)
            if match2:
                matched_name = match2.group(1).strip()
                if matched_name == plugin_name or plugin_name in matched_name or matched_name in plugin_name:
                    fixed = match2.group(2).strip()
                else:
                    fixed = original_code  # 文件名不匹配，保留原代码
            else:
                fixed = _extract_code_from_markdown(raw, plugin_name)
        if not fixed:
            fixed = _clean_code(raw)
        
        # ★ 去重：只保留第一个 === PLUGIN === 块
        if fixed and "=== END ===" in fixed:
            parts = fixed.split("=== END ===")
            fixed = parts[0].strip()

    if not fixed:
        fixed = original_code

    # 冲突变量替换
    for var in ['history', 'location', 'name']:
        fixed = re.sub(rf'\bvar\s+{var}\s*=', f'var _game{var.capitalize()} =', fixed)

    # 推送完整代码
    try:
        await ws_push(agent, channel, {
            "type": "code_stream", "chunk": fixed, "plugin": plugin_name
        })
        await ws_push(agent, channel, {
            "type": "progress", "step": "fix_done", "msg": f"✅ {plugin_name} 修复完成",
            "message": f"✅ {plugin_name} 修复完成", "plugin": plugin_name
        })
    except Exception:
        pass

    envelop.payload = {
        "ok": True,
        "plugin_name": plugin_name,
        "code": fixed,
        "original_code": original_code,
    }
    return envelop





# ============================================================
# 1. _build_context — 加上 api_context 参数，注入到 UI 的 prompt
# ============================================================

def _build_context(task, plugin_name, plugin_spec, upstream_spec, pipeline, api_context=""):
    """构建生成上下文"""
    spec = plugin_spec
    is_api = "_api" in plugin_name.lower()
    is_ui = plugin_name.endswith("_ui.py")
    is_scheduler = "_scheduler" in plugin_name.lower() or "scheduler" in plugin_name.lower()

    context = f"⚠️ 原始需求: {task[:600]}\n\n"
    context += f"🔥 你的职责: {spec.get('description', '实现完整功能')}\n"
    context += f"预估行数: {spec.get('estimated_lines', 200)} 行\n"
    context += f"推荐模块: {', '.join(spec.get('key_modules', [])) if spec.get('key_modules') else '根据需求自行选择'}\n\n"

    if upstream_spec:
        context += f"⚠️ 上游插件输出字段: {json.dumps(upstream_spec.get('output', {}), ensure_ascii=False)}\n"
        context += "必须用 prev.data.get(\"字段名\") 读取这些字段。\n\n"

    if pipeline:
        context += f"📋 全局视角：{pipeline.get('description', '')}\n"

    if is_api:
        context += """
【API 插件规范 — 必须严格遵守】

═══════════════════════════════════════
数据持久化
═══════════════════════════════════════
- 用 Path("data/项目名/xxx.json") 存储数据
- 首次访问自动创建：Path.mkdir(parents=True, exist_ok=True)
- 读写 JSON 要处理文件不存在、JSON 损坏的情况
- 数据操作放在函数里（load_data/save_data），不要在 execute 里直接写

═══════════════════════════════════════
字段命名
═══════════════════════════════════════
- 全部下划线：task_id, api_url, email_to, natural_time, created_at
- 禁止驼峰：taskId, apiUrl, emailTo
- 禁止缩写到看不出含义：不要用 expr 代替 expression，不要用 desc 代替 description
- 同一概念用同一个字段名，不要变来变去（不要一处叫 schedule，另一处叫 cron_expr）

═══════════════════════════════════════
返回格式
═══════════════════════════════════════
- 成功: envelop.payload = {"ok": True, "data": {...}}
- 失败: envelop.payload = {"ok": False, "error": "具体原因"}
- 列表用数组，禁止用对象当列表
- data 里的字段名用下划线

═══════════════════════════════════════
错误处理
═══════════════════════════════════════
- 每个 action 的每个可能失败点都要返回具体 error
- 禁止 try-except pass 吞掉异常
- 参数缺失: {"ok": False, "error": "缺少 xxx 参数"}
- 数据不存在: {"ok": False, "error": "xxx 不存在"}
- 未知操作: {"ok": False, "error": "未知操作: xxx"}

═══════════════════════════════════════
CRUD 标准
═══════════════════════════════════════
- create_task: 接收所有业务字段，生成唯一 id，返回完整 task 对象
- list_tasks: 返回 {"tasks": [...]}，如果为空返回空数组
- update_task: 只更新传入的字段，没传的字段保持不变
- delete_task: 用 task_id 定位，删除后返回成功
- 所有增删改操作后必须 save_data() 持久化

═══════════════════════════════════════
代码质量
═══════════════════════════════════════
- 函数内第一行 import 所有模块
- 输出代码之前确保能 100% 运行无 bug
- 不要用类包装（不需要 Task 类、TaskManager 类），用简单的 dict + 函数即可
"""
        context += f"\n现在生成 === PLUGIN: {plugin_name} === 块，末尾必须有 === END ===。\n"

    elif is_scheduler:
        context += f"""
【调度插件规范 — 必须严格遵守】

1. 不要重复实现 CRUD！通过 agent.system.call 调 API 插件来操作数据
   示例：
   result = await agent.system.call(core.Envelop(
       sender="applications/{{PROJECT}}/_scheduler",
       receiver="applications/{{PROJECT}}/xxx_api",
       payload={{"action": "list_tasks"}}
   ))
   tasks = result.payload.get("data", {{}}).get("tasks", [])

2. 只负责定时循环和触发，不直接读写数据文件
3. 用 asyncio.sleep() 做循环间隔，不要用 time.sleep()
4. 检查到到期任务时，调 API 插件的 run_task 或相应 action
5. 后台循环在 execute() 里用 asyncio.create_task() 启动

现在生成 === PLUGIN: {plugin_name} === 块，末尾必须有 === END ===。
"""

    if is_ui:
        context += """
【UI‑PLUGIN 强制铁律，必须100%遵守】
1. 保留标准插件签名：async def execute(envelop, agent):
2. ❌ 严禁 import Path、Path()、mkdir、write_text、任何磁盘文件读写代码。
3. 完整网页源码全部写在字符串变量 html_content 中，包含 <!DOCTYPE html> 至 </html>。

★★★ 变量命名强制规范 — 违反将导致页面报错 ★★★
以下名称是 window 全局属性，禁止以任何形式使用它们作为变量名：
  name, history, location, status, top, parent, self, open, close, length, frames, navigator

禁止：
  ❌ var name = ...           ❌ name.textContent = ...
  ❌ let name = ...           ❌ name.className = ...
  ❌ const name = ...         ❌ name.innerHTML = ...
  ❌ function name() {...}    ❌ 任何对 name 的赋值或属性操作

正确做法：
  ✅ var fileName = node.name       // 用不同的变量名
  ✅ var scanName = document...     // 加前缀
  ✅ var pathName = location...     // 从全局变量取值后立即存入新变量

如果你不确定一个词是不是全局变量，就加前缀：var _myXxx = ...

4. 返回固定格式：
envelop.payload = {
    "ok": True,
    "html": html_content
}
return envelop
⚠️ 重要：该ui插件仅用于提取html_content字符串，**不会被保存到磁盘、不会被runtime执行execute**。
磁盘保存、预览、下载全部交给上层调用系统统一处理。
"""
        if api_context:
            context += api_context

        context += f"\n现在生成 === PLUGIN: {plugin_name} === 块，末尾必须有 === END ===。"

    if not is_ui and not is_api and not is_scheduler:
        context += f"\n现在生成 === PLUGIN: {plugin_name} === 块，末尾必须有 === END ===。"

    return context


def _extract_api_summary(code: str, plugin_name: str) -> dict:
    actions = []
    actions.extend(re.findall(r'if action\s*==\s*["\']([^"\']+)["\']', code))
    actions.extend(re.findall(r'elif action\s*==\s*["\']([^"\']+)["\']', code))

    # ★ 按 action 分组提取字段
    action_fields = {}
    current_action = None
    for line in code.split('\n'):
        action_match = re.search(r'if action\s*==\s*["\']([^"\']+)["\']', line)
        if action_match:
            current_action = action_match.group(1)
            if current_action not in action_fields:
                action_fields[current_action] = []
        elif current_action:
            for m in re.finditer(r'envelop\.payload\.get\("(\w+)"', line):
                field = m.group(1)
                if field not in action_fields[current_action]:
                    action_fields[current_action].append(field)

    # ★ 提取 task 对象的结构（从 create_task 的赋值语句推断）
    task_fields = []
    in_create_task = False
    for line in code.split('\n'):
        if 'action == "create_task"' in line:
            in_create_task = True
        elif in_create_task and re.search(r'if action\s*==\s*["\']', line) and 'create_task' not in line:
            in_create_task = False
        elif in_create_task:
            # 匹配 "字段名": envelop.payload.get(...)
            m = re.search(r'"(\w+)"\s*:\s*envelop\.payload\.get', line)
            if m:
                task_fields.append(m.group(1))
            # 匹配 "字段名": str(...) 等自生成字段
            m = re.search(r'"(\w+)"\s*:\s*str\(', line)
            if m and m.group(1) not in task_fields:
                task_fields.append(m.group(1))

    # 全局字段+默认值
    all_inputs = []
    for m in re.finditer(r'envelop\.payload\.get\("(\w+)"\s*,\s*([^)]+)\)', code):
        field = m.group(1)
        default = m.group(2).strip()
        if default.startswith('"') or default.startswith("'"):
            default = default[1:-1]
        all_inputs.append(f"{field}={default}")

    output_fields = set()
    data_match = re.search(r'"data"\s*:\s*\{([^}]+)\}', code, re.DOTALL)
    if data_match:
        output_fields.update(re.findall(r'"(\w+)"\s*:', data_match.group(1)))

    return {
        "plugin": plugin_name,
        "plugin_var": plugin_name.replace("_api.py", "_api").replace(".py", ""),
        "actions": list(set(actions)),
        "input_fields": list(set(all_inputs)),
        "output_fields": list(output_fields),
        "action_fields": action_fields,
        "task_fields": task_fields,  # ★ task 对象有哪些字段
    }

def _build_api_context(api_summaries: list) -> str:
    if not api_summaries:
        return ""

    context = "\n\n"
    context += "【⚠️ 你是优秀的前端工程师，精通人机交互设计。这是后端提供的 API，前端必须只能使用这些字段，禁止自己编造】\n"
    context += "═══════════════════════════════════════\n\n"

    for api in api_summaries:
        context += f"API 插件: {api['plugin_var']}\n"
        context += f"  可用 action: {', '.join(api['actions'])}\n"

        # ★ 每个 action 需要的字段
        action_fields = api.get("action_fields", {})
        for action in api['actions'][:8]:
            fields = action_fields.get(action, [])
            if fields:
                context += f"  ★ {action} 必须传: {', '.join(fields)}\n"

        # ★ 自动生成 task 对象结构
        task_fields = api.get("task_fields", [])
        if task_fields:
            context += f"\n  list_tasks 返回的 task 对象字段（前端读取时用这些名字）:\n"
            for i, f in enumerate(task_fields[:15]):
                if i == 0:
                    context += f"    {f}  ← 唯一标识，删除/编辑/执行时传这个\n"
                else:
                    context += f"    {f}\n"

        context += "\n"
        context += f"  所有输入字段（带默认值）:\n"
        for f in api['input_fields'][:15]:
            context += f"    - {f}\n"
        context += f"  返回字段: {', '.join(api['output_fields'][:10])}\n"
        context += "\n"

    context += """【⚠️ 字段名强制对照 — 前端必须使用上面列出的字段名，禁止使用以下别名】

  如果后端字段名是 schedule，禁止写成 cron_desc、cron、time_desc、natural_time
  如果后端字段名是 email，禁止写成 notify_email、email_to、to_email、mail
  如果后端字段名是 task_id，禁止写成 id、taskId
  如果后端字段名是 api_url，禁止写成 url、apiUrl
  如果后端字段名是 api_method，禁止写成 method
  如果后端字段名是 api_body，禁止写成 body
  如果后端字段名是 enabled，禁止写成 active、status

  唯一允许的字段名就是上面列出的那些，一个字符都不能改。

═══════════════════════════════════════

【前端 JS 代码规范 — 必须遵守，违反前端无法运行】

1. ★★★ request() 函数必须保留 ok 字段 ★★★
   async function request(payload) {
       var resp = await fetch(API + '/' + PLUGIN, {
           method: 'POST',
           headers: {'Content-Type': 'application/json'},
           body: JSON.stringify(payload)
       });
       var rawText = await resp.text();
       if (!rawText) return {ok: false, error: '空响应'};
       var json = JSON.parse(rawText);
       if (!json) return {ok: false, error: 'JSON解析失败'};
       var result = json.data || json;
       result.ok = json.ok;  // ← 必须保留，否则无法判断成功/失败
       return result;
   }

2. ★ 创建/编辑成功后必须刷新列表
   saveTask() 成功后调用 loadTasks()
   deleteTask() 成功后调用 loadTasks()

3. ★ 字段名必须与上面列出的完全一致
   - 后端返回的 task 对象字段叫什么，前端就用什么
   - 不要自己改名字
   - 创建/编辑时传给后端的字段名，必须与上面"必须传"列表一致

4. ★★★ 事件处理 — 禁止隐式 event ★★★
   正确写法:
   <button onclick="switchTab('tasks', this)">任务</button>
   function switchTab(tabName, el) { el.classList.add('active'); }

5. 变量名避开浏览器全局变量: history, location, name, status
6. 所有 API 调用后检查返回的 ok 字段
7. ★ 列表为空时必须显示"暂无数据"提示
   if (tasks.length === 0) {
       container.innerHTML = '<p style="text-align:center;color:#999;padding:40px;">暂无任务</p>';
       return;
   }
8. 输出代码之前请确保代码与后端 API 100% 对齐

9. ★ onclick 传参时禁止字符串拼接转义，用以下方式:
   var button = document.createElement('button');
   button.textContent = '立即执行';
   button.setAttribute('data-task-id', task.task_id);
   button.addEventListener('click', function() {
       runTask(this.getAttribute('data-task-id'));
   });
   
   不要用:
   '<button onclick="runTask(\\'' + task.task_id + '\\')">执行</button>'
"""
    return context


def _parse_json(raw: str) -> dict:
    raw = strip_think_tags(raw)
    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    match = re.search(r'\{[\s\S]*\}', cleaned)
    if match:
        cleaned = match.group()
    cleaned = re.sub(r'[\x00-\x1f\x7f]', '', cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {}


def _extract_plugin_code(raw: str, plugin_name: str) -> str:
    pattern = rf'=== PLUGIN:\s*{re.escape(plugin_name)}\s*===\s*\n(.*?)=== END ==='
    match = re.search(pattern, raw, re.DOTALL | re.IGNORECASE)
    if match:
        return _clean_code(match.group(1).strip())  # ← 加这里
    # 只取第一个 PLUGIN 块
    match2 = re.search(r'=== PLUGIN:\s*(.+?)===\s*\n(.*?)=== END ===', raw, re.DOTALL)
    if match2:
        return _clean_code(match2.group(2).strip())  # ← 加这里
    return ""


def _clean_code(code: str) -> str:
    import re
    
    if not code:
        return ""
    
    # 统一换行符
    code = code.replace('\r\n', '\n').replace('\r', '\n')
    
    # 去掉 === PLUGIN === 和 === END === 残留
    code = re.sub(r'^=== PLUGIN:.*?===\s*\n?', '', code)
    code = re.sub(r'\n?=== END ===\s*$', '', code)
    
    # 去掉开头的代码块标记
    code = re.sub(r'^```(?:python|py|javascript|js|json)?\s*\n?', '', code)
    code = re.sub(r'^~~~(?:python|py|javascript|js|json)?\s*\n?', '', code)
    
    # 去掉结尾的代码块标记
    code = re.sub(r'\n?```\s*$', '', code)
    code = re.sub(r'\n?~~~\s*$', '', code)
    
    # 去掉首尾空白
    code = code.strip()
    
    return code


def _validate_python_syntax(code: str) -> tuple:
    """检查 Python 代码语法"""
    import ast
    errors = []
    try:
        ast.parse(code)
        return True, []
    except SyntaxError as e:
        errors.append(f"语法错误 第{e.lineno}行: {e.msg}")
        return False, errors


def _auto_fix_python_syntax(code: str) -> str:
    """自动修复常见的 Python 语法错误"""
    open_parens = code.count('(') - code.count(')')
    if open_parens > 0:
        code += ')' * open_parens
    open_braces = code.count('{') - code.count('}')
    if open_braces > 0:
        code += '}' * open_braces
    open_brackets = code.count('[') - code.count(']')
    if open_brackets > 0:
        code += ']' * open_brackets
    return code


def help():
    return {
        "route": "/api/builtins/generator",
        "actions": {
            "design": "架构设计",
            "generate": "生成单个 Plugin",
            "fix": "修复 Plugin",
            "generate_full": "完整生成（一把梭）",
        },
    }