"""探头配置对话框 — wx 版 + 历史记忆"""
import wx
import json
from pathlib import Path

HISTORY_FILE = Path("data/probe_prompts_history.json")


def load_prompt_history():
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data.get("prompts", [])
    except:
        return []


def save_prompt_history(prompts):
    unique = list(dict.fromkeys(prompts))
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps({"prompts": unique[-50:]}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


class ProbeConfigDialog(wx.Dialog):
    def __init__(self, parent, region_info, window_title=""):
        super().__init__(parent, title="🔍 配置探头", size=(500, 600))
        
        self.region_info = region_info
        self.window_title = window_title
        self.result = None
        
        c_bg = wx.Colour(30, 41, 59)
        c_panel = wx.Colour(15, 23, 42)
        c_text = wx.Colour(226, 232, 240)
        c_muted = wx.Colour(148, 163, 184)
        c_accent = wx.Colour(99, 102, 241)
        c_input = wx.Colour(15, 23, 42)
        
        self.SetBackgroundColour(c_bg)
        panel = wx.Panel(self)
        panel.SetBackgroundColour(c_bg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # ── 标题 ──
        title_text = f"📐 框选区域: {region_info.get('w', 0)}×{region_info.get('h', 0)}"
        title = wx.StaticText(panel, label=title_text)
        title.SetForegroundColour(c_text)
        title.SetFont(wx.Font(12, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        sizer.Add(title, 0, wx.ALL, 10)
        
        if window_title:
            win_label = wx.StaticText(panel, label=f"🪟 {window_title[:60]}")
            win_label.SetForegroundColour(c_muted)
            sizer.Add(win_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        
        # ── 名称 ──
        name_row = wx.BoxSizer(wx.HORIZONTAL)
        name_label = wx.StaticText(panel, label="名称:", size=(50, -1))
        name_label.SetForegroundColour(c_muted)
        name_row.Add(name_label, 0, wx.ALIGN_CENTER | wx.RIGHT, 8)
        default_name = f"探头_{region_info.get('w', 0)}×{region_info.get('h', 0)}"
        self.name_ctrl = wx.TextCtrl(panel, value=default_name)
        self.name_ctrl.SetBackgroundColour(c_input)
        self.name_ctrl.SetForegroundColour(c_text)
        name_row.Add(self.name_ctrl, 1, wx.EXPAND)
        sizer.Add(name_row, 0, wx.EXPAND | wx.ALL, 8)
        
        # ── 间隔 ──
        interval_row = wx.BoxSizer(wx.HORIZONTAL)
        interval_label = wx.StaticText(panel, label="间隔(秒):", size=(50, -1))
        interval_label.SetForegroundColour(c_muted)
        interval_row.Add(interval_label, 0, wx.ALIGN_CENTER | wx.RIGHT, 8)
        self.interval_ctrl = wx.SpinCtrl(panel, value="5", min=1, max=60, size=(80, -1))
        interval_row.Add(self.interval_ctrl, 0)
        interval_row.AddStretchSpacer()
        sizer.Add(interval_row, 0, wx.EXPAND | wx.ALL, 8)
        
        # ── 操作提示 ──
        prompt_label = wx.StaticText(panel, label="操作提示:")
        prompt_label.SetForegroundColour(c_muted)
        sizer.Add(prompt_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        
        # 历史下拉
        history_row = wx.BoxSizer(wx.HORIZONTAL)
        history_label = wx.StaticText(panel, label="历史:", size=(50, -1))
        history_label.SetForegroundColour(c_muted)
        history_row.Add(history_label, 0, wx.ALIGN_CENTER | wx.RIGHT, 8)
        
        self.history_choices = load_prompt_history()
        choices = ["（自定义输入）"] + self.history_choices
        self.history_combo = wx.ComboBox(panel, choices=choices, style=wx.CB_READONLY)
        self.history_combo.SetSelection(0)
        self.history_combo.Bind(wx.EVT_COMBOBOX, self._on_history_select)
        history_row.Add(self.history_combo, 1, wx.EXPAND)
        sizer.Add(history_row, 0, wx.EXPAND | wx.ALL, 8)
        
        # 提示输入框
        self.prompt_ctrl = wx.TextCtrl(
            panel, value="分析这张截图的内容",
            style=wx.TE_MULTILINE, size=(-1, 70)
        )
        self.prompt_ctrl.SetBackgroundColour(c_input)
        self.prompt_ctrl.SetForegroundColour(c_text)
        self.prompt_ctrl.Bind(wx.EVT_TEXT, self._update_preview)
        sizer.Add(self.prompt_ctrl, 0, wx.EXPAND | wx.ALL, 8)
        
        # ── 操作开关 ──
        action_row = wx.BoxSizer(wx.HORIZONTAL)
        self.action_check = wx.CheckBox(panel, label="🔧 允许自动操作桌面（勾选后可执行点击/输入等操作）")
        self.action_check.SetForegroundColour(c_text)
        self.action_check.SetValue(False)
        self.action_check.Bind(wx.EVT_CHECKBOX, self._update_preview)
        action_row.Add(self.action_check, 0)
        sizer.Add(action_row, 0, wx.EXPAND | wx.ALL, 8)
        
        # ── 规则预览 ──
        preview_label = wx.StaticText(panel, label="将创建规则:")
        preview_label.SetForegroundColour(c_muted)
        sizer.Add(preview_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        
        self.preview_ctrl = wx.TextCtrl(
            panel, value="",
            style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 50)
        )
        self.preview_ctrl.SetBackgroundColour(c_panel)
        self.preview_ctrl.SetForegroundColour(c_accent)
        self.preview_ctrl.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        sizer.Add(self.preview_ctrl, 0, wx.EXPAND | wx.ALL, 8)
        self._update_preview()
        
        # ── 底部按钮 ──
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        start_btn = wx.Button(panel, label="🚀 启动监控")
        start_btn.SetBackgroundColour(c_accent)
        start_btn.SetForegroundColour(wx.Colour(255, 255, 255))
        start_btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        start_btn.Bind(wx.EVT_BUTTON, self._on_start)
        btn_sizer.Add(start_btn, 1, wx.RIGHT, 8)
        
        cancel_btn = wx.Button(panel, label="取消")
        cancel_btn.SetBackgroundColour(wx.Colour(55, 65, 81))
        cancel_btn.SetForegroundColour(c_text)
        cancel_btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        cancel_btn.Bind(wx.EVT_BUTTON, self._on_cancel)
        btn_sizer.Add(cancel_btn, 0)
        
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 10)
        
        panel.SetSizer(sizer)
        self.CentreOnScreen()
    
    def _on_history_select(self, event):
        idx = self.history_combo.GetSelection()
        if idx > 0 and idx - 1 < len(self.history_choices):
            self.prompt_ctrl.SetValue(self.history_choices[idx - 1])
    
    def _update_preview(self, event=None):
        prompt = self.prompt_ctrl.GetValue().strip()
        if not prompt:
            self.preview_ctrl.SetValue("（请输入操作提示）")
            return
        
        allow_actions = self.action_check.GetValue()
        rule_type = "vision_action" if allow_actions else "vision_analyze"
        mode_label = "🔧 分析+操作" if allow_actions else "🔍 仅分析"
        
        preview = {
            "type": rule_type,
            "label": prompt[:40],
            "prompt": prompt,
            "trigger": "always",
            "mode": mode_label
        }
        self.preview_ctrl.SetValue(
            json.dumps(preview, ensure_ascii=False, indent=2)
        )
    
    def _on_start(self, event):
        prompt = self.prompt_ctrl.GetValue().strip()
        if not prompt:
            wx.MessageBox("请输入操作提示", "提示", wx.OK | wx.ICON_INFORMATION)
            return
        
        name = self.name_ctrl.GetValue().strip()
        if not name:
            wx.MessageBox("请输入探头名称", "提示", wx.OK | wx.ICON_INFORMATION)
            return
        
        history = load_prompt_history()
        if prompt not in history:
            history.append(prompt)
            save_prompt_history(history)
        
        allow_actions = self.action_check.GetValue()
        rule_type = "vision_action" if allow_actions else "vision_analyze"
        
        self.result = {
            "name": name,
            "interval": self.interval_ctrl.GetValue(),
            "rule": {
                "type": rule_type,
                "label": prompt[:40],
                "prompt": prompt,
                "trigger": "always"
            }
        }
        
        self.EndModal(wx.ID_OK)
    
    def _on_cancel(self, event):
        self.EndModal(wx.ID_CANCEL)
    
    def get_result(self):
        return self.result


def show_config_dialog(region_info, window_title=""):
    dialog = ProbeConfigDialog(None, region_info, window_title)
    if dialog.ShowModal() == wx.ID_OK:
        return dialog.get_result()
    return None