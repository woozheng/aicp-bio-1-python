# plugins/applications/studio/app_gen.py
"""应用生成插件 — 动态加载辅助函数库 + 端点声明"""
import os
import json
import re
import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "users"
DB_FILE = DATA_DIR / "users.json"
LIBS_DIR = Path(__file__).parent / "libs"


# ============================================================
# 动态加载辅助函数库
# ============================================================
def _load_libs():
    """扫描 libs/ 目录，加载所有 .js 文件，返回拼接后的代码"""
    if not LIBS_DIR.exists():
        return ""
    
    code_parts = []
    for f in sorted(LIBS_DIR.glob("*.js")):
        content = f.read_text(encoding="utf-8").strip()
        if content:
            code_parts.append(f"// ===== {f.stem} =====\n{content}")
    
    return "\n\n".join(code_parts)


def _collect_lib_signatures():
    """扫描 libs/ 目录，提取每个文件的 @name、@desc、@example"""
    signatures = []
    if not LIBS_DIR.exists():
        return signatures
    
    for f in sorted(LIBS_DIR.glob("*.js")):
        content = f.read_text(encoding="utf-8")
        name = f.stem
        desc = ""
        example = ""
        for line in content.split("\n"):
            if line.startswith("// @name:"):
                name = line.replace("// @name:", "").strip()
            elif line.startswith("// @desc:"):
                desc = line.replace("// @desc:", "").strip()
            elif line.startswith("// @example:"):
                example = line.replace("// @example:", "").strip()
        signatures.append({"name": name, "desc": desc, "example": example})
    
    return signatures


def _build_lib_section():
    """构建 prompt 中的辅助函数声明段落"""
    sigs = _collect_lib_signatures()
    if not sigs:
        return ""
    
    lines = [
        "【最高优先级 — 只能使用以下函数】",
        "下面列出的函数是唯一可用的。禁止使用任何未列出的函数或自行猜测函数名。",
        "如果需要的功能不在列表中，自己用原生 JS 实现，不要假装存在某个函数。",
        "每个函数的完整签名和用法必须严格遵守。",
        "",
        "【辅助函数 — 已注入，可直接调用】",
    ]
    for s in sigs:
        if s["desc"]:
            lines.append(s["desc"])
            if s["example"]:
                lines.append("  " + s["example"])
    return "\n".join(lines)


# ============================================================
# 动态加载端点配置（外部 API）
# ============================================================
def _load_endpoints():
    """读取 endpoints.yaml"""
    import yaml as _yaml
    endpoints_path = Path(__file__).parent.parent.parent.parent / "endpoints.yaml"
    if not endpoints_path.exists():
        return []
    with open(endpoints_path, "r", encoding="utf-8") as f:
        data = _yaml.safe_load(f)
    return data.get("endpoints", [])


def _build_api_section():
    """为 prompt 构建所有端点声明"""
    endpoints = _load_endpoints()
    if not endpoints:
        return ""
    
    lines = [
        "【可用端点 — 外部 API 必须用 proxyFetch，禁止直接 fetch】",
        "proxyFetch(url, options) 已注入，返回 { json: function() { return Promise.resolve(数据); } }",
        "调用时不需要传 Authorization，代理会自动注入。",
        "",
    ]
    for ep in endpoints:
        lines.append(f"### {ep.get('name', '')}")
        if ep.get("description"):
            lines.append(ep["description"])
        
        usage = ep.get("usage", "")
        usage = usage.replace("{url}", ep.get("url", ""))
        # 不替换 {token}，让 LLM 知道不需要传 token
        usage = re.sub(r'-H "Authorization: Bearer \{token\}" \\?\n?', '', usage)
        usage = re.sub(r'"Authorization": "Bearer \{token\}",?\n?', '', usage)
        usage = re.sub(r'"Authorization": "Bearer sk-xxx",?\n?', '', usage)
        
        if ep.get("type") == "api":
            usage = usage.replace("fetch(", "proxyFetch(")
        lines.append(usage)
        lines.append("")
    return "\n".join(lines)


