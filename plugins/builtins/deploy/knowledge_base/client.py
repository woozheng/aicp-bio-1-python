"""知识库客户端 — 供其他插件调用的统一接口"""
import core
import asyncio
from pathlib import Path

PROJECT = Path(__file__).parent.name


# ============================================================
# 同步版（等待结果）
# ============================================================

async def save_to_kb(agent, content, title, source, type="text", metadata=None, auto=True):
    """统一的知识库存入接口（等待完成）"""
    result = await agent.system.call(core.Envelop(
        sender=source,
        receiver=f"builtins/{PROJECT}/main",
        payload={
            "action": "publish",
            "params": {
                "topic": type,
                "content": content,
                "title": title,
                "source": source,
                "type": type,
                "metadata": metadata or {},
                "auto": auto
            }
        }
    ))
    return result.payload if result else {"ok": False, "error": "无响应"}


async def auto_save(agent, content, source, title=None, type="auto", metadata=None):
    """自动补货（带过滤/去重/质量检查）"""
    result = await agent.system.call(core.Envelop(
        sender=source,
        receiver=f"builtins/{PROJECT}/main",
        payload={
            "action": "auto_save",
            "params": {
                "content": content,
                "source": source,
                "title": title or source,
                "type": type,
                "metadata": metadata or {}
            }
        }
    ))
    return result.payload if result else {"ok": False, "error": "无响应"}


async def search_kb(agent, query, type=None):
    """搜索知识库"""
    params = {"query": query}
    if type:
        params["type"] = type
    result = await agent.system.call(core.Envelop(
        sender="client",
        receiver=f"builtins/{PROJECT}/main",
        payload={"action": "search", "params": params}
    ))
    return result.payload if result else {"ok": False, "error": "无响应"}


async def ask_kb(agent, query):
    """知识库问答"""
    result = await agent.system.call(core.Envelop(
        sender="client",
        receiver=f"builtins/{PROJECT}/main",
        payload={"action": "ask", "params": {"query": query}}
    ))
    return result.payload if result else {"ok": False, "error": "无响应"}


async def get_stats(agent):
    """获取知识库统计"""
    result = await agent.system.call(core.Envelop(
        sender="client",
        receiver=f"builtins/{PROJECT}/main",
        payload={"action": "stats"}
    ))
    return result.payload if result else {"ok": False, "error": "无响应"}


# ============================================================
# 异步版（不等待结果，不阻塞调用方）
# ============================================================

def save_to_kb_async(agent, content, title, source, type="text", metadata=None, auto=True):
    """异步存入知识库（不阻塞，立即返回）"""
    asyncio.create_task(_save_to_kb_async(
        agent, content, title, source, type, metadata, auto
    ))


async def _save_to_kb_async(agent, content, title, source, type, metadata, auto):
    """后台实际存入逻辑"""
    try:
        await agent.system.call(core.Envelop(
            sender=source,
            receiver=f"builtins/{PROJECT}/main",
            payload={
                "action": "auto_save",
                "params": {
                    "topic": type,
                    "content": content,
                    "title": title,
                    "source": source,
                    "type": type,
                    "metadata": metadata or {},
                    "auto": auto
                }
            }
        ))
        print(f"[kb_client] ✅ 异步存入成功: {title[:30]}...")
    except Exception as e:
        print(f"[kb_client] ❌ 异步存入失败: {e}")


def auto_save_async(agent, content, source, title=None, type="auto", metadata=None):
    """异步自动补货（不阻塞，立即返回）"""
    asyncio.create_task(_auto_save_async(
        agent, content, source, title, type, metadata
    ))


async def _auto_save_async(agent, content, source, title, type, metadata):
    """后台实际自动补货逻辑"""
    try:
        await agent.system.call(core.Envelop(
            sender=source,
            receiver=f"builtins/{PROJECT}/main",
            payload={
                "action": "auto_save",
                "params": {
                    "content": content,
                    "source": source,
                    "title": title or source,
                    "type": type,
                    "metadata": metadata or {}
                }
            }
        ))
        print(f"[kb_client] ✅ 异步自动补货成功: {title or source}")
    except Exception as e:
        print(f"[kb_client] ❌ 异步自动补货失败: {e}")