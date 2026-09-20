"""屏幕数字探头 API 路由 — Vision Only"""
import time
import ctypes
import json
import asyncio
from ctypes import wintypes
from pathlib import Path
from core import Envelop
from .probe import ProbeManager

_manager = None
_config_file = Path("data/probes_config.json")


def get_manager(agent):
    global _manager
    if _manager is None:
        _manager = ProbeManager(agent)
        _manager.load_saved_probes()
    return _manager


def window_from_point(x, y):
    pt = wintypes.POINT(x, y)
    return ctypes.windll.user32.WindowFromPoint(pt)


def get_top_parent(hwnd):
    while True:
        parent = ctypes.windll.user32.GetParent(hwnd)
        if parent == 0:
            break
        hwnd = parent
    return hwnd


def get_window_info(hwnd):
    if not hwnd:
        return {}
    class_name = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, class_name, 255)
    title = ctypes.create_unicode_buffer(1024)
    ctypes.windll.user32.GetWindowTextW(hwnd, title, 1023)
    return {
        "hwnd": hwnd,
        "class": class_name.value,
        "title": title.value,
    }


async def execute(envelop, agent):
    payload = envelop.payload.get("payload", envelop.payload)
    action = payload.get("action", "list")
    mgr = get_manager(agent)

    # ========== list ==========
    if action == "list":
        probes = mgr.list_probes()
        for p in probes:
            probe = mgr.get(p["id"])
            if probe and probe.history:
                p["last_result"] = probe.history[-1]
        envelop.payload = {"probes": probes}
        return envelop

    # ========== create_probe ==========
    elif action == "create_probe":
        # 1. 框选
        sel = await agent.system.start_selection("框选监控区域", fullscreen=True)
        if sel.get("cancelled"):
            envelop.payload = {"cancelled": True}
            return envelop
        
        s = sel["selection"]
        region_info = {"w": s["x2"] - s["x1"], "h": s["y2"] - s["y1"]}
        
        # 2. 获取窗口信息
        cx = (s["x1"] + s["x2"]) // 2
        cy = (s["y1"] + s["y2"]) // 2
        hwnd = window_from_point(cx, cy)
        hwnd = get_top_parent(hwnd)
        win_info = get_window_info(hwnd)
        window_title = win_info.get("title", "")
        
        # 3. 弹窗配置
        from .views.config_dialog import show_config_dialog
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: show_config_dialog(region_info, window_title)
        )
        
        if not result:
            envelop.payload = {"cancelled": True}
            return envelop
        
        # 4. 创建探头
        probe_id = mgr.create(
            name=result["name"],
            region={"x1": s["x1"], "y1": s["y1"], "x2": s["x2"], "y2": s["y2"]},
            interval=result["interval"],
            rules=[result["rule"]],
            mode="vision",
            hwnd_target=hwnd
        )
        
        envelop.payload = {
            "id": probe_id,
            "name": result["name"],
            "mode": "vision",
            "region": {"x1": s["x1"], "y1": s["y1"], "x2": s["x2"], "y2": s["y2"]},
        }
        return envelop

    # ========== add_rule ==========
    elif action == "add_rule":
        probe_id = payload.get("id")
        rule = payload.get("rule", {})
        if not probe_id or not rule:
            envelop.payload = {"error": "缺少 id 或 rule"}
            return envelop
        rule.setdefault("type", "vision_analyze")
        rule.setdefault("trigger", "always")
        mgr.add_rule(probe_id, rule)
        envelop.payload = {"ok": True}
        return envelop

    # ========== remove_rule ==========
    elif action == "remove_rule":
        probe_id = payload.get("id")
        rule_index = payload.get("rule_index", -1)
        if not probe_id or rule_index < 0:
            envelop.payload = {"error": "缺少 id 或 rule_index"}
            return envelop
        ok = mgr.remove_rule(probe_id, rule_index)
        envelop.payload = {"ok": ok}
        return envelop

    # ========== delete ==========
    elif action == "delete":
        probe_id = payload.get("id")
        if not probe_id:
            envelop.payload = {"error": "missing id"}
            return envelop
        mgr.delete(probe_id)
        envelop.payload = {"ok": True}
        return envelop

    # ========== start ==========
    elif action == "start":
        probe_id = payload.get("id")
        if not probe_id:
            envelop.payload = {"error": "missing id"}
            return envelop
        mgr.start(probe_id)
        envelop.payload = {"ok": True}
        return envelop

    # ========== stop ==========
    elif action == "stop":
        probe_id = payload.get("id")
        if not probe_id:
            envelop.payload = {"error": "missing id"}
            return envelop
        mgr.stop(probe_id)
        envelop.payload = {"ok": True}
        return envelop

    # ========== stop_all ==========
    elif action == "stop_all":
        for probe in mgr.list_probes():
            mgr.stop(probe["id"])
        envelop.payload = {"ok": True}
        return envelop

    # ========== start_all ==========
    elif action == "start_all":
        for probe in mgr.list_probes():
            if not probe["running"]:
                mgr.start(probe["id"])
        envelop.payload = {"ok": True}
        return envelop

    # ========== clear_all ==========
    elif action == "clear_all":
        for probe in mgr.list_probes():
            mgr.delete(probe["id"])
        envelop.payload = {"ok": True}
        return envelop

    # ========== status ==========
    elif action == "status":
        probe_id = payload.get("id")
        if not probe_id:
            envelop.payload = {"error": "missing id"}
            return envelop
        probe = mgr.get(probe_id)
        if probe:
            status = probe.status()
            status["vision_history"] = probe.history[-10:]
            envelop.payload = status
        else:
            envelop.payload = {"error": "not found"}
        return envelop

    # ========== get_history ==========
    elif action == "get_history":
        probe_id = payload.get("id")
        if not probe_id:
            envelop.payload = {"error": "missing id"}
            return envelop
        probe = mgr.get(probe_id)
        envelop.payload = {
            "probe_id": probe_id,
            "history": probe.history if probe else []
        }
        return envelop

    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop