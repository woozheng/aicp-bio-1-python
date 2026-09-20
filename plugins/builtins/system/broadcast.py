"""实时协同广播插件 — 房间管理 + 状态同步"""
import core
import time

_rooms = {}

def help():
    return {
        "route": "/api/builtins/system/broadcast",
        "actions": ["join", "leave", "broadcast", "sync_state", "get_state", "list_rooms"],
        "description": "实时协同广播 — 房间管理 + 操作同步",
    }

async def execute(envelop, agent):
    action = envelop.payload.get("action", "broadcast")
    if action == "join": return await _join(envelop, agent)
    elif action == "leave": return await _leave(envelop, agent)
    elif action == "broadcast": return await _broadcast(envelop, agent)
    elif action == "sync_state": return await _sync_state(envelop, agent)
    elif action == "get_state": return await _get_state(envelop, agent)
    elif action == "list_rooms": return await _list_rooms(envelop, agent)
    else: envelop.payload = {"error": f"Unknown action: {action}"}; return envelop

async def _join(envelop, agent):
    room_id = envelop.payload.get("room_id", "default")
    ws_id = envelop.meta.get("ws_id", "unknown")
    if room_id not in _rooms:
        _rooms[room_id] = {"created_at": time.time(), "members": {}, "state": {}, "history": []}
    room = _rooms[room_id]
    room["members"][ws_id] = {"joined_at": time.time(), "name": envelop.payload.get("name", ws_id)}
    agent.log.info(f"[Broadcast] JOIN: {ws_id} → room={room_id}, total={len(room['members'])}")
    await _push_to_room(agent, room_id, {"type": "member_joined", "ws_id": ws_id, "total": len(room["members"])}, exclude_ws=ws_id)
    envelop.payload = {"ok": True, "room_id": room_id, "members": list(room["members"].keys()), "state": room["state"], "history": room["history"][-50:]}
    return envelop

async def _leave(envelop, agent):
    room_id = envelop.payload.get("room_id", "default")
    ws_id = envelop.meta.get("ws_id", "unknown")
    if room_id in _rooms:
        _rooms[room_id]["members"].pop(ws_id, None)
        if not _rooms[room_id]["members"]: del _rooms[room_id]
        else:
            await _push_to_room(agent, room_id, {"type": "member_left", "ws_id": ws_id, "total": len(_rooms[room_id]["members"])}, exclude_ws=ws_id)
            agent.log.info(f"[Broadcast] LEAVE: {ws_id} ← room={room_id}, total={len(_rooms[room_id]['members'])}")
    envelop.payload = {"ok": True}; return envelop

async def _broadcast(envelop, agent):
    room_id = envelop.payload.get("room_id", "default")
    ws_id = envelop.meta.get("ws_id", "unknown")
    action_data = envelop.payload.get("data", {})
    action_type = action_data.get("action", "unknown")
    payload = action_data.get("payload", {})
    agent.log.info(f"[Broadcast] SYNC: {ws_id} → room={room_id}, action={action_type}")
    if room_id in _rooms:
        _rooms[room_id]["history"].append({"time": time.time(), "sender": ws_id, "action": action_type, "payload": payload})
        if len(_rooms[room_id]["history"]) > 200: _rooms[room_id]["history"] = _rooms[room_id]["history"][-200:]
    await _push_to_room(agent, room_id, {"type": "sync", "action": action_type, "payload": payload, "sender_ws_id": ws_id, "timestamp": time.time()}, exclude_ws=ws_id)
    envelop.payload = {"ok": True, "room_id": room_id}; return envelop

async def _sync_state(envelop, agent):
    room_id = envelop.payload.get("room_id", "default")
    state = envelop.payload.get("state", {})
    if room_id in _rooms: _rooms[room_id]["state"] = state
    await _push_to_room(agent, room_id, {"type": "state_sync", "state": state, "timestamp": time.time()})
    agent.log.info(f"[Broadcast] STATE_SYNC: room={room_id}")
    envelop.payload = {"ok": True, "room_id": room_id}; return envelop

async def _get_state(envelop, agent):
    room_id = envelop.payload.get("room_id", "default")
    room = _rooms.get(room_id, {})
    envelop.payload = {"ok": True, "room_id": room_id, "state": room.get("state", {}), "members": list(room.get("members", {}).keys())}
    return envelop

async def _list_rooms(envelop, agent):
    rooms = [{"room_id": rid, "members": len(r["members"]), "created_at": r["created_at"]} for rid, r in _rooms.items()]
    envelop.payload = {"rooms": rooms}; return envelop

async def _push_to_room(agent, room_id, data, exclude_ws=None):
    agent.log.info(f"[Broadcast] PUSH: room={room_id}, type={data.get('type')}, exclude={exclude_ws}")
    await agent.system.call(core.Envelop(receiver="os/_websocket", payload={"action": "push", "channel_id": f"room_{room_id}", "data": data}))