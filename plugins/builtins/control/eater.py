"""AICP Eater — 吞噬一切：Python 库 / Hermes 插件 / CLI 命令 / OpenAI Function Calling"""
import importlib
import pkgutil
import inspect
import json
import shutil
import subprocess
import sys
import asyncio
import re
from pathlib import Path


def help():
    return {
        "route": "/api/builtins/control/eater",
        "actions": [
            "scan", "call", "eat",              # Python 库
            "hermes_install", "hermes_list",     # Hermes 插件
            "cli_scan", "cli_call", "cli_eat",   # CLI 命令
            "to_openai", "from_openai",          # OpenAI Function Calling
        ],
        "description": "吞噬一切：Python 库、Hermes 插件、CLI 命令、OpenAI Function Calling",
    }


async def execute(envelop, agent):
    action = envelop.payload.get("action", "scan")

    # ── Python 库 ──
    if action == "scan":
        return await _scan(envelop)
    elif action == "call":
        return await _call(envelop)
    elif action == "eat":
        return await _eat(envelop)

    # ── Hermes 插件 ──
    elif action == "hermes_install":
        return await _hermes_install(envelop)
    elif action == "hermes_list":
        return await _hermes_list(envelop)

    # ── CLI 命令 ──
    elif action == "cli_scan":
        return await _cli_scan(envelop)
    elif action == "cli_call":
        return await _cli_call(envelop)
    elif action == "cli_eat":
        return await _cli_eat(envelop)

    # ── OpenAI Function Calling ──
    elif action == "to_openai":
        return await _to_openai(envelop)
    elif action == "from_openai":
        return await _from_openai(envelop)

    else:
        envelop.payload = {"error": f"Unknown action: {action}"}
        return envelop


# ============================================================
# 能力索引操作
# ============================================================

CAPABILITY_INDEX_PATH = Path("data/capability_index.json")


def _load_index():
    if CAPABILITY_INDEX_PATH.exists():
        try:
            return json.loads(CAPABILITY_INDEX_PATH.read_text(encoding="utf-8"))
        except:
            return {"entries": [], "version": 1}
    return {"entries": [], "version": 1}


def _save_index(index):
    CAPABILITY_INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _append_to_index(entry):
    """追加或更新能力索引条目"""
    index = _load_index()
    # 去重：source + name 作为唯一键
    existing = next(
        (e for e in index["entries"] 
         if e.get("source") == entry.get("source") and e.get("name") == entry.get("name")),
        None
    )
    if existing:
        existing.update(entry)
    else:
        index["entries"].append(entry)
    _save_index(index)


# ============================================================
# 自动安装
# ============================================================

async def _ensure_installed(library_name):
    try:
        importlib.import_module(library_name)
        return True
    except ImportError:
        pass
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", library_name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        return True if proc.returncode == 0 else stderr.decode("utf-8", errors="replace")[:200]
    except asyncio.TimeoutError:
        return "pip install timeout"
    except Exception as e:
        return str(e)


# ============================================================
# Python 库
# ============================================================

async def _scan(envelop):
    library_name = envelop.payload.get("library", "")
    if not library_name:
        envelop.payload = {"error": "Missing 'library'"}
        return envelop
    installed = await _ensure_installed(library_name)
    if installed is not True:
        envelop.payload = {"error": f"Install failed: {installed}"}
        return envelop
    try:
        lib = importlib.import_module(library_name)
    except ImportError:
        envelop.payload = {"error": f"Library '{library_name}' not found"}
        return envelop
    funcs = _scan_library(library_name, lib)
    envelop.payload = {"library": library_name, "total": len(funcs), "functions": funcs[:500]}
    return envelop


async def _call(envelop):
    library_name = envelop.payload.get("library", "")
    module_name = envelop.payload.get("module", "")
    func_name = envelop.payload.get("function", "")
    args = envelop.payload.get("args", {})
    if not library_name or not func_name:
        envelop.payload = {"error": "Missing library or function"}
        return envelop
    installed = await _ensure_installed(library_name)
    if installed is not True:
        envelop.payload = {"error": f"Install failed: {installed}"}
        return envelop
    try:
        mod = importlib.import_module(f"{library_name}.{module_name}") if module_name else importlib.import_module(library_name)
        func = getattr(mod, func_name, None)
    except Exception:
        func = None
    if func is None:
        envelop.payload = {"error": f"Function '{func_name}' not found"}
        return envelop
    try:
        result = func(**args) if args else func()
        envelop.payload = {"result": str(result)[:5000], "library": library_name, "function": func_name}
    except Exception as e:
        envelop.payload = {"error": str(e)}
    return envelop


