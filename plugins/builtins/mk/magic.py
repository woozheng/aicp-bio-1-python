"""魔法鼠标 — 框选 → vision_agent → 循环执行直到完成（适配 MKState）"""
import asyncio
import base64
import ctypes
import threading
import time
import json
import io
from io import BytesIO
from PIL import Image, ImageGrab
import numpy as np
import wx
import pyperclip
import core
from .state import get_state 


def _screenshot_region(x1, y1, x2, y2):
    """截取屏幕指定区域"""
    try:
        desktop_left = ctypes.windll.user32.GetSystemMetrics(76)
        desktop_top = ctypes.windll.user32.GetSystemMetrics(77)
        desktop_w = ctypes.windll.user32.GetSystemMetrics(78)
        desktop_h = ctypes.windll.user32.GetSystemMetrics(79)
        full = ImageGrab.grab(
            bbox=(desktop_left, desktop_top, desktop_left + desktop_w, desktop_top + desktop_h),
            all_screens=True
        )
        return full.crop((x1 - desktop_left, y1 - desktop_top,
                          x2 - desktop_left, y2 - desktop_top))
    except Exception as e:
        print(f"[MagicMouse] Screenshot error: {e}")
        return None


# ═══════════════════════════════════════════
# MagicCard
# ═══════════════════════════════════════════

