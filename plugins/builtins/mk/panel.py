"""MK 面板 UI (wx 版) — 事件驱动精简版 + Cursor 风格悬停"""
import wx
import pyperclip
from .state import get_state
from .prompts import ACTION_PROMPTS, PROCESS_BUTTONS


class MKPanel:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        self.frame = None
        self.intent_entry = None
        self.content_text = None
        self.result_text = None
        self.status_label = None
        self.action_btn = None
        self._on_execute = None
        self._processing = False

    def bind(self, on_execute=None):
        self._on_execute = on_execute

    def status(self, msg):
        def _do():
            if self.status_label:
                self.status_label.SetLabel(msg)
        wx.CallAfter(_do)

    def set_processing(self, v):
        self._processing = v
        wx.CallAfter(self._update_action_btn)

    def show(self):
        def _do():
            if self.frame is None:
                self._create()
            self.frame.Show()
            self.frame.Raise()
            self.frame.Restore()
        if wx.IsMainThread():
            _do()
        else:
            wx.CallAfter(_do)

    def hide(self):
        if self.frame:
            wx.CallAfter(self.frame.Hide)

    def set_result(self, text):
        def _do():
            if self.result_text:
                self.result_text.SetValue(text)
        wx.CallAfter(_do)

    def append_result(self, chunk):
        def _do():
            if self.result_text:
                self.result_text.AppendText(chunk)
                self.result_text.ShowPosition(self.result_text.GetLastPosition())
        wx.CallAfter(_do)

    def _update_action_btn(self):
        if not self.action_btn:
            return
        if self._processing:
            self.action_btn.SetLabel("⏳ 处理中...")
            self.action_btn.SetBackgroundColour("#475569")
        else:
            self.action_btn.SetLabel("▶ 执行")
            self.action_btn.SetBackgroundColour("#22c55e")

    # ── UI 创建 ──

    def _create(self):
        c_bg = wx.Colour(30, 41, 59)
        c_text = wx.Colour(248, 250, 252)
        c_muted = wx.Colour(148, 163, 184)
        c_accent = wx.Colour(34, 197, 94)
        c_btn = wx.Colour(71, 85, 105)
        c_btn_hover = wx.Colour(91, 107, 130)
        c_input = wx.Colour(10, 15, 25)
        sw, sh = wx.GetDisplaySize()
        self.frame = wx.Frame(None, title="MK 助手", size=(520, sh - 100),
                              style=wx.STAY_ON_TOP | wx.CAPTION | wx.CLOSE_BOX |
                                    wx.MINIMIZE_BOX | wx.MAXIMIZE_BOX | wx.RESIZE_BORDER)
        
        self.frame.SetMinSize((400, 500))
        self.frame.SetBackgroundColour(c_bg)

        panel = wx.Panel(self.frame)
        panel.SetBackgroundColour(c_bg)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # ── 快捷指令 ──
        prompt_box = wx.StaticBox(panel, label="快捷指令")
        prompt_box.SetForegroundColour(c_muted)
        prompt_sizer = wx.StaticBoxSizer(prompt_box, wx.VERTICAL)

        for row_buttons in [PROCESS_BUTTONS[i:i+4] for i in range(0, len(PROCESS_BUTTONS), 4)]:
            row = wx.BoxSizer(wx.HORIZONTAL)
            for btn_info in row_buttons:
                label = btn_info["label"]
                action = btn_info["action"]
                
                btn = wx.Button(prompt_sizer.GetStaticBox(), label=label, size=(-1, 30),
                                style=wx.BORDER_NONE)
                btn.SetBackgroundColour(c_btn)
                btn.SetForegroundColour(c_text)
                btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
                
                btn.Bind(wx.EVT_ENTER_WINDOW, lambda e, b=btn: b.SetBackgroundColour(c_btn_hover))
                btn.Bind(wx.EVT_LEAVE_WINDOW, lambda e, b=btn: b.SetBackgroundColour(c_btn))
                
                # ✅ 修复：用闭包工厂函数，显式传入 action 和 label
                def make_handler(act, lbl):
                    def handler(event):
                        if act == "auto":
                            wx.CallAfter(self.intent_entry.SetValue, "")
                            wx.CallAfter(self.status, "自定义模式")
                        else:
                            prompt = ACTION_PROMPTS.get(act, "")
                            wx.CallAfter(self.intent_entry.SetValue, prompt)
                            wx.CallAfter(self.status, "指令: " + lbl)
                            wx.CallAfter(self._do_action)
                    return handler
                
                btn.Bind(wx.EVT_BUTTON, make_handler(action, label))
                row.Add(btn, 1, wx.ALL, 2)
            prompt_sizer.Add(row, 0, wx.EXPAND)

        sizer.Add(prompt_sizer, 0, wx.EXPAND | wx.ALL, 8)

        # ── 内容区（划词自动填入） ──
        content_box = wx.StaticBox(panel, label="内容")
        content_box.SetForegroundColour(c_muted)
        content_sizer = wx.StaticBoxSizer(content_box, wx.VERTICAL)

        self.content_text = wx.TextCtrl(content_sizer.GetStaticBox(),
                                        style=wx.TE_MULTILINE, size=(-1, 60))
        self.content_text.SetBackgroundColour(c_input)
        self.content_text.SetForegroundColour(c_text)
        self.content_text.SetFont(wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        content_sizer.Add(self.content_text, 1, wx.EXPAND | wx.ALL, 4)
        sizer.Add(content_sizer, 0, wx.EXPAND | wx.ALL, 8)

        # ── 意图输入 ──
        intent_box = wx.StaticBox(panel, label="意图")
        intent_box.SetForegroundColour(c_muted)
        intent_sizer = wx.StaticBoxSizer(intent_box, wx.VERTICAL)

        self.intent_entry = wx.TextCtrl(intent_sizer.GetStaticBox(),
                                        style=wx.TE_PROCESS_ENTER)
        self.intent_entry.SetBackgroundColour(c_input)
        self.intent_entry.SetForegroundColour(c_text)
        self.intent_entry.Bind(wx.EVT_TEXT_ENTER, lambda e: self._do_action())
        intent_sizer.Add(self.intent_entry, 0, wx.EXPAND | wx.ALL, 4)

        # ── 执行按钮 ──
        self.action_btn = wx.Button(intent_sizer.GetStaticBox(), label="▶ 执行",
                                    style=wx.BORDER_NONE)
        self.action_btn.SetBackgroundColour(c_accent)
        self.action_btn.SetForegroundColour(wx.Colour(0, 0, 0))
        self.action_btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        self.action_btn.Bind(wx.EVT_ENTER_WINDOW, lambda e: self.action_btn.SetBackgroundColour("#16a34a"))
        self.action_btn.Bind(wx.EVT_LEAVE_WINDOW, lambda e: self.action_btn.SetBackgroundColour(c_accent))
        self.action_btn.Bind(wx.EVT_BUTTON, lambda e: self._do_action())
        intent_sizer.Add(self.action_btn, 0, wx.EXPAND | wx.ALL, 4)

        sizer.Add(intent_sizer, 0, wx.EXPAND | wx.ALL, 8)

        # ── 结果区 ──
        result_box = wx.StaticBox(panel, label="结果")
        result_box.SetForegroundColour(c_muted)
        result_sizer = wx.StaticBoxSizer(result_box, wx.VERTICAL)

        self.result_text = wx.TextCtrl(result_sizer.GetStaticBox(),
                                       style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_NONE)
        self.result_text.SetBackgroundColour(c_input)
        self.result_text.SetForegroundColour(c_text)
        self.result_text.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        result_sizer.Add(self.result_text, 1, wx.EXPAND | wx.ALL, 4)
        sizer.Add(result_sizer, 1, wx.EXPAND | wx.ALL, 8)

        # ── 复制按钮 ──
        copy_btn = wx.Button(panel, label="📋 复制结果", style=wx.BORDER_NONE)
        copy_btn.SetBackgroundColour(c_btn)
        copy_btn.SetForegroundColour(c_text)
        copy_btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        copy_btn.Bind(wx.EVT_ENTER_WINDOW, lambda e: copy_btn.SetBackgroundColour(c_btn_hover))
        copy_btn.Bind(wx.EVT_LEAVE_WINDOW, lambda e: copy_btn.SetBackgroundColour(c_btn))
        copy_btn.Bind(wx.EVT_BUTTON, lambda e: self._copy_result())
        sizer.Add(copy_btn, 0, wx.ALL, 8)

        # ── 状态栏 ──
        self.status_label = wx.StaticText(panel, label="就绪")
        self.status_label.SetForegroundColour(c_muted)
        sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 6)

        panel.SetSizer(sizer)

        self.frame.Bind(wx.EVT_CLOSE, lambda e: self.frame.Hide())

        # 定位右下角
        sw, sh = wx.GetDisplaySize()
        w, h = self.frame.GetSize()
        self.frame.SetPosition((sw - 520 - 20, 50))

        self._update_action_btn()

    def _do_action(self):
        if self._processing:
            return
        if self._on_execute:
            self._processing = True
            self._update_action_btn()
            self._on_execute()

    def _copy_result(self):
        txt = self.result_text.GetValue()
        if txt.strip():
            pyperclip.copy(txt)
            self.status("已复制到剪贴板")
    def _create_silently(self):
        """偷偷创建，不显示"""
        if self.frame is None:
            self._create()
    # 不调 Show()，frame 存在但隐藏


_panel = MKPanel()


def get_panel() -> MKPanel:
    return _panel