async def _eat(envelop):
    library_name = envelop.payload.get("library", "")
    if not library_name:
        envelop.payload = {"error": "Missing 'library'"}
        return envelop
    installed = await _ensure_installed(library_name)
    if installed is not True:
        envelop.payload = {"error": f"Install failed: {installed}"}
        return envelop
    try:
        lib = importlib.import_module(library_name)
    except ImportError:
        envelop.payload = {"error": f"Library '{library_name}' not found"}
        return envelop
    funcs = _scan_library(library_name, lib)
    if not funcs:
        envelop.payload = {"error": "No callable functions found"}
        return envelop
    code = _generate_plugin(library_name, funcs)
    lib_dir = Path("plugins/lib")
    lib_dir.mkdir(parents=True, exist_ok=True)
    f = lib_dir / f"{library_name}.py"
    f.write_text(code, encoding="utf-8")

    # 🔥 写入能力索引
    entry = {
        "source": "eater",
        "type": "python_library",
        "name": library_name,
        "description": f"Python 库 {library_name}，包含 {len(funcs)} 个函数",
        "functions": [f"{f['module']}.{f['name']}" if f['module'] else f['name'] for f in funcs[:50]],
        "route": f"/api/lib/{library_name}",
        "plugin_file": str(f)
    }
    _append_to_index(entry)

    envelop.payload = {"ok": True, "library": library_name, "plugin_file": str(f), "total": len(funcs), "route": f"/api/lib/{library_name}"}
    return envelop


def _scan_library(library_name, lib, depth=2):
    funcs = []
    def add(mp, name, obj):
        try:
            sig = inspect.signature(obj)
            params = {k: "<required>" if v.default is inspect.Parameter.empty else repr(v.default) for k, v in sig.parameters.items()}
        except:
            params = {}
        funcs.append({"module": mp, "name": name, "params": params, "doc": (inspect.getdoc(obj) or "")[:200]})
    for name in dir(lib):
        if name.startswith("_"): continue
        obj = getattr(lib, name, None)
        if obj and callable(obj): add("", name, obj)
    if hasattr(lib, "__path__") and depth > 0:
        for _, mn, _ in pkgutil.iter_modules(lib.__path__):
            try:
                mod = importlib.import_module(f"{library_name}.{mn}")
                for name in dir(mod):
                    if name.startswith("_"): continue
                    obj = getattr(mod, name, None)
                    if obj and callable(obj): add(mn, name, obj)
            except: pass
    return sorted(funcs, key=lambda x: (x["module"], x["name"]))


def _generate_plugin(library_name, funcs):
    names = [f"{f['module']}.{f['name']}" if f['module'] else f['name'] for f in funcs]
    ex = funcs[0] if funcs else {"name": "", "module": "", "params": {}}
    return f'''"""{library_name} — Auto-generated by AICP Eater"""
import {library_name}, inspect
def help():return{{"route":"/api/lib/{library_name}","total":{len(funcs)},"functions":{repr(names[:200])},"example":{repr(ex)}}}
async def execute(envelop, agent):
    try:
        fn=envelop.payload.get("function","");mn=envelop.payload.get("module","");args=envelop.payload.get("args",{{}})
        func=getattr(__import__(f"{library_name}.{{mn}}",fromlist=[fn]),fn,None) if mn else getattr({library_name},fn,None)
        if func is None:return{{"error":f"Function not found: {{fn}}"}}
        result=func(**args) if args else func()
        envelop.payload={{"result":str(result)[:10000],"function":fn}};return envelop
    except Exception as e:envelop.payload={{"error":str(e)}};return envelop
'''


# ============================================================
# Hermes 插件
# ============================================================

async def _hermes_install(envelop):
    source = envelop.payload.get("source", "")
    plugin_name = envelop.payload.get("name", "")
    if not source:
        envelop.payload = {"error": "Missing 'source'"}
        return envelop
    if not plugin_name:
        plugin_name = source.rstrip("/").split("/")[-1].replace(".git", "")
    hermes_dir = Path("hermes_plugins") / plugin_name
    hermes_dir.mkdir(parents=True, exist_ok=True)
    if source.startswith("http") or source.startswith("git"):
        try:
            r = subprocess.run(["git", "clone", "--depth=1", source, str(hermes_dir)], capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                envelop.payload = {"error": f"Git clone failed: {r.stderr[:200]}"}
                return envelop
        except FileNotFoundError:
            envelop.payload = {"error": "Git not installed"}
            return envelop
        except subprocess.TimeoutExpired:
            envelop.payload = {"error": "Git clone timeout"}
            return envelop
    else:
        src = Path(source)
        if not src.exists():
            envelop.payload = {"error": f"Source not found: {source}"}
            return envelop
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, hermes_dir / src.name if not src.is_dir() else hermes_dir)
        if not src.is_dir():
            shutil.copy2(src, hermes_dir / src.name)
    manifest = _scan_hermes_manifest(hermes_dir)
    if manifest:
        code = _generate_hermes_adapter(plugin_name, manifest, hermes_dir)
        af = Path("plugins/applications/hermes") / f"{plugin_name}.py"
        af.parent.mkdir(parents=True, exist_ok=True)
        af.write_text(code, encoding="utf-8")

        # 🔥 写入能力索引
        entry = {
            "source": "eater",
            "type": "hermes_plugin",
            "name": plugin_name,
            "description": manifest.get("name", plugin_name),
            "route": f"/api/hermes/{plugin_name}",
            "manifest": manifest,
            "plugin_file": str(af)
        }
        _append_to_index(entry)

    envelop.payload = {"ok": True, "plugin": plugin_name, "path": str(hermes_dir), "manifest": manifest}
    return envelop


