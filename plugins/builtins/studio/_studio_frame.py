"""AICP Studio — 单窗口：WebView + 侧边栏（基于 Nebula 架构）"""
import wx
import wx.html2
import json
import re
import os
import shutil
import webbrowser
import urllib.request
from urllib.parse import unquote
from pathlib import Path

# ============================================
# 常量
# ============================================
WINDOW_W = 1600
WINDOW_H = 900
SIDEBAR_W = 400

PLATFORMS = {
    "deepseek": {"name": "DeepSeek", "url": "https://chat.deepseek.com", "icon": "🔵"},
    "doubao": {"name": "豆包", "url": "https://www.doubao.com/chat", "icon": "🟢"},
    "chatgpt": {"name": "ChatGPT", "url": "https://chat.openai.com", "icon": "🟡"},
    "claude": {"name": "Claude", "url": "https://claude.ai", "icon": "🟣"},
    "kimi": {"name": "Kimi", "url": "https://kimi.moonshot.cn", "icon": "⚪"},
}

PROTOCOL_PATH = Path(__file__).parent / "aicp.md"

INJECT_JS = """(function(){
    if(window.__aicp_studio) return;
    window.__aicp_studio = true;

    var lastContent = '';
    var sentHashes = new Set();
    setInterval(function(){
        var text = document.body.innerText || '';
        var matches = text.match(/=== (PLUGIN|HTML|APP):[\\s\\S]*?=== END ===/g);
        if(matches && matches.length > 0){
            var last = matches[matches.length-1];
            if(last !== lastContent && last.length > 50 && !window.__aicp_reply_lock){
                lastContent = last;
                window.__aicp_reply_text = last;
                window.__aicp_reply_lock = true;
                window.__aicp_is_new_reply = true;
            }
        }
    }, 500);
})();"""

# ============================================
# 配色
# ============================================
BG = "#1e1e1e"
BG_PANEL = "#252525"
BG_INPUT = "#141414"
FG = "#dcdcdc"
FG_MUTED = "#969696"
ACCENT = "#6366f1"
SUCCESS = "#4ec9b0"
DANGER = "#f44747"
WARNING = "#f59e0b"


