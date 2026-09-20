"""
契约智能体 — 专用 LLM，只做一件事：读源码，提取契约
- 有 .contract.json 且 source_hash 一致：直接返回（落盘缓存）
- 无契约 或 hash 不一致：读源码 → LLM 提取 → 保存 .contract.json
- LLM 提取失败：返回 fallback（正则粗扫 action 名单）
- 提取契约时只读源码，不解析 @AICP_ALIGN 注释
- 返回：契约（不含源码片段）
- get_tools：返回 Function Calling tools schema
"""

import json
import re
import hashlib
from pathlib import Path

import core


# ============================================================
# 专用 LLM 的 System Prompt
# ============================================================

CONTRACT_EXTRACTOR_PROMPT = """你是一个契约提取专家。你的唯一任务：读插件源码，提取标准契约。

## 你收到的源码是完整的插件文件（包含 execute 函数和所有 helper 函数）

## 你的工作方式

### 第一步：定位 execute 函数
找到 async def execute(envelop, agent): 函数。这是插件的唯一入口。

### 第二步：提取所有 action 名称
扫描 execute 函数内的 action 分发逻辑：
- if action == 'xxx': 提取 xxx
- elif action == 'yyy': 提取 yyy
- match action: case 'zzz': 提取 zzz
- 字典路由 actions = {'aaa': handler} 提取 aaa

去重，列出所有 action。

### 第三步：逐个 action 提取参数【必须！不能省略！】
对每个 action 的代码块，扫描 envelop.payload 的引用：
- payload['xxx'] 是必填参数
- payload.get('xxx') 是非必填参数
- payload.get('xxx', 默认值) 是非必填，有默认值

【重要】如果 action 的参数不在 execute 函数里，而是在 helper 函数里（如 _scan、_call 等），
也要提取这些 helper 函数的参数。每个 action 必须列出所有参数，不能为空！

参数类型推断规则：
- 默认值是数字 → number
- 默认值是字符串 → string
- 默认值是 True/False → boolean
- 默认值是 [] → array
- 默认值是 {} → object
- 猜不出来 → string

### 第四步：提取返回字段【必须！不能省略！】
找到 return envelop 之前的 envelop.payload = {...}，提取所有顶层 key。
如果返回结构在 helper 函数里，也要提取。

### 第五步：生成标准契约
按标准 schema 输出：

{
  "plugin": "插件路由名",
  "actions": {
    "action名": {
      "description": "功能描述（一句话）",
      "params": [
        {"name": "参数名", "required": true/false, "type": "类型", "desc": "参数说明"}
      ],
      "returns": [
        {"name": "返回字段", "type": "类型", "desc": "说明"}
      ],
      "notes": "补充说明（可选）",
      "pitfalls": "坑点提醒（可选）"
    }
  }
}

## 核心原则

1. 源码是唯一真相——不要猜，所有信息从源码提取
2. 准确优先——action 名和参数名必须 100% 准确
3. 完整覆盖——不要遗漏任何 action 和参数
4. 参数和返回值不能为空——如果源码里有，必须提取出来
5. 保守估计——不确定的类型标 string，不确定的必填性标非必填

## 输出格式

你只输出 JSON，不要包含任何其他文字：

{
  "plugin": "插件路由名",
  "actions": {
    "action名": {
      "description": "功能描述",
      "params": [...],
      "returns": [...],
      "pitfalls": "坑点提醒"
    }
  }
}
"""


# ============================================================
# 契约 → Function Calling tools 转换
# ============================================================