async def _hermes_list(envelop):
    hd = Path("hermes_plugins")
    plugins = []
    if hd.exists():
        for d in hd.iterdir():
            if d.is_dir():
                plugins.append({"name": d.name, "manifest": _scan_hermes_manifest(d)})
    envelop.payload = {"plugins": plugins}
    return envelop


def _scan_hermes_manifest(d):
    mf = d / "manifest.json"
    if mf.exists():
        try: return json.loads(mf.read_text(encoding="utf-8"))
        except: pass
    return None


def _generate_hermes_adapter(name, manifest, d):
    entry = manifest.get("entry", "run.py")
    return f'''"""Hermes adapter — {manifest.get("name", name)}"""
import json,subprocess,sys,asyncio
from pathlib import Path
PLUGIN_DIR=Path("{d}");ENTRY="{entry}"
def help():return{{"route":"/api/hermes/{name}","manifest":{json.dumps(manifest,ensure_ascii=False)}}}
async def execute(envelop,agent):
    try:
        p=envelop.payload.get("prompt","");c=envelop.payload.get("context","")
        proc=await asyncio.create_subprocess_exec(sys.executable,str(PLUGIN_DIR/ENTRY),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await asyncio.wait_for(proc.communicate(json.dumps({{"prompt":p,"context":c}}).encode()),60)
        envelop.payload={{"result":out.decode("utf-8",errors="replace").strip()}} if proc.returncode==0 else {{"error":err.decode("utf-8",errors="replace").strip()[:500]}}
        return envelop
    except Exception as e:envelop.payload={{"error":str(e)}};return envelop
'''


# ============================================================
# CLI 命令
# ============================================================

async def _cli_scan(envelop):
    cmd = envelop.payload.get("command", "")
    if not cmd:
        envelop.payload = {"error": "Missing 'command'"}
        return envelop
    try:
        r = subprocess.run([cmd, "--help"], capture_output=True, text=True, timeout=10)
        params = _parse_cli_help(r.stdout or r.stderr)
        envelop.payload = {"command": cmd, "params": params, "help": (r.stdout or r.stderr)[:2000]}
    except FileNotFoundError:
        envelop.payload = {"error": f"Command not found: {cmd}"}
    except subprocess.TimeoutExpired:
        envelop.payload = {"error": "Help timeout"}
    except Exception as e:
        envelop.payload = {"error": str(e)}
    return envelop


async def _cli_call(envelop):
    cmd = envelop.payload.get("command", "")
    args = envelop.payload.get("args", [])
    stdin_data = envelop.payload.get("stdin", "")
    if not cmd:
        envelop.payload = {"error": "Missing 'command'"}
        return envelop
    try:
        r = subprocess.run([cmd] + args, input=stdin_data, capture_output=True, text=True, timeout=120)
        envelop.payload = {"stdout": r.stdout[:5000], "stderr": r.stderr[:2000], "returncode": r.returncode}
    except FileNotFoundError:
        envelop.payload = {"error": f"Command not found: {cmd}"}
    except subprocess.TimeoutExpired:
        envelop.payload = {"error": "Command timeout"}
    except Exception as e:
        envelop.payload = {"error": str(e)}
    return envelop


async def _cli_eat(envelop):
    cmd = envelop.payload.get("command", "")
    if not cmd:
        envelop.payload = {"error": "Missing 'command'"}
        return envelop
    code = _generate_cli_plugin(cmd)
    lib_dir = Path("plugins/lib")
    lib_dir.mkdir(parents=True, exist_ok=True)
    f = lib_dir / f"cli_{cmd}.py"
    f.write_text(code, encoding="utf-8")

    # 🔥 写入能力索引
    entry = {
        "source": "eater",
        "type": "cli_command",
        "name": cmd,
        "description": f"CLI 命令 {cmd}",
        "route": f"/api/lib/cli_{cmd}",
        "plugin_file": str(f)
    }
    _append_to_index(entry)

    envelop.payload = {"ok": True, "command": cmd, "plugin_file": str(f), "route": f"/api/lib/cli_{cmd}"}
    return envelop