class MagicCard(wx.Frame):
    _instances = []

    def __init__(self, agent, title, body, actions, ready_event=None, on_action_callback=None):
        sw, sh = wx.GetDisplaySize()
        super().__init__(None, title=title, size=(520, sh - 100),
                         style=wx.STAY_ON_TOP | wx.CAPTION | wx.CLOSE_BOX |
                               wx.RESIZE_BORDER | wx.MINIMIZE_BOX | wx.MAXIMIZE_BOX)
       
        self.agent = agent
        self._on_action_callback = on_action_callback
        self._closed = False
        self._lock = threading.Lock()

        c_bg = wx.Colour(24, 24, 27)
        c_input_bg = wx.Colour(18, 18, 22)
        c_text = wx.Colour(228, 228, 238)
        c_muted = wx.Colour(140, 140, 155)
        c_accent = wx.Colour(129, 140, 248)
        c_execute = wx.Colour(34, 197, 94)
        c_btn = wx.Colour(45, 45, 52)
        c_border = wx.Colour(39, 39, 45)
        c_log_bg = wx.Colour(15, 15, 18)

        self.SetBackgroundColour(c_bg)
        self.SetMinSize(wx.Size(400, 400))
        self.SetPosition((sw - 520 - 20, 50))

        panel = wx.Panel(self)
        panel.SetBackgroundColour(c_bg)
        panel.SetDoubleBuffered(True)

        sizer = wx.BoxSizer(wx.VERTICAL)

        # ── 正文区域 ──
        self.body_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_NONE)
        self.body_text.SetBackgroundColour(c_bg)
        self.body_text.SetForegroundColour(c_text)
        self.body_text.SetFont(wx.Font(12, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.body_text.SetValue(body)
        sizer.Add(self.body_text, 2, wx.EXPAND | wx.ALL, 16)

        # ── 操作日志 ──
        log_label = wx.StaticText(panel, label="📋 操作日志")
        log_label.SetForegroundColour(c_muted)
        log_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        sizer.Add(log_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)

        self.log_text = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_NONE)
        self.log_text.SetBackgroundColour(c_log_bg)
        self.log_text.SetForegroundColour(c_accent)
        self.log_text.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.log_text.SetMinSize(wx.Size(-1, 60))
        sizer.Add(self.log_text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)

        # ── 按钮 ──
        self.btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        for act in actions:
            btn = wx.Button(panel, label=act["label"], size=wx.Size(-1, 36))
            btn.SetBackgroundColour(c_btn)
            btn.SetForegroundColour(c_text)
            btn.SetFont(wx.Font(11, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
            btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            btn.Bind(wx.EVT_BUTTON, lambda e, v=act["value"]: self._on_action(v))
            self.btn_sizer.Add(btn, 1, wx.LEFT, 6)
        sizer.Add(self.btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        # ── 分隔线 ──
        sep = wx.Panel(panel, size=(-1, 1))
        sep.SetBackgroundColour(c_border)
        sizer.Add(sep, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 16)

        # ── 输入行 ──
        input_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.input_entry = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER, value="输入追问或操作指令...")
        self.input_entry.SetBackgroundColour(c_input_bg)
        self.input_entry.SetForegroundColour(c_muted)
        self.input_entry.SetFont(wx.Font(12, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.input_entry.SetMinSize(wx.Size(-1, 40))
        self.input_entry.Bind(wx.EVT_SET_FOCUS, self._clear_placeholder)
        self.input_entry.Bind(wx.EVT_KILL_FOCUS, self._restore_placeholder)
        self.input_entry.Bind(wx.EVT_TEXT_ENTER, lambda e: self._on_send("__input__"))
        input_sizer.Add(self.input_entry, 1, wx.EXPAND | wx.RIGHT, 6)

        check_btn = wx.Button(panel, label="🔍 分析", size=wx.Size(80, 40))
        check_btn.SetBackgroundColour(c_accent)
        check_btn.SetForegroundColour(wx.Colour(255, 255, 255))
        check_btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        check_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_send("__check__"))
        input_sizer.Add(check_btn, 0, wx.RIGHT, 4)

        action_btn = wx.Button(panel, label="🔧 操作", size=wx.Size(80, 40))
        action_btn.SetBackgroundColour(c_execute)
        action_btn.SetForegroundColour(wx.Colour(0, 0, 0))
        action_btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        action_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_send("__action__"))
        input_sizer.Add(action_btn, 0, wx.RIGHT, 4)

        send_btn = wx.Button(panel, label="发送", size=wx.Size(60, 40))
        send_btn.SetBackgroundColour(c_btn)
        send_btn.SetForegroundColour(c_text)
        send_btn.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        send_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_send("__input__"))
        input_sizer.Add(send_btn, 0)

        sizer.Add(input_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.SetSizer(sizer)

        self.Bind(wx.EVT_CLOSE, self._on_close)
        MagicCard._instances.append(self)
        if ready_event:
            ready_event.set()

    def refresh(self, title, body):
        if self._closed:
            return
        def _do():
            try:
                if not self._closed:
                    self.SetTitle(title)
                    self.body_text.SetEditable(True)
                    self.body_text.ChangeValue(body)
                    self.body_text.SetEditable(False)
            except Exception:
                pass
        wx.CallAfter(_do)

    def append_log(self, msg):
        if self._closed:
            return
        def _do():
            try:
                if not self._closed:
                    timestamp = time.strftime("%H:%M:%S")
                    self.log_text.AppendText(f"[{timestamp}] {msg}\n")
                    self.log_text.ShowPosition(self.log_text.GetLastPosition())
            except Exception:
                pass
        wx.CallAfter(_do)

    def clear_log(self):
        if self._closed:
            return
        def _do():
            try:
                if not self._closed:
                    self.log_text.Clear()
            except Exception:
                pass
        wx.CallAfter(_do)

    def _clear_placeholder(self, event):
        if self.input_entry.GetValue() == "输入追问或操作指令...":
            self.input_entry.SetValue("")
            self.input_entry.SetForegroundColour(wx.Colour(228, 228, 238))
        event.Skip()

    def _restore_placeholder(self, event):
        if not self.input_entry.GetValue().strip():
            self.input_entry.SetValue("输入追问或操作指令...")
            self.input_entry.SetForegroundColour(wx.Colour(140, 140, 155))
        event.Skip()

    def _on_send(self, action_type):
        with self._lock:
            if self._closed:
                return
            text = self.input_entry.GetValue().strip()
            if text and text != "输入追问或操作指令...":
                self.input_entry.SetValue("")
                if self._on_action_callback:
                    wx.CallAfter(self._on_action_callback, {"text": text, "selected": action_type})

    def _on_action(self, value):
        with self._lock:
            if self._closed:
                return
            if self._on_action_callback:
                wx.CallAfter(self._on_action_callback, {"selected": value})

    def _on_close(self, event):
        with self._lock:
            if self._closed:
                event.Skip()
                return
            self._closed = True
            if self._on_action_callback:
                wx.CallAfter(self._on_action_callback, {"dismissed": True})
            self.Hide()
            event.Skip()

    def close_card(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        def _do():
            try:
                if self._on_action_callback:
                    wx.CallAfter(self._on_action_callback, {"dismissed": True})
                self.Hide()
                self.Destroy()
            except Exception:
                pass
        wx.CallAfter(_do)
        with self._lock:
            if self in MagicCard._instances:
                MagicCard._instances.remove(self)

    @classmethod
    def close_all(cls):
        for card in list(cls._instances):
            card.close_card()
        cls._instances.clear()


# ═══════════════════════════════════════════
# 核心逻辑 — 使用 MKState 属性
# ═══════════════════════════════════════════

async def handle_select_done(envelop, agent, state):
    try:
        selection = envelop.payload.get("selection", {})
        print(f"[MagicMouse] Selection: {selection}")

        image_b64 = _capture(selection)
        if not image_b64:
            return core.Envelop(payload={"error": "截图失败"})

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
            asyncio.create_task(run_card_loop(agent, state, "分析结果", body, channel))

        return core.Envelop(payload={"done": True})
    except Exception as e:
        print(f"[MagicMouse] handle_select_done error: {e}")
        return core.Envelop(payload={"error": str(e)})


def _capture(selection):
    try:
        img = _screenshot_region(selection["x1"], selection["y1"],
                                 selection["x2"], selection["y2"])
        if img is None:
            return None
        buf = BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"[MagicMouse] capture error: {e}")
        return None


async def call_vision(agent, image_b64, intent, channel, reset=False, allow_actions=False, region=None):
    try:
        result = await agent.system.call(core.Envelop(
            sender="builtins/mk/magic",
            receiver="builtins/agents/vision",
            payload={
                "image": image_b64,
                "intent": intent,
                "channel": channel,
                "reset": reset,
                "allow_actions": allow_actions,
                "region": {
                    "x": region.get("x1", 0) if region else 0,
                    "y": region.get("y1", 0) if region else 0,
                } if region else {}
            }
        ))
        if result and result.payload:
            return result.payload
        return None
    except Exception as e:
        print(f"[MagicMouse] vision error: {e}")
        return None


def _images_differ(img1_b64, img2_b64, threshold=0.95):
    try:
        img1 = Image.open(io.BytesIO(base64.b64decode(img1_b64)))
        img2 = Image.open(io.BytesIO(base64.b64decode(img2_b64)))
        img1 = img1.resize((200, 150)).convert('L')
        img2 = img2.resize((200, 150)).convert('L')
        arr1 = np.array(img1, dtype=np.float64)
        arr2 = np.array(img2, dtype=np.float64)
        diff = np.abs(arr1 - arr2).mean()
        similarity = 1.0 - diff / 255.0
        return similarity < threshold, similarity
    except Exception as e:
        print(f"[MagicMouse] Image compare error: {e}")
        return True, 0


async def run_card_loop(agent, state, title, body, channel):
    """卡片循环 - 带停止检查"""
    actions = [{"label": "📋 复制", "value": "copy"}]
    event_queue = asyncio.Queue()
    ready_event = threading.Event()
    card_holder = {}

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()

    def on_card_action(result):
        try:
            asyncio.run_coroutine_threadsafe(event_queue.put(result), loop)
        except Exception as e:
            print(f"[MagicMouse] Failed to queue event: {e}")

    def create():
        try:
            # 🔥 窗口创建不检查任何标志，必须能正常创建
            card = MagicCard(agent, title, body, actions,
                             ready_event=ready_event, on_action_callback=on_card_action)
            card_holder['card'] = card
            card.Show()
            card.Raise()
        except Exception as e:
            print(f"[MagicMouse] Failed to create card: {e}")
            ready_event.set()

    wx.CallAfter(create)

    try:
        await loop.run_in_executor(None, ready_event.wait, 5)
    except Exception:
        pass

    card = card_holder.get('card')
    if not card:
        return

    try:
        # 🔥 主循环：只在处理用户交互时检查停止标志
        while True:
            # 检查是否应该停止（仅当有停止请求时退出）
            if state.magic_stop_requested:
                print("[MagicMouse] 收到停止信号，退出主循环")
                break
                
            if card._closed:
                break
                
            try:
                result = await asyncio.wait_for(event_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                # 超时继续循环，检查停止标志
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[MagicMouse] Queue error: {e}")
                break

            if card._closed:
                break

            # 🔥 再次检查停止标志
            if state.magic_stop_requested:
                print("[MagicMouse] 处理事件前收到停止信号")
                break

            if result.get("dismissed"):
                break

            sel = result.get("selected")

            if sel == "copy":
                try:
                    pyperclip.copy(card.body_text.GetValue())
                    card.append_log("📋 已复制到剪贴板")
                except Exception as e:
                    print(f"[MagicMouse] Copy failed: {e}")
                continue

            if sel in ("__check__", "__action__", "__input__"):
                user_msg = result.get("text", "")
                if not user_msg:
                    continue
                allow_actions = (sel == "__action__")
                card.refresh("处理中", "⏳ 执行中...")
                # 🔥 创建任务时传入 state 和 card
                asyncio.create_task(process_user_action(agent, state, card, user_msg, allow_actions, channel))
    except Exception as e:
        print(f"[MagicMouse] Card loop error: {e}")
    finally:
        # 确保卡片被关闭
        if card and not card._closed:
            card.close_card()
        print("[MagicMouse] Card loop ended")


async def process_user_action(agent, state, card, user_msg, allow_actions, channel):
    """处理用户操作 - 每步检查停止标志"""
    try:
        # 🔥 初始检查
        if state.magic_stop_requested:
            print("[MagicMouse] 操作被停止信号中断")
            card.append_log("⏹️ 已停止")
            card.refresh("已停止", "⏹️ 用户已停止操作")
            return

        selection = state.magic_selection
        image_b64 = state.magic_last_image

        if not image_b64:
            card.refresh("错误", "❌ 没有可用的截图")
            return

        card.clear_log()
        card.append_log(f"🎯 任务：{user_msg}")

        current_msg = user_msg
        previous_image = None

        for step in range(1, 11):
            # 🔥 每一步都检查停止标志
            if state.magic_stop_requested or card._closed:
                print(f"[MagicMouse] 第{step}步被停止信号中断")
                card.append_log("⏹️ 已停止")
                card.refresh("已停止", "⏹️ 用户已停止操作")
                return

            card.append_log(f"📸 第{step}步：分析截图...")

            vr = await call_vision(
                agent, image_b64, current_msg,
                channel=f"{channel}_step{step}",
                reset=True,
                allow_actions=allow_actions,
                region=selection
            )

            # 🔥 检查是否被停止
            if state.magic_stop_requested or card._closed:
                print(f"[MagicMouse] 分析完成后被停止信号中断")
                return

            if not vr:
                card.append_log("❌ 分析失败")
                card.refresh("错误", "❌ 分析失败")
                break

            if vr.get("thinking"):
                card.append_log(f"💭 {vr['thinking'][:120]}")

            title = user_msg[:30] if user_msg else "结果"

            if vr.get("done"):
                card.append_log(f"✅ {vr['result']}")
                card.refresh(title, f"✅ {vr['result']}")
                break

            if vr.get("error"):
                card.append_log(f"🤷 {vr['error']}")
                card.refresh(title, f"🤷 {vr['error']}\n\n{vr.get('result', '')}")
                break

            exec_result = vr.get('exec_result')
            if exec_result is not None and not exec_result.get("all_ok", True):
                card.append_log(f"❌ {exec_result.get('error', '操作失败')}")
                card.refresh(title, f"❌ {vr.get('result', '操作失败')}")
                break

            # 显示执行的动作
            for action in vr.get("actions", []):
                t = action.get("type", "")
                if t == "click":
                    card.append_log(f"🖱️ 点击 ({action.get('x')}, {action.get('y')})")
                elif t == "type":
                    card.append_log(f"⌨️ 输入：{action.get('text', '')}")
                elif t == "hotkey":
                    card.append_log(f"⌨️ 按键：{' + '.join(action.get('keys', []))}")
                elif t == "scroll":
                    card.append_log(f"🔄 滚动 delta={action.get('delta')}")
                elif t == "wait":
                    card.append_log(f"⏳ 等待 {action.get('seconds', 0)} 秒")
                elif t == "screenshot":
                    card.append_log("📸 准备截图确认")

            if vr.get("need_screenshot") and not vr.get("done"):
                # 🔥 截图前检查停止标志
                if state.magic_stop_requested or card._closed:
                    print("[MagicMouse] 截图前被停止信号中断")
                    return

                card.append_log("⏳ 等待界面更新...")
                await asyncio.sleep(1)

                if state.magic_stop_requested or card._closed:
                    print("[MagicMouse] 等待后被停止信号中断")
                    return

                new_image = _capture(selection)
                if not new_image:
                    card.append_log("❌ 截图失败")
                    card.refresh(title, "❌ 截图失败")
                    break

                if previous_image:
                    different, similarity = _images_differ(previous_image, new_image)
                    card.append_log(f"📊 相似度：{similarity:.1%}")
                    if not different:
                        card.append_log("⏳ 未检测到变化，等待 2 秒再试...")
                        await asyncio.sleep(2)
                        
                        if state.magic_stop_requested or card._closed:
                            return
                            
                        new_image = _capture(selection)
                        if new_image:
                            different, similarity = _images_differ(previous_image, new_image)
                            card.append_log(f"📊 再次检查 相似度：{similarity:.1%}")
                            if not different:
                                card.append_log("⚠️ 界面无变化（5秒），停止等待")
                                card.refresh(title, f"📸 {vr.get('result', '界面无变化')}")
                                break

                image_b64 = new_image
                previous_image = new_image
                state.magic_last_image = new_image

                if vr.get("next_prompt"):
                    current_msg = vr["next_prompt"]
                elif vr.get("result"):
                    current_msg = f"上一步：{vr['result']}。请基于当前截图判断。"
                else:
                    current_msg = "请基于当前截图判断是否完成。"

                card.refresh(title, f"📸 第{step}步：{vr.get('result', '继续执行...')}")
                continue

            card.append_log(f"📸 {vr.get('result', '完成')}")
            card.refresh(title, f"📸 {vr.get('result', '')}")
            break
        else:
            card.append_log("⚠️ 达到最大步骤限制（10步）")
            card.refresh("警告", "⚠️ 达到最大步骤限制，已停止")

    except Exception as e:
        print(f"[MagicMouse] process_user_action 异常: {e}")
        import traceback
        traceback.print_exc()
        if not card._closed:
            card.append_log(f"💥 异常：{str(e)}")
            card.refresh("错误", f"❌ {str(e)}")


# ═══════════════════════════════════════════
# LoadingDot
# ═══════════════════════════════════════════

class LoadingDot:
    def __init__(self, selection=None):
        if selection:
            cx = (selection["x1"] + selection["x2"]) // 2
            cy = (selection["y1"] + selection["y2"]) // 2
        else:
            cx, cy = 400, 300
        self.x = cx - 50
        self.y = cy - 20
        self.win = None

    def show(self):
        def _show():
            try:
                self.win = wx.Frame(None, pos=(self.x, self.y), size=(100, 40),
                                    style=wx.STAY_ON_TOP | wx.FRAME_NO_TASKBAR | wx.BORDER_NONE)
                self.win.SetBackgroundColour(wx.Colour(30, 30, 30))
                label = wx.StaticText(self.win, label="⏳ 分析中...", pos=(12, 10))
                label.SetForegroundColour(wx.Colour(99, 102, 241))
                label.SetFont(wx.Font(11, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
                self.win.Show()
            except Exception as e:
                print(f"[MagicMouse] Loading show error: {e}")
        wx.CallAfter(_show)

    def hide(self):
        if self.win:
            def _hide():
                try:
                    if self.win:
                        self.win.Close()
                        self.win = None
                except Exception as e:
                    print(f"[MagicMouse] Loading hide error: {e}")
            wx.CallAfter(_hide)