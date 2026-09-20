"""MK 项目入口 — 选中文字弹 popup + 魔法鼠标"""
import threading
import asyncio
import core
import time
from .state import get_state
from .clipboard import start_monitor, stop_monitor  # 🔥 导入新函数


async def execute(envelop, agent):
    payload = envelop.payload
    if "payload" in payload and isinstance(payload.get("payload"), dict):
        payload = payload["payload"]
    action = payload.get("action", "init")
    
    if action == "init":
        return await _mount(envelop, agent)
    elif action == "start_analyst":
        return await _start_analyst(envelop, agent)
    elif action == "stop_analyst":
        return await _stop_analyst(envelop, agent)
    elif action == "magic_start":
        return await _magic_start(envelop, agent)
    elif action == "magic_stop":
        return await _magic_stop(envelop, agent)
    elif action == "magic_select_done":
        return await _magic_select_done(envelop, agent)
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop


# ═══════════════════════════════════════════
# 挂载
# ═══════════════════════════════════════════

async def _mount(envelop, agent):
    state = get_state()
    state.agent = agent
    state.base_url = agent.base_url.replace("0.0.0.0", "127.0.0.1")
    
    menu_env = core.Envelop(
        sender="builtins/mk/_init",
        receiver="shell/_tray",
        payload={
            "action": "add_menu",
            "app_id": "mk",
            "label": "MK 助手",
            "items": [
                {"label": "启动 Analyst", "action": "start_analyst", "receiver": "builtins/mk/_init"},
                {"label": "停止 Analyst", "action": "stop_analyst", "receiver": "builtins/mk/_init"},
                {"label": "---", "action": "", "receiver": ""},
                {"label": "魔法鼠标 - 启动", "action": "magic_start", "receiver": "builtins/mk/_init"},
                {"label": "魔法鼠标 - 停止", "action": "magic_stop", "receiver": "builtins/mk/_init"},
            ],
        },
    )
    await agent.system.call(menu_env)
    await _start_analyst(envelop, agent)
    
    agent.log.info("[MK] Mounted and started")
    envelop.payload = {"ok": True}
    return envelop


# ═══════════════════════════════════════════
# Analyst（划词弹窗）
# ═══════════════════════════════════════════

async def _start_analyst(envelop, agent):
    state = get_state()
    state.agent = agent
    
    from .panel import get_panel
    from .prompts import ACTION_PROMPTS
    from .clipboard import process_clipboard, on_mouse
    
    panel = get_panel()
    
    def _on_execute():
        content = panel.content_text.GetValue().strip()
        if not content:
            panel.status("请先划选文字，或手动粘贴内容")
            panel.set_processing(False)
            return
        
        intent = panel.intent_entry.GetValue().strip()
        if not intent:
            intent = ACTION_PROMPTS.get("auto", "")
        
        full_text = f"{intent}\n\n内容：{content}"
        
        panel.set_processing(True)
        asyncio.run_coroutine_threadsafe(
            process_clipboard(0, 0, full_text),
            agent.loop
        )
    panel.bind(on_execute=_on_execute)
    
    # 🔥 使用安全的启动函数
    start_monitor()
    
    # 启动鼠标监听
    from pynput import mouse as pynput_mouse
    listener = pynput_mouse.Listener(on_click=on_mouse)
    listener.start()
    print("[MK] 鼠标监听已启动 (pynput)")

    import keyboard
    from .memory_capture import memory_capture_clipboard, memory_capture_screenshot

    keyboard.add_hotkey('ctrl+f11', lambda: asyncio.run_coroutine_threadsafe(
        memory_capture_clipboard(agent), agent.loop
    ))
    keyboard.add_hotkey('ctrl+f12', lambda: asyncio.run_coroutine_threadsafe(
        memory_capture_screenshot(agent), agent.loop
    ))
    print("[MK] Ctrl+F11（存剪贴板）Ctrl+F12（存截屏）")
    
    agent.log.info("[MK] Analyst started")
    envelop.payload = {"ok": True, "status": "analyst started"}
    return envelop


async def _stop_analyst(envelop, agent):
    """完全停止 Analyst（包括监控线程）"""
    state = get_state()
    
    # 🔥 停止监控线程
    stop_monitor()
    
    # 停止鼠标监听
    try:
        agent.system.stop_mouse_hook()
    except:
        pass
    
    # 注销热键
    import keyboard
    try:
        keyboard.remove_hotkey('ctrl+f11')
        keyboard.remove_hotkey('ctrl+f12')
        print("[MK] 热键已注销 (Ctrl+F11, Ctrl+F12)")
    except:
        pass
    
    agent.log.info("[MK] Analyst stopped")
    envelop.payload = {"ok": True, "status": "analyst stopped"}
    return envelop


# ═══════════════════════════════════════════
# 魔法鼠标
# ═══════════════════════════════════════════

async def _magic_start(envelop, agent):
    """启动魔法鼠标"""
    state = get_state()
    
    # 🔥 只设置标志，不创建任何窗口
    state.magic_active = True
    state.magic_stop_requested = False
    state.magic_last_image = None
    
    agent.log.info("[MK] 魔法鼠标已启动")
    envelop.payload = {"status": "started", "cursor": "golden"}
    return envelop


async def _magic_stop(envelop, agent):
    """停止魔法鼠标 - 只设置停止标志"""
    state = get_state()
    
    # 🔥 设置停止标志
    state.magic_stop_requested = True
    state.magic_active = False
    
    # 🔥 关闭所有魔法卡片
    from .magic import MagicCard
    MagicCard.close_all()
    
    # 🔥 等待一小段时间让循环退出
    await asyncio.sleep(0.3)
    
    agent.log.info("[MK] 魔法鼠标已停止")
    envelop.payload = {"status": "stopped", "cursor": "normal"}
    return envelop


async def _magic_select_done(envelop, agent):
    """框选完成，调 Vision Agent - 这个函数才是真正创建窗口的地方"""
    state = get_state()
    selection = envelop.payload.get("selection", {})
    
    if not selection:
        envelop.payload = {"error": "缺少 selection"}
        return envelop
    
    from .magic import _capture, LoadingDot, call_vision, run_card_loop
    
    image_b64 = _capture(selection)
    if not image_b64:
        envelop.payload = {"error": "截图失败"}
        return envelop
    
    state.magic_last_image = image_b64
    state.magic_selection = selection
    
    channel = f"magic_{int(time.time())}"
    
    loading = LoadingDot(selection)
    loading.show()
    
    vr = await call_vision(agent, image_b64, "分析这张截图的内容",
                           channel=channel, reset=True, allow_actions=False,
                           region=selection)
    loading.hide()
    
    if vr:
        body = f"📸 {vr['result']}\n\n{vr.get('thinking', '')}"
        # 🔥 创建卡片窗口和循环
        asyncio.create_task(run_card_loop(agent, state, "分析结果", body, channel))
    
    envelop.payload = {"done": True}
    return envelop