# ============================================================
# System Prompt
# ============================================================
APP_GEN_PROMPT = r"""你是 js 代码生成器。你是执行者，不是聊天助手，你只根据需求生成主要逻辑。


【最高铁律 — 违反将导致系统崩溃】
❌ 禁止输出 <!DOCTYPE html>
❌ 禁止输出 <html>、<head>、<body>、<style> 标签
❌ 禁止输出任何 HTML 结构
❌ 用户需求中提到"HTML"、"单文件HTML"时，完全忽略，只生成 JS
✅ 只输出 JavaScript 代码，用 ~~~js ... ~~~ 包裹
✅ 所有样式用 element.style.cssText 设置
✅ 所有 DOM 操作基于 container 参数
⚠️ **无论需求如何描述，你的输出必须是一个 `function execute(container)` 函数！**
⚠️ **不要因为用户说了 "HTML" 就生成 HTML 文件！**
⚠️ **你只负责生成 `execute` 函数，外壳，页面其他由系统自动组装！**
⚠️ **必须生成 `function execute(container)` 函数**
⚠️ 示例：
~~~js
function execute(container) {
    // 你的代码
    container.appendChild(someElement);
}
~~~
任何需求都必须用 JavaScript 代码实现。
代码必须兼容移动端（iOS Safari / Android Chrome）。
用 var 声明变量，用 function 定义函数。禁止箭头函数、const/let、模板字符串。
代码块用 ~~~js ... ~~~ 包裹。不要解释，只输出代码块。

【全局变量】
__aicp_container__ — 输出容器 DOM 元素
__aicp_width__ / __aicp_height__ — 容器宽高（像素）
llm(msgs) — 调用 LLM，返回字符串。用法：var result = await llm([{role:'user', content:'...'}]);

""" + "{lib_section}" + r"""

""" + "{api_section}" + r"""



【代码规范】
Canvas 宽高用 __aicp_width__ / __aicp_height__。
颜色用 #e2b714（金）、#e94560（红）、#007AFF（蓝）、#4CAF50（绿）。
禁止 document.body。禁止 require()。禁止 Node.js API。

【DOM 操作规范】
appendChild 之前必须检查元素是否已有父节点：
if (el.parentNode) el.parentNode.removeChild(el);
container.appendChild(el);
同一个元素只能 append 一次，重复 append 会报错 "contains the parent"。

【两种模式 — 任选其一】

模式一：工具/搜索/数据（有 UI 界面）
定义 function execute(container)，返回 HTMLElement。系统会自动调用。
示例：
~~~js
function execute(container) {
    var output = document.createElement('div');
    var onSearch = async function(q) {
        var r = await llm([{role:'user', content: q}]);
        output.textContent = r;
    };
    return Flex([SearchBar('输入内容...', onSearch), Card('结果', output)]);
}
~~~

模式二：游戏/创意（Canvas 渲染）
不定义 execute。自己创建 Canvas 并启动 GameLoop。系统检测到容器已有内容则跳过。
示例：
~~~js
var canvas = createCanvas(__aicp_container__, __aicp_width__, __aicp_height__);
var ctx = canvas.getContext('2d');
var kb = Keyboard();
var mp = MousePosition(canvas);
var player = {x: 100, y: 100, speed: 3};

function myUpdate(dt) {
    if (kb.isDown('w')) player.y -= player.speed;
    if (kb.isDown('s')) player.y += player.speed;
    if (kb.isDown('a')) player.x -= player.speed;
    if (kb.isDown('d')) player.x += player.speed;
}
function myDraw() {
    ctx.fillStyle = '#000'; ctx.fillRect(0, 0, __aicp_width__, __aicp_height__);
    ctx.fillStyle = 'red'; ctx.fillRect(player.x, player.y, 20, 20);
}
GameLoop(myUpdate, myDraw);

注意：传给 GameLoop 的两个函数必须自己先定义好。函数名可以随便取，只要保持一致就行。
~~~
"""


# ============================================================
# 执行
# ============================================================
async def execute(envelop, agent):
    action = envelop.payload.get("action", "generate")
    if action == "generate":
        return await _generate(envelop, agent)
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop


def _get_username(envelop):
    token = envelop.meta.get("token", "")
    # ★ 如果没传 token，当作系统调用，返回 "system"
    if not token:
        return "system"
    if not DB_FILE.exists():
        return "system"
    db = json.loads(DB_FILE.read_text())
    for username, info in db.items():
        if info.get("token") == token:
            return username
    return "system"  # ★ 找不到也返回 system，不报错


