# plugins/builtins/newbula_plus/config_wizard.py
import wx
import json
import requests
import asyncio
from pathlib import Path

API = "http://localhost:9000/api/builtins/newbula_plus/engine"
CONFIG_FILE = Path(__file__).parent / "config.json"


class ConfigWizard(wx.Frame):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, agent):
        if hasattr(self, '_initialized') and self._initialized:
            self.Raise()
            self.SetFocus()
            return

        self.agent = agent
        self._initialized = True
        self.wizard_data = {}
        self.step_items = []

        c_bg = wx.Colour(30, 41, 59)
        c_text = wx.Colour(248, 250, 252)
        c_muted = wx.Colour(148, 163, 184)
        c_accent = wx.Colour(34, 197, 94)
        c_btn = wx.Colour(71, 85, 105)
        c_btn_hover = wx.Colour(91, 107, 130)
        c_input = wx.Colour(10, 15, 25)
        c_selected = wx.Colour(20, 60, 50)

        sw, sh = wx.GetDisplaySize()
        super().__init__(None, title="⚙️ Newbula Plus 配置", size=(580, sh - 60),
                         style=wx.STAY_ON_TOP | wx.CAPTION | wx.CLOSE_BOX |
                               wx.MINIMIZE_BOX | wx.MAXIMIZE_BOX | wx.RESIZE_BORDER)

        self.SetMinSize((450, 450))
        self.SetBackgroundColour(c_bg)
        self.SetPosition((sw - 580 - 20, 30))

        panel = wx.Panel(self)
        panel.SetBackgroundColour(c_bg)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # 标题
        title = wx.StaticText(panel, label="⚙️ 平台配置")
        title.SetForegroundColour(c_accent)
        title.SetFont(wx.Font(12, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        sizer.Add(title, 0, wx.ALL, 6)

        # ============================================================
        # 列表区 - 权重 4（最大）
        # ============================================================
        list_box = wx.StaticBox(panel, label="已配置平台")
        list_box.SetForegroundColour(c_muted)
        list_sizer = wx.StaticBoxSizer(list_box, wx.VERTICAL)

        self.platform_list = wx.ListCtrl(list_box, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES, size=(-1, -1))
        self.platform_list.SetBackgroundColour(c_input)
        self.platform_list.SetForegroundColour(c_text)
        self.platform_list.SetFont(wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.platform_list.InsertColumn(0, "启用", width=45)
        self.platform_list.InsertColumn(1, "名称", width=90)
        self.platform_list.InsertColumn(2, "JS文件", width=170)
        self.platform_list.InsertColumn(3, "URL", width=210)
        
        # 🔥 绑定选中事件
        self.platform_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_list_select)
        self.platform_list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.on_list_deselect)
        
        list_sizer.Add(self.platform_list, 1, wx.EXPAND | wx.ALL, 4)
        sizer.Add(list_sizer, 4, wx.EXPAND | wx.ALL, 6)

        # ============================================================
        # 按钮行
        # ============================================================
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        for label, color, handler in [
            ("➕ 添加", c_accent, self.on_add),
            ("🔄 加载", wx.Colour(243, 156, 18), self.on_load),
            ("🗑 删除", wx.Colour(239, 68, 68), self.on_delete),
            ("✅ 启用/禁用", wx.Colour(34, 197, 94), self.on_toggle_enabled),
        ]:
            btn = wx.Button(panel, label=label, size=(-1, 26), style=wx.BORDER_NONE)
            btn.SetBackgroundColour(color if label == "➕ 添加" else c_btn)
            btn.SetForegroundColour(wx.Colour(0, 0, 0) if label == "➕ 添加" else c_text)
            btn.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
            btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            btn.Bind(wx.EVT_BUTTON, handler)
            btn_row.Add(btn, 1, wx.LEFT, 4)
        sizer.Add(btn_row, 0, wx.ALL | wx.EXPAND, 4)

        # ============================================================
        # 步骤区
        # ============================================================
        step_box = wx.StaticBox(panel, label="📋 步骤")
        step_box.SetForegroundColour(c_muted)
        self.step_sizer = wx.StaticBoxSizer(step_box, wx.VERTICAL)

        steps = [
            ("1. 打开 Chrome", "step1", True),
            ("2. 等待登录", "step2", False),
            ("3. 点击输入框位置", "step3", True),
            ("4. 发送测试消息", "step4", True),
            ("5. 框选输入区域", "step5", True),
            ("6. 保存配置", "step8", True),
        ]

        for i, (text, key, has_btn) in enumerate(steps):
            row = wx.BoxSizer(wx.HORIZONTAL)
            num = wx.StaticText(step_box, label=f"{i+1}.")
            num.SetForegroundColour(c_muted)
            num.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
            row.Add(num, 0, wx.ALL | wx.ALIGN_CENTER, 2)

            desc = wx.StaticText(step_box, label=text)
            desc.SetForegroundColour(c_muted)
            desc.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
            row.Add(desc, 1, wx.ALL | wx.ALIGN_CENTER, 2)

            if has_btn:
                btn = wx.Button(step_box, label="执行", size=(46, 22), style=wx.BORDER_NONE)
                btn.SetBackgroundColour(c_btn)
                btn.SetForegroundColour(c_text)
                btn.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
                btn.Bind(wx.EVT_BUTTON, lambda e, k=key: self.on_step_action(k))
                row.Add(btn, 0, wx.ALL | wx.ALIGN_CENTER, 2)

            status = wx.StaticText(step_box, label="○")
            status.SetForegroundColour(c_muted)
            status.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
            row.Add(status, 0, wx.ALL | wx.ALIGN_CENTER, 2)

            self.step_sizer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
            self.step_items.append({"num": num, "desc": desc, "status": status, "key": key})

        sizer.Add(self.step_sizer, 0, wx.EXPAND | wx.ALL, 6)

        # ============================================================
        # 编辑区
        # ============================================================
        edit_box = wx.StaticBox(panel, label="📝 编辑")
        edit_box.SetForegroundColour(c_muted)
        edit_sizer = wx.StaticBoxSizer(edit_box, wx.VERTICAL)

        # 名称 + URL
        row1 = wx.BoxSizer(wx.HORIZONTAL)
        name_label = wx.StaticText(edit_box, label="名称:")
        name_label.SetForegroundColour(c_muted)
        name_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        row1.Add(name_label, 0, wx.ALL | wx.ALIGN_CENTER, 2)
        self.name_input = wx.TextCtrl(edit_box, size=(100, 24))
        self.name_input.SetBackgroundColour(c_input)
        self.name_input.SetForegroundColour(c_text)
        self.name_input.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        row1.Add(self.name_input, 0, wx.ALL, 2)

        url_label = wx.StaticText(edit_box, label="URL:")
        url_label.SetForegroundColour(c_muted)
        url_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        row1.Add(url_label, 0, wx.ALL | wx.ALIGN_CENTER, 2)
        self.url_input = wx.TextCtrl(edit_box, size=(180, 24), value="https://")
        self.url_input.SetBackgroundColour(c_input)
        self.url_input.SetForegroundColour(c_text)
        self.url_input.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        row1.Add(self.url_input, 1, wx.ALL | wx.EXPAND, 2)
        edit_sizer.Add(row1, 0, wx.EXPAND)

        # JS文件
        row2 = wx.BoxSizer(wx.HORIZONTAL)
        js_label = wx.StaticText(edit_box, label="JS文件:")
        js_label.SetForegroundColour(c_muted)
        js_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        row2.Add(js_label, 0, wx.ALL | wx.ALIGN_CENTER, 2)
        self.js_input = wx.TextCtrl(edit_box, size=(250, 24), value="scripts/")
        self.js_input.SetBackgroundColour(c_input)
        self.js_input.SetForegroundColour(c_text)
        self.js_input.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        row2.Add(self.js_input, 1, wx.ALL | wx.EXPAND, 2)
        edit_sizer.Add(row2, 0, wx.EXPAND)

        # 预设按钮
        preset_row = wx.BoxSizer(wx.HORIZONTAL)
        for name, js in [
            ("豆包", "scripts/doubao.js"),
            ("千问", "scripts/qwen.js"),
            ("DeepSeek", "scripts/deepseek.js"),
            ("Kimi", "scripts/kimi.js"),
        ]:
            btn = wx.Button(edit_box, label=name, size=(56, 22), style=wx.BORDER_NONE)
            btn.SetBackgroundColour(c_btn)
            btn.SetForegroundColour(c_text)
            btn.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
            btn.Bind(wx.EVT_BUTTON, lambda e, n=name, j=js: self._set_preset(n, j))
            preset_row.Add(btn, 0, wx.ALL, 1)
        edit_sizer.Add(preset_row, 0, wx.ALL | wx.ALIGN_CENTER, 2)

        # 保存按钮
        save_btn = wx.Button(edit_box, label="💾 保存平台", size=(-1, 26), style=wx.BORDER_NONE)
        save_btn.SetBackgroundColour(c_accent)
        save_btn.SetForegroundColour(wx.Colour(0, 0, 0))
        save_btn.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        save_btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        save_btn.Bind(wx.EVT_BUTTON, lambda e: self._save_platform())
        edit_sizer.Add(save_btn, 0, wx.ALL | wx.EXPAND, 2)

        sizer.Add(edit_sizer, 0, wx.EXPAND | wx.ALL, 6)

        # ============================================================
        # 日志区 - 固定高度
        # ============================================================
        log_box = wx.StaticBox(panel, label="📋 日志")
        log_box.SetForegroundColour(c_muted)
        log_sizer = wx.StaticBoxSizer(log_box, wx.VERTICAL)
        self.log = wx.TextCtrl(log_box, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_NONE, size=(-1, 40))
        self.log.SetMinSize((-1, 40))
        self.log.SetMaxSize((-1, 45))
        self.log.SetBackgroundColour(c_input)
        self.log.SetForegroundColour(c_accent)
        self.log.SetFont(wx.Font(8, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        log_sizer.Add(self.log, 0, wx.EXPAND | wx.ALL, 4)
        sizer.Add(log_sizer, 0, wx.EXPAND | wx.ALL, 6)

        panel.SetSizer(sizer)
        self.Bind(wx.EVT_CLOSE, lambda e: self.Hide())
        self.load_platforms()
        self.Show()

    # ============================================================
    # 列表选中高亮
    # ============================================================
    def on_list_select(self, event):
        idx = event.GetIndex()
        # 重置所有行颜色
        for i in range(self.platform_list.GetItemCount()):
            if i == idx:
                self.platform_list.SetItemBackgroundColour(i, wx.Colour(20, 60, 50))
            else:
                self.platform_list.SetItemBackgroundColour(i, wx.Colour(10, 15, 25))
        self.platform_list.Refresh()

    def on_list_deselect(self, event):
        idx = event.GetIndex()
        self.platform_list.SetItemBackgroundColour(idx, wx.Colour(10, 15, 25))
        self.platform_list.Refresh()

    def log_msg(self, msg):
        wx.CallAfter(self.log.AppendText, msg + "\n")
        wx.CallAfter(self.log.ShowPosition, self.log.GetLastPosition())

    def set_step_done(self, n):
        item = self.step_items[n - 1]
        item["num"].SetForegroundColour(wx.Colour(34, 197, 94))
        item["status"].SetLabel("✅")
        item["status"].SetForegroundColour(wx.Colour(34, 197, 94))

    def _get_platform_from_config(self):
        name = self.name_input.GetValue().strip()
        url = self.url_input.GetValue().strip()
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for p in config.get("platforms", []):
                if p.get("name") == name or p.get("url") == url:
                    return p
        except:
            pass
        return None

    def _set_preset(self, name, js_file):
        self.name_input.SetValue(name)
        self.js_input.SetValue(js_file)
        urls = {
            "豆包": "https://www.doubao.com/chat",
            "千问": "https://tongyi.aliyun.com/qianwen",
            "DeepSeek": "https://chat.deepseek.com",
            "Kimi": "https://kimi.moonshot.cn",
        }
        self.url_input.SetValue(urls.get(name, "https://"))
        self.log_msg(f"📌 预设: {name} → {js_file}")

    def _save_platform(self):
        name = self.name_input.GetValue().strip()
        url = self.url_input.GetValue().strip()
        js_file = self.js_input.GetValue().strip()

        if not name or not url:
            self.log_msg("❌ 请填写名称和 URL")
            return
        if not js_file:
            self.log_msg("❌ 请填写 JS 文件路径")
            return

        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {"platforms": []}
        except:
            config = {"platforms": []}

        existing = None
        for p in config["platforms"]:
            if p.get("name") == name:
                existing = p
                break

        if existing:
            existing["url"] = url
            existing["reader"] = {"file": js_file}
            self.log_msg(f"✅ 已更新: {name}")
        else:
            import uuid
            platform = {
                "id": str(uuid.uuid4())[:8],
                "name": name,
                "url": url,
                "profile_dir": f"./profiles/{name}",
                "chrome_port": 9230 + len(config["platforms"]),
                "reader": {"file": js_file},
                "enabled": True
            }
            config["platforms"].append(platform)
            self.log_msg(f"✅ 已添加: {name}")

        CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        self.load_platforms()

    def load_platforms(self):
        self.platform_list.DeleteAllItems()
        if CONFIG_FILE.exists():
            try:
                config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for i, p in enumerate(config.get("platforms", [])):
                    idx = self.platform_list.InsertItem(i, "✅" if p.get("enabled", True) else "❌")
                    self.platform_list.SetItem(idx, 1, p.get("name", ""))
                    self.platform_list.SetItem(idx, 2, p.get("reader", {}).get("file", "未配置"))
                    self.platform_list.SetItem(idx, 3, p.get("url", ""))
                    # 默认背景色
                    self.platform_list.SetItemBackgroundColour(idx, wx.Colour(10, 15, 25))
            except:
                pass

    def on_step_action(self, key):
        if key == "step1":
            self._do_open_chrome()
        elif key == "step2":
            self._do_wait_login()
        elif key == "step3":
            self._do_get_click_position()
        elif key == "step4":
            self._do_send_hello()
        elif key == "step5":
            self._do_get_region()
        elif key == "step8":
            self._do_save()

    def _do_open_chrome(self):
        url = self.url_input.GetValue().strip()
        name = self.name_input.GetValue().strip()
        if not url:
            self.log_msg("❌ 请输入 URL")
            return

        self.wizard_data = {"url": url, "name": name or url.split("//")[1].split("/")[0]}

        existing = self._get_platform_from_config()
        if existing:
            self.wizard_data["platform"] = existing
            self.log_msg(f"📌 平台已存在: {name}，端口: {existing['chrome_port']}")
        else:
            self.log_msg(f"🚀 打开 Chrome: {name} (新平台)")

        try:
            resp = requests.post(API, json={"action": "open_chrome", "url": url, "name": name}, timeout=30)
            data = resp.json()
            if data.get("ok"):
                self.wizard_data["platform"] = data["platform"]
                self.log_msg(f"✅ Chrome 已打开，端口: {data['platform']['chrome_port']}")
                self.set_step_done(1)
            else:
                self.log_msg(f"❌ 打开失败: {data}")
        except Exception as e:
            self.log_msg(f"❌ {e}")

    def _do_wait_login(self):
        self.log_msg("⏳ 请在 Chrome 中完成登录，然后点击下一步...")
        self.set_step_done(2)

    def _do_get_click_position(self):
        self.log_msg("👆 请点击 Chrome 中输入框位置...")
        async def _click():
            sel = await self.agent.system.start_selection("请点击输入框")
            if sel and not sel.get("cancelled") and sel.get("selection"):
                s = sel["selection"]
                click_data = {"x": s["x1"], "y": s["y1"]}
                self.wizard_data["click_x"] = s["x1"]
                self.wizard_data["click_y"] = s["y1"]
                self._save_to_config({"input_click": click_data})
                wx.CallAfter(self._on_click_result, s)
        self.agent.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_click()))

    def _on_click_result(self, s):
        self.log_msg(f"✅ 点击位置: ({s['x1']}, {s['y1']})")
        self.set_step_done(3)

    def _do_send_hello(self):
        click_x = self.wizard_data.get("click_x")
        click_y = self.wizard_data.get("click_y")
        if not click_x or not click_y:
            self.log_msg("❌ 请先完成步骤3")
            return

        test_id = "sel_" + str(__import__('uuid').uuid4())[:4]
        test_marker = f"（ID={test_id}）"
        self.wizard_data["test_marker"] = test_marker

        self.log_msg(f"📤 发送测试消息，激活输入框...")

        async def _send():
            import pyautogui
            pyautogui.click(click_x, click_y)
            await __import__('asyncio').sleep(0.3)
            pyautogui.write(f"{test_marker}回复这个ID", interval=0.05)
            await __import__('asyncio').sleep(0.3)
            pyautogui.press('enter')
            await __import__('asyncio').sleep(2)
            wx.CallAfter(self._on_hello_sent)
        self.agent.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_send()))

    def _on_hello_sent(self):
        self.log_msg("✅ 测试消息已发送，输入框已激活")
        self.set_step_done(4)

    def _do_get_region(self):
        self.log_msg("📐 请框选输入框区域...")
        async def _region():
            sel = await self.agent.system.start_selection("框选输入框区域")
            if sel and not sel.get("cancelled") and sel.get("selection"):
                s = sel["selection"]
                region = {"x1": s["x1"], "y1": s["y1"], "x2": s["x2"], "y2": s["y2"]}
                self.wizard_data["region"] = region
                self._save_to_config({"input_region": region})
                wx.CallAfter(self._on_region_result, s)
        self.agent.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_region()))

    def _on_region_result(self, s):
        self.log_msg(f"✅ 框选区域: ({s['x1']},{s['y1']})-({s['x2']},{s['y2']})")
        self.set_step_done(5)

    def _save_to_config(self, updates):
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            pid = self.wizard_data.get("platform", {}).get("id")
            for p in config.get("platforms", []):
                if p.get("id") == pid:
                    p.update(updates)
                    break
            CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            self.log_msg(f"⚠️ 保存失败: {e}")
            return False

    def _do_save(self):
        platform = self.wizard_data.get("platform") or self._get_platform_from_config()
        if not platform:
            self.log_msg("❌ 没有平台数据")
            return

        errors = []
        if not platform.get("input_click") and not self.wizard_data.get("click_x"):
            errors.append("步骤3: 缺少点击位置")
        if not platform.get("input_region") and not self.wizard_data.get("region"):
            errors.append("步骤5: 缺少框选区域")

        if errors:
            for e in errors:
                self.log_msg(f"❌ {e}")
            return

        updates = {}
        if self.wizard_data.get("click_x"):
            updates["input_click"] = {"x": self.wizard_data["click_x"], "y": self.wizard_data["click_y"]}
        if self.wizard_data.get("region"):
            updates["input_region"] = self.wizard_data["region"]

        if updates:
            self._save_to_config(updates)
            self.log_msg("💾 配置已保存")
            self.set_step_done(6)
            self.load_platforms()
        else:
            self.log_msg("⚠️ 没有需要保存的数据")

    def on_add(self, event):
        self.wizard_data = {}
        self.name_input.SetValue("")
        self.url_input.SetValue("https://")
        self.js_input.SetValue("scripts/")
        self.log_msg("📝 请输入平台信息，按步骤执行")
        for item in self.step_items:
            item["num"].SetForegroundColour(wx.Colour(148, 163, 184))
            item["status"].SetLabel("○")
            item["status"].SetForegroundColour(wx.Colour(148, 163, 184))

    def on_load(self, event):
        idx = self.platform_list.GetFirstSelected()
        if idx >= 0:
            try:
                config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                platforms = config.get("platforms", [])
                if idx < len(platforms):
                    p = platforms[idx]
                    self.name_input.SetValue(p.get("name", ""))
                    self.url_input.SetValue(p.get("url", ""))
                    self.js_input.SetValue(p.get("reader", {}).get("file", ""))
                    self.wizard_data = {"platform": p}
                    self.log_msg(f"📌 加载: {p.get('name')}")
                    if p.get("input_click"):
                        self.set_step_done(3)
                    if p.get("input_region"):
                        self.set_step_done(5)
            except:
                pass

    def on_toggle_enabled(self, event):
        idx = self.platform_list.GetFirstSelected()
        if idx >= 0:
            try:
                config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                platforms = config.get("platforms", [])
                if idx < len(platforms):
                    p = platforms[idx]
                    p["enabled"] = not p.get("enabled", True)
                    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
                    self.load_platforms()
                    self.log_msg(f"✅ {p['name']} → {'启用' if p['enabled'] else '禁用'}")
            except:
                pass

    def on_delete(self, event):
        idx = self.platform_list.GetFirstSelected()
        if idx >= 0:
            if wx.MessageBox("确定删除？", "确认", wx.YES_NO) == wx.YES:
                try:
                    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                    platforms = config.get("platforms", [])
                    if idx < len(platforms):
                        name = platforms[idx].get("name", "")
                        del platforms[idx]
                        CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
                        self.load_platforms()
                        self.log_msg(f"🗑 已删除: {name}")
                except:
                    pass


def show_config_wizard(agent):
    def _create():
        wizard = ConfigWizard(agent)
        wizard.Show()
        wizard.Raise()
    wx.CallAfter(_create)