class StudioFrame(wx.Frame):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, base_url="http://127.0.0.1:9000"):
        if hasattr(self, '_initialized') and self._initialized:
            self.Raise()
            self.SetFocus()
            return

        super().__init__(None, title="🛠 AICP Studio", size=(WINDOW_W, WINDOW_H),
                         style=wx.CAPTION | wx.CLOSE_BOX | wx.RESIZE_BORDER | wx.MINIMIZE_BOX | wx.MAXIMIZE_BOX)
        self._initialized = True
        self._platforms = PLATFORMS
        self._current_platform = "deepseek"
        self._current_project = {"name": None, "path": None}

        self.SetBackgroundColour(BG)
        self.SetMinSize(wx.Size(800, 500))

        # ── 主布局 ──
        main_panel = wx.Panel(self)
        main_panel.SetBackgroundColour(BG)
        hbox = wx.BoxSizer(wx.HORIZONTAL)

        # WebView
        self.webview = wx.html2.WebView.New(main_panel)
        self.webview.LoadURL(PLATFORMS["deepseek"]["url"])
        self.webview.Bind(wx.html2.EVT_WEBVIEW_LOADED, self._on_loaded)
        hbox.Add(self.webview, 1, wx.EXPAND)

        # 侧边栏
        self._sidebar = self._build_sidebar(main_panel)
        hbox.Add(self._sidebar, 0, wx.EXPAND)

        main_panel.SetSizer(hbox)

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(main_panel, 1, wx.EXPAND)
        self._base_url = base_url
        self.SetSizer(main_sizer)

        self.Centre()
        self.Show()

        # 轮询
        self._poll_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._poll_reply, self._poll_timer)
        self._poll_timer.Start(1000)

        self.Bind(wx.EVT_CLOSE, self._on_closing)

    def _open_vision_memory(self):
        """打开视觉知识库"""
        webbrowser.open(f"{self._base_url}/vision_memory/")
        self._status.SetLabel("📸 已打开视觉知识库")

    def _on_closing(self, event):
        self._poll_timer.Stop()
        StudioFrame._instance = None
        self.Destroy()

    def _on_loaded(self, event):
        wx.CallLater(3000, lambda: self.webview.RunScript(INJECT_JS))
        print("[Studio] JS 注入完成")

    # ============================================================
    # 轮询
    # ============================================================

    def _poll_reply(self, event):
        try:
            text = self.webview.RunScript("window.__aicp_reply_text || '';")
            is_new = self.webview.RunScript("window.__aicp_is_new_reply || false;")
            if isinstance(text, tuple):
                text = text[1] if len(text) > 1 else ''
            if text and text.strip():
                print(f"[Studio] 截获回复: {text[:100]}...")
                self._auto_save_blocks(text, force=is_new)
                self.webview.RunScript("window.__aicp_reply_text = ''; window.__aicp_reply_lock = false; window.__aicp_is_new_reply = false;")
                self._status.SetLabel("✅ 已截获 AI 回复")
        except Exception as e:
            print(f"[Studio] poll error: {e}")

    # ============================================================
    # 侧边栏
    # ============================================================

    def _build_sidebar(self, parent):
        panel = wx.Panel(parent, size=(SIDEBAR_W, -1))
        panel.SetBackgroundColour(BG_PANEL)
        vbox = wx.BoxSizer(wx.VERTICAL)

        # ── 标题 ──
        title = wx.StaticText(panel, label="🛠 AICP Studio")
        title.SetForegroundColour(FG)
        title.SetBackgroundColour(BG_PANEL)
        title.SetFont(title.GetFont().Bold().Larger())
        vbox.Add(title, 0, wx.ALL, 12)

        # ── AI 平台 ──
        vbox.Add(self._section_label(panel, "🤖 AI 平台"), 0, wx.LEFT | wx.TOP, 8)

        choices = [f"{v['icon']} {v['name']}" for v in self._platforms.values()]
        self._platform_choice = wx.Choice(panel, choices=choices)
        self._platform_choice.SetSelection(0)
        self._platform_choice.Bind(wx.EVT_CHOICE, self._on_platform_change)
        vbox.Add(self._platform_choice, 0, wx.ALL | wx.EXPAND, 4)

        url_row = wx.BoxSizer(wx.HORIZONTAL)
        self._url_entry = wx.TextCtrl(panel, value=PLATFORMS["deepseek"]["url"])
        self._url_entry.SetBackgroundColour(BG_INPUT)
        self._url_entry.SetForegroundColour(FG)
        url_row.Add(self._url_entry, 1, wx.EXPAND)
        go_btn = self._button(panel, "GO", self._go_url)
        url_row.Add(go_btn, 0, wx.LEFT, 4)
        vbox.Add(url_row, 0, wx.ALL | wx.EXPAND, 4)

        # ── 项目 ──
        vbox.Add(self._section_label(panel, "📁 项目"), 0, wx.LEFT | wx.TOP, 8)

        self._project_status = wx.StaticText(panel, label="未设置项目")
        self._project_status.SetForegroundColour(WARNING)
        self._project_status.SetBackgroundColour(BG_PANEL)
        vbox.Add(self._project_status, 0, wx.ALL, 4)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        for label, cmd, color in [
            ("➕ 新建", self._new_project, ACCENT),
            ("📋 列表", self._list_projects, ACCENT),
            ("📡 协议", self._send_protocol, SUCCESS),
            ("📸 视觉库", self._open_vision_memory, ACCENT),  # 🔥 加在这里
        ]:
            btn = self._button(panel, label, cmd, color)
            btn_row.Add(btn, 1, wx.LEFT, 2)
        vbox.Add(btn_row, 0, wx.ALL | wx.EXPAND, 4)

        # ── 文件树 ──
        vbox.Add(self._section_label(panel, "📂 文件"), 0, wx.LEFT | wx.TOP, 8)

        file_toolbar = wx.BoxSizer(wx.HORIZONTAL)
        file_toolbar.AddStretchSpacer()
        refresh_btn = wx.Button(panel, label="🔄 刷新", size=(-1, 24))
        refresh_btn.SetBackgroundColour("#3a3a3a")
        refresh_btn.SetForegroundColour(FG)
        refresh_btn.Bind(wx.EVT_BUTTON, lambda e: self._refresh_file_tree())
        file_toolbar.Add(refresh_btn, 0)
        vbox.Add(file_toolbar, 0, wx.ALL | wx.EXPAND, 4)

        self._file_tree = wx.TreeCtrl(panel, style=wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT)
        self._file_tree.SetBackgroundColour(BG_INPUT)
        self._file_tree.SetForegroundColour(FG)
        self._file_tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self._on_file_double_click)
        vbox.Add(self._file_tree, 1, wx.ALL | wx.EXPAND, 4)

        # ── 底部按钮 ──
        bottom = wx.BoxSizer(wx.HORIZONTAL)
        for label, cmd, color in [
            ("▶ 预览", self._preview, ACCENT),
            ("📦 搬入", self._move_to_project, WARNING),
            ("🚀 发布", self._publish, DANGER),
            ("📡 部署", self._deploy, SUCCESS),
        ]:
            btn = self._button(panel, label, cmd, color)
            bottom.Add(btn, 1, wx.LEFT, 2)
        vbox.Add(bottom, 0, wx.ALL | wx.EXPAND, 4)

        # ── 状态栏 ──
        self._status = wx.StaticText(panel, label="就绪")
        self._status.SetForegroundColour(FG_MUTED)
        self._status.SetBackgroundColour(BG_PANEL)
        vbox.Add(self._status, 0, wx.ALL, 8)

        panel.SetSizer(vbox)
        return panel

    # ============================================================
    # 组件工厂
    # ============================================================

    def _section_label(self, parent, text):
        lbl = wx.StaticText(parent, label=text)
        lbl.SetBackgroundColour(BG_PANEL)
        lbl.SetForegroundColour(FG_MUTED)
        lbl.SetFont(lbl.GetFont().Bold())
        return lbl

    def _button(self, parent, label, cmd, color=None):
        btn = wx.Button(parent, label=label, size=(-1, 28))
        btn.SetBackgroundColour(color or "#3a3a3a")
        btn.SetForegroundColour("#fff" if color else FG)
        btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        btn.Bind(wx.EVT_BUTTON, lambda e: cmd())
        return btn

    # ============================================================
    # AI 平台
    # ============================================================

    def _on_platform_change(self, event):
        sel = self._platform_choice.GetStringSelection()
        for key, cfg in self._platforms.items():
            if f"{cfg['icon']} {cfg['name']}" == sel:
                self._current_platform = key
                self._url_entry.SetValue(cfg["url"])
                self.webview.LoadURL(cfg["url"])
                break

    def _go_url(self):
        url = self._url_entry.GetValue().strip()
        if url:
            self.webview.LoadURL(url)

    def _send_protocol(self):
        protocol = self._load_protocol()
        if not protocol:
            return
        import pyperclip
        pyperclip.copy(protocol)
        wx.MessageBox("📡 协议已就绪，点聊天输入框，按 F8 发送")

        # 注册全局快捷键
        import keyboard
        keyboard.add_hotkey('f8', self._paste_and_send)

    def _paste_and_send(self):
        import pyautogui
        pyautogui.hotkey('ctrl', 'v')
        pyautogui.keyDown('enter')
        pyautogui.keyUp('enter')
        self._status.SetLabel("✅ 已发送")

    def _load_protocol(self):
        if PROTOCOL_PATH.exists():
            return PROTOCOL_PATH.read_text(encoding="utf-8")
        return None

    # ============================================================
    # 项目
    # ============================================================

    def _new_project(self):
        name = wx.GetTextFromUser("项目名称（英文）:", "新建项目")
        if not name or not re.match(r'^[a-z][a-z0-9_]*$', name):
            wx.MessageBox("项目名只能包含小写字母、数字、下划线，以字母开头", "错误")
            return
        proj_dir = Path(f"plugins/applications/{name}")
        if proj_dir.exists():
            wx.MessageBox("项目已存在", "错误")
            return
        
        # ✅ 创建目录
        proj_dir.mkdir(parents=True, exist_ok=True)
        www_dir = Path(f"www/{name}")
        www_dir.mkdir(parents=True, exist_ok=True)
        
        # ✅ 创建默认文件
        (proj_dir / "app.yaml").write_text(f"name: {name}\nversion: 1.0.0\nauto_start: false\ninit_action: init\n", encoding="utf-8")
        
        print(f"[Studio] 项目创建: {proj_dir}, 存在={proj_dir.exists()}")
        
        # ✅ 切换项目
        self._current_project = {"name": name, "path": str(proj_dir)}
        self._project_status.SetLabel(f"当前项目: {name}")
        self._status.SetLabel(f"✅ 项目 {name} 已创建")
        
        # ✅ 刷新文件树
        wx.CallAfter(self._refresh_file_tree)

    def _list_projects(self):
        apps_dir = Path("plugins/applications")
        if not apps_dir.exists():
            return
        names = [d.name for d in sorted(apps_dir.iterdir())
                 if d.is_dir() and d.name not in ("studio", "mk", "__pycache__")]
        if not names:
            wx.MessageBox("暂无项目", "项目列表")
            return
        dlg = wx.SingleChoiceDialog(self, "选择项目：", "📋 项目列表", names)
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetStringSelection()
            self._current_project = {"name": name, "path": str(Path(f"plugins/applications/{name}"))}
            self._project_status.SetLabel(f"当前项目: {name}")
            self._refresh_file_tree()
        dlg.Destroy()

    def _move_to_project(self):
        if not self._current_project["name"]:
            wx.MessageBox("请先创建或选择项目", "提示")
            return
        src = Path("plugins/_cache")
        if not src.exists() or not any(src.iterdir()):
            self._status.SetLabel("缓存区为空")
            return

        proj_name = self._current_project["name"]
        proj_dir = Path(f"plugins/applications/{proj_name}")
        www_dir = Path(f"www/{proj_name}")

        moved = 0
        for f in list(src.iterdir()):
            if not f.is_file():
                continue
            if f.suffix == ".html":
                www_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(www_dir / f.name))
            else:
                shutil.move(str(f), str(proj_dir / f.name))
            moved += 1

        self._status.SetLabel(f"已搬入 {moved} 个文件到 {proj_name}")
        self._refresh_file_tree()

    def _preview(self):
        if not self._current_project["name"]:
            wx.MessageBox("请先创建项目", "提示")
            return

        proj_name = self._current_project["name"]
        
        # 检查并安装依赖
        app_yaml = Path(f"plugins/applications/{proj_name}/app.yaml")
        if app_yaml.exists():
            import yaml
            cfg = yaml.safe_load(app_yaml.read_text(encoding="utf-8"))
            deps = cfg.get("dependencies", {}).get("pip", [])
            if deps:
                self._status.SetLabel(f"📦 安装依赖: {', '.join(deps)}")
                import subprocess, threading
                def install():
                    for pkg in deps:
                        try:
                            __import__(pkg.replace("-", "_"))
                        except ImportError:
                            wx.CallAfter(self._status.SetLabel, f"📦 正在安装: {pkg}")
                            subprocess.run(
                                ["pip", "install", pkg, "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
                                check=True, capture_output=True, timeout=120
                            )
                    wx.CallAfter(self._status.SetLabel, "✅ 依赖安装完成")
                    wx.CallAfter(lambda: webbrowser.open(f"http://127.0.0.1:9000/{proj_name}/"))
                threading.Thread(target=install, daemon=True).start()
                return

        webbrowser.open(f"http://127.0.0.1:9000/{proj_name}/")

    def _publish(self):
        if not self._current_project["name"]:
            wx.MessageBox("请先创建项目", "提示")
            return
        token = wx.GetTextFromUser("GitHub Token:", "发布到商店")
        if not token:
            return
        try:
            body = json.dumps({
                "action": "publish",
                "app_id": self._current_project["name"],
                "platform": "github",
                "org": "aicp-store",
                "token": token,
            }).encode()
            req = urllib.request.Request(
                "http://127.0.0.1:9000/api/builtins/control/publish",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=60)
            data = json.loads(resp.read())
            if data.get("ok"):
                self._status.SetLabel(f"✅ 已发布: {data.get('repo_url', '')}")
            else:
                self._status.SetLabel(f"❌ 失败: {data.get('error', '')}")
        except Exception as e:
            self._status.SetLabel(f"❌ 发布失败: {e}")

    def _deploy(self):
        if not self._current_project["name"]:
            wx.MessageBox("请先创建或选择项目", "提示")
            return

        proj_name = self._current_project["name"]

        # 简单输入框
        target = wx.GetTextFromUser("目标地址:", "部署到引擎", "http://")
        if not target:
            return
        token = wx.GetTextFromUser("Token:", "部署到引擎")
        if not token:
            return

        target = target.rstrip('/')
        proj_dir = Path(f"plugins/applications/{proj_name}")
        www_dir = Path(f"www/{proj_name}")

        files = {}
        for d in [(proj_dir, "plugins"), (www_dir, "www")]:
            if not d[0].exists():
                continue
            for root, dirs, filenames in os.walk(d[0]):
                for f in filenames:
                    full = Path(root) / f
                    rel = str(full.relative_to(Path(".")))
                    files[rel] = full.read_text(encoding="utf-8")

        if not files:
            self._status.SetLabel("❌ 没有文件可部署")
            return

        self._status.SetLabel("正在部署...")
        try:
            body = json.dumps({
                "payload": {
                    "action": "receive",
                    "app_id": proj_name,
                    "files": files,
                }
            }).encode()
            req = urllib.request.Request(
                f"{target}/api/builtins/deploy/receive",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-AICP-Token": token,
                },
            )
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read())
            if data.get("ok"):
                self._status.SetLabel(f"✅ 已部署到 {target}")
            else:
                self._status.SetLabel(f"❌ 部署失败: {data.get('error', '')}")
        except Exception as e:
            self._status.SetLabel(f"❌ 连接失败: {e}")

    # ============================================================
    # 文件树
    # ============================================================

    def _refresh_file_tree(self):
        self._file_tree.DeleteAllItems()
        root = self._file_tree.AddRoot("项目文件")

        if self._current_project["path"]:
            for label, d in [
                ("📁 插件", self._current_project["path"]),
                ("📁 前端", f"www/{self._current_project['name']}")
            ]:
                if os.path.exists(d):
                    node = self._file_tree.AppendItem(root, label)
                    self._walk_tree(node, d)
                    self._file_tree.Expand(node)  # ✅ 自动展开

        cache_dir = "plugins/_cache"
        if os.path.exists(cache_dir):
            cache_files = [f for f in os.listdir(cache_dir) if os.path.isfile(os.path.join(cache_dir, f))]
            if cache_files:
                node = self._file_tree.AppendItem(root, "📁 _cache")
                for f in sorted(cache_files):
                    full = os.path.join(cache_dir, f)
                    child = self._file_tree.AppendItem(node, f"📄 {f}")
                    self._file_tree.SetItemData(child, full)
                self._file_tree.Expand(node)

        self._file_tree.Expand(root)

    def _walk_tree(self, parent_node, path):
        for item in sorted(os.listdir(path)):
            full = os.path.join(path, item)
            if os.path.isdir(full):
                if item in ("__pycache__",):
                    continue
                node = self._file_tree.AppendItem(parent_node, f"📁 {item}")
                self._walk_tree(node, full)
            else:
                node = self._file_tree.AppendItem(parent_node, f"📄 {item}")
                self._file_tree.SetItemData(node, full)

    def _on_file_double_click(self, event):
        item = event.GetItem()
        filepath = self._file_tree.GetItemData(item)
        if filepath and os.path.isfile(filepath):
            wx.CallLater(200, lambda fp=filepath: self._open_editor(fp))

    def _open_editor(self, filepath):
        try:
            from ._editor import CodeEditor
            editor = CodeEditor(self, filepath, on_save=lambda p: self._on_file_saved(p))
            editor.Show()
            editor.Raise()
            editor.SetFocus()
        except ImportError:
            os.startfile(filepath)

    def _on_file_saved(self, filepath):
        self._status.SetLabel(f"已保存: {os.path.basename(filepath)}")
        self._refresh_file_tree()

    # ============================================================
    # 自动保存
    # ============================================================

    def _auto_save_blocks(self, text, force=False):
        saved = 0
        pattern = r'===\s*(PLUGIN|HTML|APP):\s*(\S+)\s*===\s*\n(.*?)=== END ==='
        matches = re.finditer(pattern, text, re.DOTALL)
        for m in matches:
            filepath = m.group(2).strip()
            code = m.group(3).strip()
            if '..' in filepath or filepath.startswith('/'):
                continue
            full = self._get_full_path(filepath)
            if not force and full and full.exists() and full.read_text(encoding='utf-8') == code:
                continue
            self._write_file(filepath, code)
            saved += 1

        if saved > 0:
            where = self._current_project["name"] or "_cache"
            self._status.SetLabel(f"✅ 已保存 {saved} 个文件 → {where}")
            wx.CallAfter(self._refresh_file_tree)

    def _get_full_path(self, filepath):
        if self._current_project["name"]:
            filepath = filepath.replace("{project}", self._current_project["name"])
        if filepath.startswith('www/'):
            if self._current_project["name"]:
                return Path(f"www/{self._current_project['name']}") / filepath[4:]
        elif filepath.startswith('plugins/'):
            if self._current_project["path"]:
                return Path(self._current_project["path"]) / filepath[8:]
        return None

    def _write_file(self, filepath, code):
        if self._current_project["name"]:
            filepath = filepath.replace("{project}", self._current_project["name"])

        if self._current_project["name"]:
            # 直接拼到项目根目录
            full = Path(".") / filepath
        else:
            full = Path("plugins/_cache") / Path(filepath).name

        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(code, encoding='utf-8')