_TYPE_MAP = {
    "string": "string",
    "number": "number",
    "integer": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


def contract_to_tools(plugin_name: str, contract: dict) -> list:
    """把标准契约转成 OpenAI Function Calling tools schema"""
    tools = []
    actions = contract.get("actions", {})
    
    for action_name, action_def in actions.items():
        properties = {}
        required = []
        
        for param in action_def.get("params", []):
            param_name = param.get("name", "")
            if not param_name:
                continue
            
            param_type = _TYPE_MAP.get(param.get("type", "string"), "string")
            param_desc = param.get("desc", "")
            
            prop = {
                "type": param_type,
                "description": param_desc,
            }
            
            # array 类型补充 items
            if param_type == "array":
                prop["items"] = {"type": "string"}
            
            properties[param_name] = prop
            
            if param.get("required", False):
                required.append(param_name)
        
        tool = {
            "type": "function",
            "function": {
                "name": f"{plugin_name}::{action_name}",
                "description": action_def.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
            }
        }
        tools.append(tool)
    
    return tools


# ============================================================
# ★ 源码 hash
# ============================================================

def calc_source_hash(source_code: str) -> str:
    """算源码 md5 hash"""
    return hashlib.md5(source_code.encode("utf-8")).hexdigest()


# ============================================================
# ★ Fallback 契约（LLM 提取失败时用）
# ============================================================

def build_fallback_contract(plugin_name: str, source_code: str) -> dict:
    """LLM 提取失败时的 fallback：用正则粗扫源码，提取 action 名单。
    
    覆盖：
    - Python: if action == "xxx" / elif action == "yyy"
    - TS: if (action === "xxx") / case "xxx":
    
    提取不到时返回空 actions，至少让主 Agent 知道"有插件但没契约"。
    """
    actions = {}
    
    # Python: if action == "xxx" / elif action == "yyy"
    py_if_regex = re.compile(r'(?:if|elif)\s+action\s*==\s*["\']([^"\']+)["\']')
    # TS: if (action === "xxx") / if (action == "xxx")
    ts_if_regex = re.compile(r'if\s*\(\s*action\s*===?\s*["\']([^"\']+)["\']\s*\)')
    # TS: case "xxx":
    ts_case_regex = re.compile(r'case\s+["\']([^"\']+)["\']\s*:')
    
    for regex in (py_if_regex, ts_if_regex, ts_case_regex):
        for m in regex.finditer(source_code):
            name = m.group(1)
            if name and name not in actions:
                actions[name] = {
                    "description": "(fallback: LLM extraction failed)",
                    "params": [],
                    "returns": [],
                    "notes": "此契约由正则粗扫生成，可能不完整。建议修复后 refresh。",
                }
    
    return {
        "plugin": plugin_name,
        "actions": actions,
        "_fallback": True,
        "_fallback_note": (
            "LLM 提取失败，此契约由正则粗扫生成，action 名单可能不全，"
            "参数和返回值缺失。建议检查插件源码后调用 refresh。"
        ),
    }


# ============================================================
# LLM 提取契约
# ============================================================

async def _call_llm_extract(agent, plugin_name: str, source_code: str) -> dict:
    """直接调用 LLM 提取契约"""
    prompt = (
        CONTRACT_EXTRACTOR_PROMPT
        + "\n\n## 插件源码（完整文件）\n\n插件路径："
        + plugin_name
        + "\n\n"
        + source_code
        + "\n\n请提取契约，只输出 JSON。"
    )
    
    try:
        llm_output = await agent.llm.chat([{"role": "user", "content": prompt}], role="code")
        
        if not llm_output:
            return {"ok": False, "error": "LLM 返回为空"}
        
        json_match = re.search(r'\{[\s\S]*\}', llm_output)
        if json_match:
            contract = json.loads(json_match.group())
            
            # ★ 结构校验：必须有 actions 且非空
            if not contract.get("actions") or not isinstance(contract["actions"], dict) or len(contract["actions"]) == 0:
                return {
                    "ok": False,
                    "error": "LLM 返回缺少 actions 字段或为空",
                    "raw": llm_output[:500],
                }
            
            contract.setdefault("plugin", plugin_name)
            return {"ok": True, "contract": contract}
        
        return {"ok": False, "error": "LLM 返回不是 JSON", "raw": llm_output[:500]}
    except Exception as e:
        return {"ok": False, "error": f"LLM 调用失败: {e}"}


# ============================================================
# 主入口
# ============================================================

async def execute(envelop, agent):
    action = envelop.payload.get("action", "get")
    plugin_name = envelop.payload.get("plugin", "").strip()
    
    if not plugin_name:
        envelop.payload = {"ok": False, "error": "缺少 plugin 参数"}
        return envelop
    
    # 统一去掉 .py / .ts 后缀
    if plugin_name.endswith(".py"):
        plugin_name = plugin_name[:-3]
    elif plugin_name.endswith(".ts"):
        plugin_name = plugin_name[:-3]
    
    plugin_file = Path("plugins") / f"{plugin_name}.py"
    contract_file = Path("plugins") / f"{plugin_name}.contract.json"
    
    # ============================================================
    # action: get — 返回标准契约
    # ============================================================
    if action == "get":
        if not plugin_file.exists():
            envelop.payload = {"ok": False, "error": f"插件源码不存在: {plugin_file}"}
            return envelop
        
        try:
            source_code = plugin_file.read_text(encoding="utf-8")
        except Exception as e:
            envelop.payload = {"ok": False, "error": f"读取源码失败: {e}"}
            return envelop
        
        # ★ 算当前源码 hash
        current_hash = calc_source_hash(source_code)
        
        # ★ 有契约文件，且 hash 一致 → 直接返回
        if contract_file.exists():
            try:
                contract = json.loads(contract_file.read_text(encoding="utf-8"))
                if contract.get("source_hash") == current_hash:
                    envelop.payload = {
                        "ok": True,
                        "plugin": plugin_name,
                        "contract": contract,
                        "source": "existing_contract",
                        "contract_file": str(contract_file),
                    }
                    return envelop
                else:
                    print(f"[contract_agent] source changed, re-extracting: {plugin_name}")
            except Exception:
                pass
        
        # ★ 契约缺失 或 hash 不一致 → LLM 提取
        result = await _call_llm_extract(agent, plugin_name, source_code)
        
        if result.get("ok"):
            # ★ 写入 source_hash
            result["contract"]["source_hash"] = current_hash
            try:
                contract_file.write_text(
                    json.dumps(result["contract"], ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                result["contract_saved"] = str(contract_file)
            except Exception as e:
                result["contract_saved"] = f"保存失败: {e}"
            result["source"] = "llm_extraction"
        else:
            # ★ 提取失败 → fallback
            result["source"] = "extraction_failed"
            result["fallback"] = build_fallback_contract(plugin_name, source_code)
        
        result["plugin"] = plugin_name
        envelop.payload = result
        return envelop
    
    # ============================================================
    # action: get_tools — 返回 Function Calling tools schema
    # ============================================================
    elif action == "get_tools":
        if not plugin_file.exists():
            envelop.payload = {"ok": False, "error": f"插件源码不存在: {plugin_file}"}
            return envelop
        
        try:
            source_code = plugin_file.read_text(encoding="utf-8")
        except Exception as e:
            envelop.payload = {"ok": False, "error": f"读取源码失败: {e}"}
            return envelop
        
        # ★ 算当前源码 hash
        current_hash = calc_source_hash(source_code)
        
        contract = None
        source = ""
        
        # 1. 优先读契约文件，且 hash 一致
        if contract_file.exists():
            try:
                existing = json.loads(contract_file.read_text(encoding="utf-8"))
                if existing.get("source_hash") == current_hash:
                    contract = existing
                    source = "existing_contract"
            except Exception:
                contract = None
        
        # 2. 契约缺失 或 hash 不一致 → LLM 提取并保存
        if contract is None:
            result = await _call_llm_extract(agent, plugin_name, source_code)
            
            if not result.get("ok"):
                # ★ 提取失败 → fallback
                fallback = build_fallback_contract(plugin_name, source_code)
                envelop.payload = {
                    "ok": True,
                    "plugin": plugin_name,
                    "tools": contract_to_tools(plugin_name, fallback),
                    "source": "fallback",
                    "extraction_error": result.get("error"),
                }
                return envelop
            
            result["contract"]["source_hash"] = current_hash
            contract = result["contract"]
            source = "llm_extraction"
            
            try:
                contract_file.write_text(
                    json.dumps(contract, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
            except Exception:
                pass
        
        # 3. 转 Function Calling tools
        tools = contract_to_tools(plugin_name, contract)
        
        envelop.payload = {
            "ok": True,
            "plugin": plugin_name,
            "tools": tools,
            "source": source,
            "contract_file": str(contract_file),
        }
        return envelop
    
    # ============================================================
    # action: get_execute_only — 返回 execute 函数源码
    # ============================================================
    elif action == "get_execute_only":
        if not plugin_file.exists():
            envelop.payload = {"ok": False, "error": f"插件源码不存在: {plugin_file}"}
            return envelop
        
        source_code = plugin_file.read_text(encoding="utf-8")
        
        lines = source_code.split('\n')
        execute_start = -1
        execute_end = -1
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if execute_start == -1 and ('async def execute' in stripped or 'def execute' in stripped):
                execute_start = i
                continue
            
            if execute_start != -1:
                if stripped.startswith('async def ') or stripped.startswith('def ') or stripped.startswith('class '):
                    if 'def execute' not in stripped:
                        execute_end = i
                        break
        
        if execute_start == -1:
            execute_code = source_code
        elif execute_end == -1:
            execute_code = '\n'.join(lines[execute_start:])
        else:
            execute_code = '\n'.join(lines[execute_start:execute_end])
        
        envelop.payload = {
            "ok": True,
            "plugin": plugin_name,
            "source_snippet": execute_code,
        }
        return envelop
    
    # ============================================================
    # action: refresh — 强制重新提取契约
    # ============================================================
    elif action == "refresh":
        if not plugin_file.exists():
            envelop.payload = {"ok": False, "error": f"插件源码不存在: {plugin_file}"}
            return envelop
        
        source_code = plugin_file.read_text(encoding="utf-8")
        current_hash = calc_source_hash(source_code)
        
        result = await _call_llm_extract(agent, plugin_name, source_code)
        
        if result.get("ok"):
            result["contract"]["source_hash"] = current_hash
            try:
                contract_file.write_text(
                    json.dumps(result["contract"], ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                result["contract_saved"] = str(contract_file)
            except Exception as e:
                result["contract_saved"] = f"保存失败: {e}"
            result["source"] = "refreshed"
        else:
            result["source"] = "extraction_failed"
            result["fallback"] = build_fallback_contract(plugin_name, source_code)
        
        result["plugin"] = plugin_name
        envelop.payload = result
        return envelop
    
    else:
        envelop.payload = {"ok": False, "error": f"未知 action: {action}"}
        return envelop


def help() -> dict:
    return {
        "route": "builtins/agents/contract_agent",
        "description": "契约智能体 — 有契约且 hash 一致直接返回，无契约或 hash 不一致则 LLM 提取",
        "input": {
            "action": "get | get_tools | get_execute_only | refresh",
            "plugin": "插件路由名（如 applications/office/converter）"
        },
        "output": {
            "ok": "是否成功",
            "contract": "标准契约 JSON（含 source_hash）",
            "tools": "Function Calling tools schema",
            "source": "existing_contract | llm_extraction | refreshed | extraction_failed | fallback",
            "contract_file": "契约文件路径"
        }
    }