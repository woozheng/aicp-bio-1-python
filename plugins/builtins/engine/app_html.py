"""app_html — 生成完整可运行的 HTML 应用（含模板 + 辅助函数）"""
import json
import re
from pathlib import Path
import core

LIBS_DIR = Path(__file__).parent / "libs"


def _load_libs():
    """加载 libs/ 目录下所有 JS 辅助函数"""
    if not LIBS_DIR.exists():
        return ""
    parts = []
    for f in sorted(LIBS_DIR.glob("*.js")):
        content = f.read_text(encoding="utf-8").strip()
        if content:
            parts.append(f"// ===== {f.stem} =====\n{content}")
    return "\n\n".join(parts)


def _is_html_wrapper(code: str) -> bool:
    """检测代码是否包含完整的 HTML 包装"""
    return bool(re.search(r'<!DOCTYPE\s+html', code, re.IGNORECASE))


def _extract_execute_from_html(code: str) -> str:
    """从 LLM 生成的 HTML 中提取 execute 函数"""
    # 尝试从 <script> 标签中提取
    script_match = re.search(r'<script[^>]*>([\s\S]*?)</script>', code)
    if script_match:
        inner = script_match.group(1).strip()
        if "function execute" in inner or "async function execute" in inner:
            return inner
    return ""


async def execute(envelop, agent):
    action = envelop.payload.get("action", "generate")

    if action == "generate":
        return await _generate_html(envelop, agent)

    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop


async def _generate_html(envelop, agent):
    import time
    _t0 = time.time()
    print(f"[app_html] ========== 开始 ==========", flush=True)
    
    name = envelop.payload.get("name", "")
    description = envelop.payload.get("description", "")
    
    print(f"[app_html] name: {name}", flush=True)
    print(f"[app_html] description 长度: {len(description)}", flush=True)
    
    if not name:
        envelop.payload = {"ok": False, "error": "缺少 name 参数"}
        return envelop
    if not description:
        envelop.payload = {"ok": False, "error": "缺少 description 参数"}
        return envelop
    
    # 调用 app_gen
    print(f"[app_html] 调用 app_gen...", flush=True)
    gen_env = core.Envelop(
        sender="builtins/engine/app_html",
        receiver="builtins/engine/app_gen",
        payload={
            "action": "generate",
            "messages": [{"role": "user", "content": description}],
        },
        meta=envelop.meta
    )
    
    gen_result = await agent.system.call(gen_env)
    print(f"[app_html] app_gen 返回 ok={gen_result.payload.get('ok')}，耗时: {time.time()-_t0:.1f}秒", flush=True)
    
    if not gen_result.payload.get("ok"):
        envelop.payload = {"ok": False, "error": gen_result.payload.get("error", "生成失败")}
        return envelop
    
    code = gen_result.payload.get("code", "")
    print(f"[app_html] 代码长度: {len(code)}", flush=True)
    
    if not code:
        envelop.payload = {"ok": False, "error": "生成代码为空"}
        return envelop
    
    # 过滤 HTML 包装
    if _is_html_wrapper(code):
        print(f"[app_html] ⚠️ 检测到 HTML 包装，提取 JS...", flush=True)
        extracted = _extract_execute_from_html(code)
        if extracted:
            code = extracted
            print(f"[app_html] ✅ 提取成功，长度 {len(code)}", flush=True)
    
    # 确保有 execute 函数
    if "function execute" not in code and "async function execute" not in code:
        if "var canvas" in code or "createCanvas" in code or "GameLoop" in code:
            code = f"function execute(container) {{\n    {code}\n}}"
            print(f"[app_html] 自动包裹 execute", flush=True)
        else:
            envelop.payload = {"ok": False, "error": "生成的代码中没有 execute 函数"}
            return envelop
    
    # 加载辅助函数
    libs_code = _load_libs()
    
    # 组装 HTML
    print(f"[app_html] 组装 HTML...", flush=True)
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{name} - AICP</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
    font-family:-apple-system,sans-serif;
    background:#1a1a2e;
    color:#e0e0e0;
    width:100vw;
    height:100vh;
    overflow:hidden;
    display:flex;
    justify-content:center;
    align-items:center;
}}
#aicp-container{{
    width:100%;
    height:100%;
    max-width:none;
    padding:0;
    margin:0;
    overflow:hidden;
    position:relative;
}}
</style>
</head>
<body>
<div id="aicp-container"></div>
<script>
{libs_code}

// ===== 应用代码 =====
{code}
</script>
</body>
</html>'''
    
    # 落盘
    print(f"[app_html] 落盘...", flush=True)
    save_path = Path("www") / name / "index.html"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(html, encoding="utf-8")
    print(f"[app_html] ✅ 已保存: {save_path}，大小: {len(html)}，总耗时: {time.time()-_t0:.1f}秒", flush=True)
    
    envelop.payload = {
        "ok": True,
        "name": name,
        "url": f"/{name}/",
        "saved_to": str(save_path),
        "size": len(html)
    }
    print(f"[app_html] ========== 完成 ==========", flush=True)
    return envelop


def help():
    return {
        "route": "builtins/engine/app_html",
        "description": "生成完整可运行的 HTML 应用并落盘",
        "input": {
            "name": "应用名称（目录名）",
            "description": "需求描述"
        },
        "output": {
            "ok": "是否成功",
            "name": "应用名称",
            "url": "访问路径",
            "saved_to": "保存路径",
            "size": "文件大小"
        }
    }