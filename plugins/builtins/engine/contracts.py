# plugins/applications/studio/engine/contracts.py
"""
契约传递 — 从 dev_agent 提取
上游实际输出字段 → 下游输入契约
"""

import re
import json


def extract_output_fields(code: str) -> list:
    """
    从 Plugin 代码中提取实际输出的字段名。
    支持 envelop.payload = {"ok": True, "data": {...}} 格式。
    
    输入: Plugin 的 Python 源码
    输出: 字段名列表，如 ["files", "count", "summary"]
    """
    if not code:
        return []

    # 策略1: 匹配 envelop.payload = {...}
    match = re.search(r'envelop\.payload\s*=\s*(\{.*?\n\})', code, re.DOTALL)
    if match:
        try:
            json_str = match.group(1)
            json_str = _normalize_python_json(json_str)
            payload = json.loads(json_str)
            data = payload.get("data", {})
            if isinstance(data, dict):
                return list(data.keys())
        except (json.JSONDecodeError, Exception):
            pass

    # 策略2: 匹配 "data" 键后的花括号内容（支持嵌套）
    data_match = re.search(r'"data"\s*:\s*\{', code)
    if data_match:
        start = data_match.end() - 1  # 指向 {
        end = _find_matching_brace(code, start)
        if end > start:
            inner = code[start:end]
            fields = re.findall(r'"(\w+)"\s*:', inner)
            return _filter_top_level_fields(fields, code)

    # 策略3: 匹配 return {"data": f"...字符串..."}
    # 字符串内没有字段，返回空
    return_match = re.search(r'return\s*\{\s*"data"\s*:', code)
    if return_match:
        # 检查 data 的值是否是 f-string 或普通字符串
        after_data = code[return_match.end():].strip()
        if after_data.startswith("f\"") or after_data.startswith("f'") or \
           after_data.startswith("\"") or after_data.startswith("'"):
            return []  # 字符串类型，无结构化字段

    return []


def build_input_contract(upstream_code: str, upstream_plugin_name: str = "") -> dict:
    """
    从上游插件代码中提取输出字段，构建下游插件的输入契约。
    
    输入:
      - upstream_code: 上游插件的 Python 源码
      - upstream_plugin_name: 上游插件名（用于提示）
    
    输出:
      {
        "fields": ["field1", "field2"],
        "hint": "从 prev.data 中读取：prev.data.get('field1'), prev.data.get('field2')",
        "upstream_plugin": "插件名"
      }
    """
    fields = extract_output_fields(upstream_code)
    return {
        "fields": fields,
        "hint": _build_input_hint(fields, upstream_plugin_name),
        "upstream_plugin": upstream_plugin_name,
    }


def build_context_with_contract(
    task: str,
    current_plugin: str,
    current_spec: dict,
    upstream_spec: dict,
    pipeline: dict = None,
) -> str:
    """
    构建带契约传递的生成上下文。
    给 generator 用——告诉 LLM 上游输出了什么字段，你必须用什么字段名读取。
    
    输入:
      - task: 原始需求
      - current_plugin: 当前要生成的插件名
      - current_spec: 当前插件的规格 {"name": "...", "description": "...", "output": {...}}
      - upstream_spec: 上游插件的规格（含实际输出字段）
      - pipeline: 全局视角描述
    
    输出: 一段完整的 prompt 文本，可直接作为 user message
    """
    context = f"任务: {task[:600]}\n\n"
    context += f"当前生成: {current_plugin}\n"
    context += f"职责: {current_spec.get('description', '实现完整功能')}\n\n"

    if pipeline:
        context += f"全局视角: {pipeline.get('description', '')}\n\n"

    if upstream_spec and upstream_spec.get("output"):
        upstream_name = upstream_spec.get("name", "上游插件")
        upstream_fields = list(upstream_spec["output"].keys())
        upstream_desc = upstream_spec.get("produces_for_downstream", "")

        context += "=" * 40 + "\n"
        context += "⚠️ 上游数据契约 — 必须逐字段遵守\n"
        context += "=" * 40 + "\n\n"
        context += f"上游插件「{upstream_name}」已生成完毕。\n"
        if upstream_desc:
            context += f"它产出的内容: {upstream_desc}\n"
        context += "\n你必须从 prev.data 中按以下字段名精确读取：\n"
        for field in upstream_fields:
            context += f'  prev.data.get("{field}")\n'
        context += "\n禁止自己编造字段名。禁止假设字段名。\n\n"
    else:
        context += "本插件是第一个插件，没有上游输入。\n\n"

    if current_spec.get("output"):
        context += f"你的输出必须包含以下字段（供下游使用）：\n"
        context += json.dumps(current_spec["output"], ensure_ascii=False, indent=2)
        context += "\n字段名必须与上述定义完全一致。\n"

    return context


# ============================================================
# 工具函数
# ============================================================

def _normalize_python_json(s: str) -> str:
    """Python dict 字符串 → 标准 JSON 字符串"""
    s = s.replace("True", "true").replace("False", "false").replace("None", "null")
    s = re.sub(r"'([^']*)'", r'"\1"', s)
    return s


def _find_matching_brace(code: str, start: int) -> int:
    """从 start 位置找匹配的 } """
    depth = 0
    for i in range(start, len(code)):
        if code[i] == '{':
            depth += 1
        elif code[i] == '}':
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _filter_top_level_fields(fields: list, code: str) -> list:
    """过滤掉嵌套对象中的字段，只保留顶层字段"""
    if not fields:
        return []
    # 简单策略：去重，保持顺序
    seen = set()
    result = []
    for f in fields:
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result


def _build_input_hint(fields: list, upstream_name: str = "") -> str:
    """构建自然语言提示"""
    if not fields:
        return f"上游插件{'「' + upstream_name + '」' if upstream_name else ''}无结构化输出，或输出为纯文本。"
    
    lines = [f"从上游插件{'「' + upstream_name + '」' if upstream_name else ''}的 prev.data 中读取："]
    for f in fields:
        lines.append(f'  prev.data.get("{f}")')
    return "\n".join(lines)


__all__ = [
    "extract_output_fields",
    "build_input_contract",
    "build_context_with_contract",
]