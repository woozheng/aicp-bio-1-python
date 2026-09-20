"""MKPopup — 无焦点浮动按钮栏（事件驱动版）"""
import wx
import asyncio
import threading
import time

class MKPopup:
    @staticmethod
    async def show(actions, x, y, timeout=6.0):
        loop = asyncio.get_event_loop()
        result_holder = {}
        ready_event = threading.Event()
        popup_holder = {}

        def on_done(result):
            # print(f"[MKPopup] on_done: {result}")
            result_holder["result"] = result

        def create():
            # print(f"[MKPopup] creating frame at ({x}, {y})")
            popup = _PopupFrame(actions, x, y, timeout, on_done)
            popup_holder["popup"] = popup
            ready_event.set()
            # print(f"[MKPopup] frame created")

        wx.CallAfter(create)
        # print(f"[MKPopup] waiting for creation...")
        await loop.run_in_executor(None, ready_event.wait, 2)
        # print(f"[MKPopup] creation done, popup={popup_holder.get('popup')}")

        popup = popup_holder.get("popup")
        if not popup:
            # print(f"[MKPopup] popup is None, returning dismissed")
            return {"dismissed": True}

        # print(f"[MKPopup] waiting for result...")
        await loop.run_in_executor(None, popup._result_event.wait, timeout + 2)
        # print(f"[MKPopup] result: {result_holder}")
        return result_holder.get("result", {"dismissed": True})


class _PopupFrame(wx.Frame):
    def __init__(self, actions, x, y, timeout, callback):
        pad = 6
        btn_w = 100
        btn_h = 24

        # 🔥 偏移量调节器（偏上就减，偏下就加）
        offset_x = 0   # 水平微调
        offset_y = -1  # 垂直微调（偏下就减）

        total_w = btn_w + pad * 2
        total_h = btn_h + pad * 2

        style = wx.STAY_ON_TOP | wx.FRAME_NO_TASKBAR | wx.BORDER_NONE
        super().__init__(None, pos=(x, y), size=(total_w, total_h), style=style)

        self._callback = callback
        self._result_event = threading.Event()
        self._done = False
        self._created_at = time.time()

        self.SetBackgroundColour("#1e1e1e")

        btn_x = pad + offset_x
        btn_y = pad + offset_y

        for act in actions:
            btn = wx.Button(self, label=act["label"], size=(btn_w, btn_h),
                            pos=(btn_x, btn_y), style=wx.BORDER_NONE)
            btn.SetBackgroundColour("#252525")
            btn.SetForegroundColour("#ffffff")
            btn.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL, faceName="Consolas"))
            btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))

            btn.Bind(wx.EVT_ENTER_WINDOW, lambda e, b=btn: b.SetBackgroundColour("#3a3a3a"))
            btn.Bind(wx.EVT_LEAVE_WINDOW, lambda e, b=btn: b.SetBackgroundColour("#252525"))
            btn.Bind(wx.EVT_BUTTON, lambda e, v=act["value"]: self._on_select(v))
            break

        self.ShowWithoutActivating()

        self._check_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_check_mouse, self._check_timer)
        self._check_timer.Start(200)

        if timeout > 0:
            wx.CallLater(int(timeout * 1000), self._on_timeout)

    def _on_select(self, value):
        self._finish({"selected": value})

    def _on_timeout(self):
        self._finish({"dismissed": True})

    def _on_check_mouse(self, event):
        if self._done:
            return
        
        # 🔥 前 0.5 秒不检测
        if time.time() - self._created_at < 0.5:
            return
        
        mx, my = wx.GetMousePosition()
        bx, by = self.GetPosition()
        bw, bh = self.GetSize()
        
        # 🔥 窗口还没稳定，不检测
        if bw == 0 or bh == 0:
            return
        
        margin = 20
        if not (bx - margin <= mx <= bx + bw + margin and by - margin <= my <= by + bh + margin):
            self._finish({"dismissed": True})

    def _finish(self, result):
        if self._done:
            return
        self._done = True
        self._check_timer.Stop()
        try:
            self._result_event.set()
            self._callback(result)
        except Exception:
            pass
        try:
            self.Close()
        except Exception:
            pass