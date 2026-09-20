"""AICP Core — Envelop + Agent + plugins dict + route"""
import asyncio
import uuid
import json
import hmac
import hashlib
import os
from typing import Dict, Optional, Callable
from datetime import datetime


# ============================================================
# 回调签名密钥（从环境变量读，不硬编码）
# ============================================================
CALLBACK_SECRET = os.environ.get("AICP_CALLBACK_SECRET", "")


def sign_payload(payload: dict) -> str:
    """计算 payload 签名"""
    if not CALLBACK_SECRET:
        return ""
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hmac.new(CALLBACK_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()


def verify_signature(payload: dict, signature: str) -> bool:
    """验证签名"""
    if not CALLBACK_SECRET or not signature:
        return False
    expected = sign_payload(payload)
    return hmac.compare_digest(expected, signature)


class Envelop:
    __slots__ = (
        "sender", "receiver", "intent", "payload",
        "trace_id", "message_id", "channel_id", "ttl", "meta",
        "created_at", "path_history"
    )
    
    def __init__(self, sender="", receiver="", intent="", payload=None,
                 channel_id="", ttl=10, meta=None):
        self.sender = sender
        self.receiver = receiver
        self.intent = intent
        self.payload = payload if payload is not None else {}
        self.trace_id = f"tr_{uuid.uuid4().hex[:8]}"
        self.message_id = f"msg_{uuid.uuid4().hex[:6]}"
        self.channel_id = channel_id
        self.ttl = ttl
        self.meta = meta if meta is not None else {}
        self.created_at = datetime.now().isoformat()
        self.path_history = []

    def to_dict(self) -> dict:
        return {
            "sender": self.sender,
            "receiver": self.receiver,
            "intent": self.intent,
            "payload": self.payload,
            "trace_id": self.trace_id,
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "ttl": self.ttl,
            "meta": self.meta,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Envelop":
        env = cls(
            sender=data.get("sender", ""),
            receiver=data.get("receiver", ""),
            intent=data.get("intent", ""),
            payload=data.get("payload", {}),
            channel_id=data.get("channel_id", ""),
            ttl=data.get("ttl", 10),
            meta=data.get("meta", {}),
        )
        env.trace_id = data.get("trace_id", env.trace_id)
        env.message_id = data.get("message_id", env.message_id)
        env.created_at = data.get("created_at", env.created_at)
        return env


class Agent:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
    
    def get(self, key, default=None):
        return getattr(self, key, default)



class Agent:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
    
    def get(self, key, default=None):
        return getattr(self, key, default)


# 全局插件地址簿
plugins: Dict[str, Callable] = {}


# ============================================================
# 异步任务内部标记键（不对外暴露）
# ============================================================
_ASYNC_INTERNAL_KEYS = (
    "callback_receiver",
    "_async_started",
)


def _strip_async_meta(meta: dict) -> dict:
    """从 meta 中移除异步内部标记，防止插件透传时重复触发"""
    cleaned = {k: v for k, v in meta.items() if k not in _ASYNC_INTERNAL_KEYS}
    return cleaned


def _get_callback_token(agent: Agent = None) -> str:
    """从 agent.config 读 token，不硬编码"""
    if agent and hasattr(agent, 'config'):
        tokens = agent.config.get("tokens", [])
        if tokens:
            return tokens[0]
    return os.environ.get("AICP_TOKEN", "")


async def route(envelop: Envelop, agent: Agent = None, timeout: float = 1200.0) -> Optional[Envelop]:
    """引擎路由 — 支持同步、异步回调、回调确认三种模式
    
    三种模式：
    1. 同步：无特殊 meta，直接执行插件并等待结果
    2. 异步回调：meta 中有 callback_receiver，后台执行完成后回调
    3. 回调确认：meta 中有 is_callback，立即 ACK 并后台投递
    """
    if not envelop.receiver:
        envelop.payload = {"error": "Missing receiver"}
        return envelop
    
    if envelop.ttl <= 0:
        envelop.payload = {"error": "TTL expired"}
        return envelop
    
    envelop.ttl -= 1
    
    plugin = plugins.get(envelop.receiver)
    if not plugin:
        envelop.payload = {"error": f"Plugin not found: {envelop.receiver}"}
        return envelop
    
    if agent is None:
        agent = Agent()
    
    # ============================================================
    # 模式3：回调确认 — 收到回调消息，立即 ACK，后台投递
    # ============================================================
    if envelop.meta.get("is_callback"):
        asyncio.create_task(_deliver_callback(envelop, agent, timeout))
        return Envelop(
            sender=envelop.receiver,
            receiver=envelop.sender,
            payload={"ok": True, "received": True},
            meta={"is_ack": True, "trace_id": envelop.trace_id},
        )
    
    # ============================================================
    # 模式2：异步回调 — 发起方请求异步执行
    # ============================================================
    callback_receiver = envelop.meta.get("callback_receiver", "")
    if callback_receiver:
        # 创建后台任务
        asyncio.create_task(_execute_async(envelop, agent, callback_receiver, timeout))
        
        # 立即返回 processing
        return Envelop(
            sender=envelop.receiver,
            receiver=envelop.sender,
            payload={"ok": True, "status": "processing"},
            meta={"async": True, "trace_id": envelop.trace_id},
        )
    
    # ============================================================
    # 模式1：同步执行
    # ============================================================
    try:
        result = await asyncio.wait_for(plugin(envelop, agent), timeout=timeout)
        return result
    except asyncio.TimeoutError:
        envelop.payload = {"error": f"Plugin timeout after {timeout}s"}
        return envelop
    except Exception as e:
        envelop.payload = {"error": f"Plugin execution error: {str(e)[:200]}"}
        return envelop


async def _execute_async(envelop: Envelop, agent: Agent, callback_receiver: str, timeout: float):
    """后台执行插件，完成后回调"""
    
    # ★★★ 关键：执行插件前，清除异步内部标记 ★★★
    envelop.meta = _strip_async_meta(envelop.meta)
    
    try:
        plugin = plugins.get(envelop.receiver)
        if plugin:
            result = await asyncio.wait_for(plugin(envelop, agent), timeout=timeout)
        else:
            result = Envelop(payload={"error": f"Plugin not found: {envelop.receiver}"})
    except asyncio.TimeoutError:
        result = Envelop(payload={"error": f"Plugin timeout after {timeout}s"})
    except Exception as e:
        result = Envelop(payload={"error": f"Plugin execution error: {str(e)[:200]}"})
    
    if result is None:
        result = Envelop(payload={"error": "Plugin returned None"})
    
    # ============================================================
    # 构造回调消息
    # ============================================================
    result.sender = envelop.receiver
    result.receiver = callback_receiver
    result.trace_id = envelop.trace_id
    result.meta["is_callback"] = True
    result.meta["callback_original_receiver"] = envelop.receiver
    result.meta["trace_id"] = envelop.trace_id
    
    # 透传业务信息（不是异步标记）
    for key in ("remote_node", "remote_plugin", "callback_session_id"):
        if envelop.meta.get(key):
            result.meta[key] = envelop.meta[key]
    
    # session_id 兜底
    if not result.meta.get("callback_session_id") and envelop.meta.get("session_id"):
        result.meta["callback_session_id"] = envelop.meta["session_id"]
    
    # ============================================================
    # 发送回调
    # ============================================================
    if callback_receiver.startswith(("http://", "https://")):
        await _http_callback(callback_receiver, result, timeout, agent)
    elif "/" in callback_receiver:
        try:
            await route(result, agent, timeout=timeout)
        except Exception:
            pass
    else:
        await _http_callback(f"https://{callback_receiver}", result, timeout, agent)


async def _http_callback(url: str, envelop: Envelop, timeout: float, agent: Agent = None):
    """HTTP 回调：把结果 POST 到远程地址"""
    import aiohttp
    
    if "://" not in url:
        url = f"https://{url}"
    url = url.rstrip("/")
    if "/api/" not in url:
        url = f"{url}/api/builtins/agents/main_agent"
    
    # 签名（如果配置了 secret）
    signature = sign_payload(envelop.payload)
    if signature:
        envelop.meta["signature"] = signature
    
    # ★ token 从 agent.config 读，不硬编码
    token = _get_callback_token(agent)
    
    body = {
        "token": token,
        "payload": envelop.payload,
        "meta": envelop.meta,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=body,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                print(f"[callback] POST {url} -> {resp.status}")
    except Exception as e:
        print(f"[callback] FAILED: {url} -> {e}")


async def _deliver_callback(envelop: Envelop, agent: Agent, timeout: float):
    """后台投递回调消息到插件"""
    try:
        plugin = plugins.get(envelop.receiver)
        if plugin:
            await asyncio.wait_for(plugin(envelop, agent), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        print(f"[callback] deliver failed: {e}")


# 兼容旧代码
engine_route = route