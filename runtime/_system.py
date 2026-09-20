import logging
import re
import asyncio
import core
import sys


class SystemServer:
    """Server 版 — 无 GUI，无记忆"""
    def __init__(self, loop, route_fn):
        self.loop = loop
        self._route = route_fn
        self._agent = None

    def bind(self, agent):
        self._agent = agent

    async def call(self, envelop):
        # 直接执行插件，不激活记忆、不提取记忆
        response = await self._route(envelop, self._agent)
        return response


if "--server" not in sys.argv:
    import threading
    from plugins.builtins.system.gui import SystemGUI

    class System(SystemServer):
        """完整版 — 含 GUI 和鼠标钩子"""
        def __init__(self, loop, route_fn, on_shutdown=None):
            super().__init__(loop, route_fn)
            self._gui = SystemGUI()
            for name in dir(self._gui):
                if name.startswith('_'):
                    continue
                attr = getattr(self._gui, name)
                if callable(attr):
                    setattr(self, name, attr)
            self._mouse_listener = None
            self._mouse_callback = None
            self.on_shutdown = on_shutdown

        def start_gui(self):
            self._gui.start()

        def start_mouse_hook(self, callback):
            if self._mouse_listener is not None:
                return
            from pynput import mouse
            self._mouse_callback = callback
            self._mouse_listener = mouse.Listener(on_click=self._on_mouse_event)
            self._mouse_listener.start()

        def _on_mouse_event(self, x, y, button, pressed):
            if self._mouse_callback:
                try:
                    self._mouse_callback(x, y, button, pressed)
                except Exception as e:
                    print(f"[System] 鼠标回调异常: {e}")

        def stop_mouse_hook(self):
            if self._mouse_listener:
                try:
                    self._mouse_listener.stop()
                except Exception:
                    pass
                self._mouse_listener = None
else:
    System = SystemServer