"""MK Worker — 键鼠操控引擎"""
import asyncio
import threading
import time
import aiohttp

_engine_state = None


def _start_action_worker(state):
    def run():
        try:
            import pyautogui
            import pyperclip
            while state.get("_hooks_running"):
                actions = state.get("actions", [])
                if not actions:
                    time.sleep(0.05)
                    continue
                cmd = actions.pop(0)
                cmd_type = list(cmd.keys())[0] if cmd else ""
                if cmd_type == "move_to":
                    x, y = cmd["move_to"]
                    pyautogui.moveTo(x, y)
                elif cmd_type == "click":
                    pyautogui.click(button=cmd.get("click", "left"))
                elif cmd_type == "type":
                    text = cmd.get("type", "")
                    pyperclip.copy(text)
                    pyautogui.hotkey("ctrl", "v")
                elif cmd_type == "hotkey":
                    pyautogui.hotkey(*cmd.get("hotkey", []))
                elif cmd_type == "press":
                    pyautogui.press(cmd.get("press", ""))
                elif cmd_type == "wait":
                    time.sleep(cmd.get("wait", 1))
                elif cmd_type == "screenshot":
                    state["screenshot"] = pyautogui.screenshot()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


async def execute(envelop, agent):
    global _engine_state
    payload = envelop.payload
    if "payload" in payload and isinstance(payload.get("payload"), dict):
        payload = payload["payload"]
    action = payload.get("action", "status")
    engine_id = payload.get("engine_id", "mk_worker")
    base_url = agent.base_url.replace("0.0.0.0", "127.0.0.1")
    try:
        if action == "start":
            async with aiohttp.ClientSession() as session:
                await session.post(base_url + "/api/os/_pool", json={
                    "payload": {"action": "create", "engine_id": engine_id, "config": {"ttl": 1800}},
                })
                async with session.post(base_url + "/api/os/_pool", json={
                    "payload": {"action": "get", "engine_id": engine_id},
                }) as resp:
                    data = await resp.json()
                state = data.get("state", {})
                _engine_state = state
                state["_hooks_running"] = True
                state.setdefault("actions", [])
                _start_action_worker(state)
            envelop.payload = {"ok": True, "engine_id": engine_id}
        elif action == "status":
            envelop.payload = {"exists": _engine_state is not None, "actions_queued": len(_engine_state.get("actions", [])) if _engine_state else 0}
        elif action == "do":
            command = payload.get("command", {})
            if _engine_state:
                _engine_state.setdefault("actions", [])
                _engine_state["actions"].append(command)
                envelop.payload = {"ok": True, "queued": len(_engine_state["actions"])}
            else:
                envelop.payload = {"error": "MK engine not started"}
        elif action == "batch":
            commands = payload.get("commands", [])
            if _engine_state:
                _engine_state.setdefault("actions", [])
                _engine_state["actions"].extend(commands)
                envelop.payload = {"ok": True, "queued": len(_engine_state["actions"])}
            else:
                envelop.payload = {"error": "MK engine not started"}
        elif action == "stop":
            if _engine_state:
                _engine_state["_hooks_running"] = False
            async with aiohttp.ClientSession() as session:
                await session.post(base_url + "/api/os/_pool", json={
                    "payload": {"action": "destroy", "engine_id": engine_id},
                })
            _engine_state = None
            envelop.payload = {"ok": True}
        else:
            envelop.payload = {"error": "Unknown action: " + action}
        return envelop
    except Exception as e:
        envelop.payload = {"error": str(e)}
        return envelop