def _parse_cli_help(help_text):
    """从 --help 输出中解析参数"""
    params = []
    for line in help_text.split("\n"):
        line = line.strip()
        m = re.match(r'(-{1,2}[\w-]+)(?:\s+[A-Z]+)?\s*(.*)', line)
        if m:
            params.append({"flag": m.group(1), "desc": m.group(2).strip()[:100]})
    return params[:50]


def _generate_cli_plugin(cmd):
    return f'''"""{cmd} — Auto-generated CLI plugin by AICP Eater"""
import subprocess, json

def help():return{{"route":"/api/lib/cli_{cmd}","command":"{cmd}"}}

async def execute(envelop, agent):
    try:
        args = envelop.payload.get("args", [])
        stdin_data = envelop.payload.get("stdin", "")
        r = subprocess.run(["{cmd}"] + args, input=stdin_data, capture_output=True, text=True, timeout=120)
        envelop.payload = {{"stdout": r.stdout[:5000], "stderr": r.stderr[:2000], "returncode": r.returncode}}
        return envelop
    except Exception as e:
        envelop.payload = {{"error": str(e)}}
        return envelop
'''


# ============================================================
# OpenAI Function Calling
# ============================================================

async def _to_openai(envelop):
    """Python 库 → OpenAI Function Calling Schema"""
    library_name = envelop.payload.get("library", "")
    if not library_name:
        envelop.payload = {"error": "Missing 'library'"}
        return envelop
    installed = await _ensure_installed(library_name)
    if installed is not True:
        envelop.payload = {"error": f"Install failed: {installed}"}
        return envelop
    try:
        lib = importlib.import_module(library_name)
    except ImportError:
        envelop.payload = {"error": f"Library '{library_name}' not found"}
        return envelop
    funcs = _scan_library(library_name, lib)
    tools = [_to_openai_function(f) for f in funcs[:100]]
    envelop.payload = {"library": library_name, "tools": tools, "total": len(tools)}
    return envelop


def _to_openai_function(f):
    props = {}
    for name, default in f["params"].items():
        props[name] = {"type": "string", "description": f"Parameter: {name}"}
    return {
        "type": "function",
        "function": {
            "name": f["name"],
            "description": f["doc"] or f"Call {f['name']}",
            "parameters": {
                "type": "object",
                "properties": props,
                "required": [k for k, v in f["params"].items() if v == "<required>"]
            }
        }
    }


async def _from_openai(envelop):
    """OpenAI Function Schema → AICP 插件"""
    tools = envelop.payload.get("tools", [])
    plugin_name = envelop.payload.get("name", "openai_tools")
    if not tools:
        envelop.payload = {"error": "Missing 'tools'"}
        return envelop

    code = _generate_openai_plugin(plugin_name, tools)
    lib_dir = Path("plugins/lib")
    lib_dir.mkdir(parents=True, exist_ok=True)
    f = lib_dir / f"{plugin_name}.py"
    f.write_text(code, encoding="utf-8")

    # 🔥 写入能力索引
    entry = {
        "source": "eater",
        "type": "openai_tools",
        "name": plugin_name,
        "description": f"OpenAI Function Calling 插件，包含 {len(tools)} 个工具",
        "route": f"/api/lib/{plugin_name}",
        "plugin_file": str(f),
        "tools_count": len(tools)
    }
    _append_to_index(entry)

    envelop.payload = {"ok": True, "plugin_name": plugin_name, "plugin_file": str(f), "route": f"/api/lib/{plugin_name}", "total_tools": len(tools)}
    return envelop


def _generate_openai_plugin(plugin_name, tools):
    tools_json = json.dumps(tools, ensure_ascii=False, indent=2)
    return f'''"""{plugin_name} — OpenAI Function Calling plugin"""
import json

TOOLS = {tools_json}

def help(): return {{"route": "/api/lib/{plugin_name}", "tools": TOOLS}}

async def execute(envelop, agent):
    try:
        tool_name = envelop.payload.get("tool", "")
        args = envelop.payload.get("args", {{}})
        envelop.payload = {{"called": tool_name, "args": args, "message": f"Tool '{{tool_name}}' called with args: {{json.dumps(args)}}"}}
        return envelop
    except Exception as e:
        envelop.payload = {{"error": str(e)}}
        return envelop
'''