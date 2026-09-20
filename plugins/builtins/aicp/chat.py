# plugins/builtins/aicp/chat.py
"""AICP Chat API — 远端 chatEnvelop 端点 / 能力探测"""

import platform
import subprocess
import requests as _requests

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

    # ===== 正常 chat =====
    messages = envelop.payload.get("messages") or envelop.payload.get("payload", {}).get("messages", [])

    model = envelop.payload.get("model")
    temperature = envelop.payload.get("temperature")
    max_iter = envelop.payload.get("max_iter", 5)

    if not hasattr(agent, 'aicp_llm'):
        agent.aicp_llm = AICP_LLM(agent.config)

    result = await agent.aicp_llm.chatEnvelop(
        messages,
        model=model,
        role="code",  # ← 直接写死
        temperature=temperature,
        max_iter=max_iter
    )

    payload = result.payload if hasattr(result, 'payload') else result
    return Envelop(
        receiver=envelop.sender,
        payload={
            "ok": payload.get("ok", True),
            "data": payload.get("data", ""),
            "error": payload.get("error", "")
        }
    )


async def _detect_capabilities(agent):
    """探测本节点能力，返回 dict"""

    # 网络探测
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

    # GPU 探测
    has_gpu = False
    gpu_model = ""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
            encoding='utf-8', errors='ignore'  # ← 加这两
        )
        if result.returncode == 0:
            has_gpu = True
            gpu_model = result.stdout.strip().split("\n")[0]
    except Exception:
        pass

    # 模型列表（从 agent.config 读取）
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