def _extract_code(raw):
    if not raw: return None
    match = re.search(r'~~~(?:javascript|js)?\s*\n([\s\S]*?)~~~', raw)
    if not match: match = re.search(r'```(?:javascript|js)?\s*\n([\s\S]*?)```', raw)
    if not match: match = re.search(r'```\s*\n([\s\S]*?)```', raw)
    return match.group(1).strip() if match else None


async def _generate(envelop, agent):
    import time
    _t0 = time.time()
    print(f"[app_gen] ========== 开始生成 ==========", flush=True)
    
    username = _get_username(envelop)
    if not username:
        envelop.payload = {"ok": False, "error": "请先登录"}
        return envelop
    
    messages = envelop.payload.get("messages", [])
    if not messages:
        envelop.payload = {"ok": False, "error": "messages 不能为空"}
        return envelop
    
    # 动态构建 system prompt
    print(f"[app_gen] 构建 prompt...", flush=True)
    lib_section = _build_lib_section()
    api_section = _build_api_section()
    system_prompt = APP_GEN_PROMPT.replace("{lib_section}", lib_section).replace("{api_section}", api_section)
    full_messages = [{"role": "system", "content": system_prompt}, *messages]
    
    print(f"[app_gen] system_prompt 长度: {len(system_prompt)}", flush=True)
    print(f"[app_gen] messages 数量: {len(messages)}", flush=True)
    print(f"[app_gen] 调用 LLM 流式生成...", flush=True)
    
    # ★★★ 改为流式输出 ★★★
    raw = ""
    try:
        async for token in agent.llm.chat_stream(full_messages, role="code"):
            raw += token
            # 每 50 个 token 打印一次进度
            if len(raw) % 50 == 0:
                print(f"[app_gen] 已生成 {len(raw)} 字符...", flush=True)
    except Exception as e:
        print(f"[app_gen] 流式生成失败: {e}，尝试非流式...", flush=True)
        raw = await agent.llm.chat(full_messages, role="code")
    
    print(f"[app_gen] LLM 生成完成，总长度: {len(raw)}，耗时: {time.time()-_t0:.1f}秒", flush=True)
    
    # 保存日志
    try:
        log_dir = Path(__file__).parent.parent.parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        raw_log = log_dir / f"appgen_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:18]}.txt"
        raw_log.write_text(raw, encoding="utf-8")
        print(f"[app_gen] 日志已保存: {raw_log}", flush=True)
    except Exception as e:
        print(f"[app_gen] 日志保存失败: {e}", flush=True)
    
    # 提取代码
    print(f"[app_gen] 提取代码...", flush=True)
    code = _extract_code(raw) or raw
    if '<!DOCTYPE html>' in code or '<html' in code.lower():
        print(f"[app_gen] ⚠️ LLM 生成了 HTML，提取 JS...", flush=True)
        scripts = re.findall(r'<script[^>]*>([\s\S]*?)</script>', code, re.IGNORECASE)
        if scripts:
            # 取最后一个 script（应用代码）
            code = scripts[-1].strip()
            print(f"[app_gen] ✅ 提取成功，JS 长度 {len(code)}", flush=True)
        else:
            envelop.payload = {"ok": False, "error": "LLM 生成了 HTML 但无法提取 JS"}
            return envelop
    if not code:
        print(f"[app_gen] ❌ 代码提取失败", flush=True)
        envelop.payload = {"ok": False, "error": "代码提取失败"}
        return envelop
    
    print(f"[app_gen] 代码长度: {len(code)}", flush=True)
    
    # 动态加载辅助函数 + LLM 代码
    libs_code = _load_libs()
    full_code = libs_code + "\n// ===== LLM 生成的代码 =====\n" + code
    
    print(f"[app_gen] 最终代码长度: {len(full_code)}，耗时: {time.time()-_t0:.1f}秒", flush=True)
    print(f"[app_gen] ========== 生成完成 ==========", flush=True)
    
    envelop.payload = {"ok": True, "code": full_code}
    return envelop