"""知识库消息总线 — 收消息 → 自动存入知识库"""
import json
import core
from pathlib import Path
from datetime import datetime

PROJECT = Path(__file__).parent.name


def help():
    return {
        "route": "/api/builtins/knowledge_base/bus",
        "actions": {
            "publish": {"params": {"topic": "主题", "content": "内容", "source": "来源", "type": "类型"}},
            "status": {}
        }
    }


async def execute(envelop, agent):
    payload = envelop.payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except:
            payload = {"action": payload}
    if isinstance(payload, dict) and "payload" in payload:
        payload = payload["payload"]

    action = payload.get("action", "publish")
    params = payload.get("params", {})
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except:
            params = {"content": params}

    if action == "publish":
        return await _publish(params, agent)
    elif action == "status":
        return await _status()

    return {"ok": False, "error": f"未知操作: {action}"}


async def _publish(params, agent):
    topic = params.get("topic", "default")
    content = params.get("content", "")
    source = params.get("source", "unknown")
    entry_type = params.get("type", "text")
    title = params.get("title", source)
    metadata = params.get("metadata", {})
    auto = params.get("auto", True)

    if not content or len(content) < 20:
        return {"ok": False, "error": "内容太短"}

    print(f"[bus] 收到消息: topic={topic}, source={source}")

    # 生成摘要（可选）
    summary = ""
    if not auto and agent.llm and len(content) > 200:
        try:
            summary = await agent.llm.chat([
                {"role": "system", "content": "请为以下内容生成一个简短的摘要（50字以内）。"},
                {"role": "user", "content": content[:1500]}
            ], role="reasoning")
            summary = summary.strip()
        except:
            pass

    if not summary:
        summary = content[:200] + "..." if len(content) > 200 else content

    # 存入知识库（调用 main）
    result = await agent.system.call(core.Envelop(
        sender=f"builtins/{PROJECT}/bus",
        receiver=f"builtins/{PROJECT}/main",
        payload={
            "action": "save",
            "params": {
                "content": content,
                "title": f"[{topic}] {title}",
                "type": entry_type,
                "source": f"bus:{source}",
                "metadata": {
                    "topic": topic,
                    "summary": summary,
                    "source_plugin": source,
                    "timestamp": datetime.now().isoformat(),
                    **metadata
                }
            }
        }
    ))

    return {
        "ok": True,
        "summary": summary,
        "kb_result": result.payload if result else None,
        "topic": topic,
        "source": source
    }


async def _status():
    return {
        "ok": True,
        "topics": ["default"],
        "message": "知识库消息总线运行中"
    }