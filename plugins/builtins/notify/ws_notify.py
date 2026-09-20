# plugins/builtins/notify/ws_notify.py
"""WebSocket 推送通知"""
import core


async def push(agent, channel_id: str, data: dict):
    try:
        await agent.system.call(core.Envelop(
            sender="builtins/notify/ws",
            receiver="os/_websocket",
            payload={
                "action": "push",
                "channel_id": channel_id,
                "data": data
            }
        ))
    except Exception as e:
        print(f"[ws_notify] 推送失败: {e}")