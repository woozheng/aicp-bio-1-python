# plugins/builtins/aicp/chat.py
"""AICP Chat API — 远端 chatEnvelop 端点 / 能力探测 / 兜底工具"""

import platform
import subprocess
import requests as _requests
import json

from core import Envelop
from runtime._aicp_llm import AICP_LLM


async def execute(envelop, agent):
    action = envelop.payload.get("action", "chat")

    # ===== 能力探测 =====
    if action == "capabilities":
        caps = await _detect_capabilities(agent)
        return Envelop(
            receiver=envelop.sender,
            payload={"ok": True, "data": caps}
        )

    # ===== 正常 chat（兜底执行） =====
    # ★★★ 支持两种调用方式 ★★★
    # 方式1：直接传 messages
    messages = envelop.payload.get("messages")
    
    # 方式2：传 task（main_agent 的 aicp_chat 调用方式）
    task = envelop.payload.get("task", "")
    if not messages and task:
        messages = [{"role": "user", "content": task}]
    
    # 方式3：从 payload 里取（兼容旧格式）
    if not messages:
        messages = envelop.payload.get("payload", {}).get("messages", [])

    if not messages:
        return Envelop(
            receiver=envelop.sender,
            payload={"ok": False, "error": "缺少 messages 或 task 参数"}
        )

    model = envelop.payload.get("model")
    temperature = envelop.payload.get("temperature")
    max_iter = envelop.payload.get("max_iter", 5)

    if not hasattr(agent, 'aicp_llm'):
        agent.aicp_llm = AICP_LLM(agent.config)

    result = await agent.aicp_llm.chatEnvelop(
        messages,
        model=model,
        role="code",
        temperature=temperature,
        max_iter=max_iter
    )

    payload = result.payload if hasattr(result, 'payload') else result
    
    # ★★★ 提取返回数据 ★★★
    data = payload.get("data", "")
    error = payload.get("error", "")
    
    # 如果 data 是 dict，转成 JSON 字符串
    if isinstance(data, (dict, list)):
        data = json.dumps(data, ensure_ascii=False, indent=2)
    
    return Envelop(
        receiver=envelop.sender,
        payload={
            "ok": payload.get("ok", True),
            "data": data,
            "error": error
        }
    )


async def _detect_capabilities(agent):
    """探测本节点能力，返回 dict"""
    can_access_foreign = False
    can_access_domestic = False
    try:
        resp = _requests.get("https://www.google.com", timeout=3)
        can_access_foreign = resp.status_code == 200
    except Exception:
        pass
    try:
        resp = _requests.get("https://www.baidu.com", timeout=3)
        can_access_domestic = resp.status_code == 200
    except Exception:
        pass

    has_gpu = False
    gpu_model = ""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
            encoding='utf-8', errors='ignore'
        )
        if result.returncode == 0:
            has_gpu = True
            gpu_model = result.stdout.strip().split("\n")[0]
    except Exception:
        pass

    models_config = agent.config.get("models", agent.config.get("model", {}))
    if isinstance(models_config, list):
        model_names = [m.get("model_name", m.get("name", str(m))) for m in models_config]
    elif isinstance(models_config, dict):
        model_names = [models_config.get("model_name", models_config.get("name", "default"))]
    else:
        model_names = ["default"]

    return {
        "name": agent.config.get("node_name", platform.node()),
        "os": platform.system(),
        "python": platform.python_version(),
        "network": {
            "can_access_foreign": can_access_foreign,
            "can_access_domestic": can_access_domestic,
        },
        "gpu": {
            "available": has_gpu,
            "model": gpu_model,
        },
        "models": model_names,
    }


def help():
    return {
        "route": "builtins/aicp/chat",
        "description": "AICP 兜底工具 — 执行一次性代码任务",
        "input": {
            "task": "具体任务描述（main_agent 调用方式）",
            "messages": "消息列表（直接调用方式）",
            "model": "模型名（可选）",
            "temperature": "温度（可选）",
            "max_iter": "最大迭代次数（可选，默认 5）"
        },
        "output": {
            "ok": "是否成功",
            "data": "执行结果",
            "error": "错误信息"
        }
    }