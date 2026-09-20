"""
System GUI — 框选 / 基础对话框 (wx 版，极简)
"""
import asyncio
import wx
import ctypes
from runtime.wx_utils import get_app


class SystemGUI:
    def __init__(self):
        self._ready = False
        self._selection_window = None

    def start(self):
        get_app()
        self._ready = True

    # ── 框选 ──

    async def start_selection(self, intent: str = "", fullscreen: bool = False) -> dict:
        event = asyncio.Event()
        result_holder = {}

        def on_done(result):
            result_holder["result"] = result
            event.set()

        wx.CallAfter(self._show_selection, intent, fullscreen, on_done)
        await event.wait()
        return result_holder.get("result", {"cancelled": True})

    def _show_selection(self, intent, fullscreen, callback):
        if fullscreen:
            left = ctypes.windll.user32.GetSystemMetrics(76)
            top = ctypes.windll.user32.GetSystemMetrics(77)
            width = ctypes.windll.user32.GetSystemMetrics(78)
            height = ctypes.windll.user32.GetSystemMetrics(79)
        else:
            left = 0
            top = 0
            width = ctypes.windll.user32.GetSystemMetrics(0)
            height = ctypes.windll.user32.GetSystemMetrics(1)

        win = wx.Frame(None, pos=(left, top), size=(width, height),
                       style=wx.STAY_ON_TOP | wx.FRAME_NO_TASKBAR)
        win.SetBackgroundColour("#000000")
        win.SetTransparent(60)
        win.SetCursor(wx.Cursor(wx.CURSOR_CROSS))

        start_pos = [None]
        rect = [None]
        released = [False]

        hint_text = f"{intent} — 拖拽框选 | ESC 取消" if intent else "拖拽选择屏幕区域 | ESC 取消"

        def on_paint(event):
            dc = wx.PaintDC(win)
            dc.Clear()
            gc = wx.GraphicsContext.Create(dc)
            gc.SetFont(wx.Font(18, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL),
                       wx.Colour(255, 255, 255))
            tw, th = gc.GetTextExtent(hint_text)
            gc.DrawText(hint_text, (width - tw) / 2, (height - th) / 2)
            if rect[0]:
                x1, y1, x2, y2 = rect[0]
                gc.SetPen(wx.Pen(wx.Colour(129, 140, 248), 3))
                gc.SetBrush(wx.Brush(wx.Colour(0, 0, 0, 0)))
                gc.DrawRectangle(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

        def on_down(event):
            start_pos[0] = (event.GetX(), event.GetY())
            rect[0] = None
            win.Refresh()

        def on_move(event):
            if start_pos[0] and event.Dragging():
                x1, y1 = start_pos[0]
                rect[0] = (x1, y1, event.GetX(), event.GetY())
                win.Refresh()

        def do_release(result):
            if released[0]:
                return
            released[0] = True
            win.Hide()
            wx.CallAfter(win.Destroy)
            callback(result)

        def on_up(event):
            if not start_pos[0]:
                return
            x1, y1 = start_pos[0]
            x2, y2 = event.GetX(), event.GetY()
            if abs(x2 - x1) < 10 or abs(y2 - y1) < 10:
                do_release({"cancelled": True})
                return
            abs_x1 = min(x1, x2) + left
            abs_y1 = min(y1, y2) + top
            abs_x2 = max(x1, x2) + left
            abs_y2 = max(y1, y2) + top
            do_release({"selection": {"x1": abs_x1, "y1": abs_y1, "x2": abs_x2, "y2": abs_y2}})

        def on_esc(event):
            if event.GetKeyCode() == wx.WXK_ESCAPE:
                do_release({"cancelled": True})

        win.Bind(wx.EVT_PAINT, on_paint)
        win.Bind(wx.EVT_LEFT_DOWN, on_down)
        win.Bind(wx.EVT_MOTION, on_move)
        win.Bind(wx.EVT_LEFT_UP, on_up)
        win.Bind(wx.EVT_KEY_DOWN, on_esc)

        win.Show()
        try:
            hwnd = win.GetHandle()
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except:
            pass
        win.Raise()
        win.SetFocus()

    # ── 对话框 ──

    async def show_input_dialog(self, title: str, prompt: str, initialvalue: str = "") -> str | None:
        event = asyncio.Event()
        result_holder = {}

        def on_done(result):
            result_holder["result"] = result
            event.set()

        wx.CallAfter(self._show_input_dialog, title, prompt, initialvalue, on_done)
        await event.wait()
        return result_holder.get("result")

    def _show_input_dialog(self, title, prompt, initialvalue, callback):
        dlg = wx.TextEntryDialog(None, prompt, title, initialvalue)
        if dlg.ShowModal() == wx.ID_OK:
            callback(dlg.GetValue())
        else:
            callback(None)
        dlg.Destroy()

    async def show_message(self, title: str, message: str, style: str = "info") -> bool:
        event = asyncio.Event()
        result_holder = {}

        def on_done(result):
            result_holder["result"] = result
            event.set()

        wx.CallAfter(self._show_message, title, message, style, on_done)
        await event.wait()
        return result_holder.get("result", True)

    def _show_message(self, title, message, style, callback):
        flags = {
            "info": wx.OK | wx.ICON_INFORMATION,
            "warning": wx.OK | wx.ICON_WARNING,
            "error": wx.OK | wx.ICON_ERROR,
            "question": wx.YES_NO | wx.ICON_QUESTION,
        }.get(style, wx.OK)
        dlg = wx.MessageDialog(None, message, title, flags)
        result = dlg.ShowModal() == wx.ID_YES if style == "question" else True
        dlg.Destroy()
        callback(result)

    async def show_choice_dialog(self, title: str, message: str, choices: list) -> str | None:
        event = asyncio.Event()
        result_holder = {}

        def on_done(result):
            result_holder["result"] = result
            event.set()

        wx.CallAfter(self._show_choice_dialog, title, message, choices, on_done)
        await event.wait()
        return result_holder.get("result")

    def _show_choice_dialog(self, title, message, choices, callback):
        dlg = wx.SingleChoiceDialog(None, message, title, choices)
        if dlg.ShowModal() == wx.ID_OK:
            callback(dlg.GetStringSelection())
        else:
            callback(None)
        dlg.Destroy()

    def hide_all(self):
        if self._selection_window:
            try:
                self._selection_window.Close()
            except Exception:
                pass
        self._selection_window = None


_gui_instance = None


def get_gui() -> SystemGUI:
    global _gui_instance
    if _gui_instance is None:
        _gui_instance = SystemGUI()
    return _gui_instance