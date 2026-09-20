"""屏幕数字探头 — Vision Agent 驱动版"""
import asyncio
import uuid
import time
import base64
import hashlib
import wx
import threading
import ctypes
import json
from ctypes import wintypes
from io import BytesIO
from datetime import datetime
from pathlib import Path
import traceback

from PIL import ImageGrab, Image
from runtime.wx_utils import get_app


# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def get_virtual_desktop():
    """获取完整虚拟桌面边界"""
    left = ctypes.windll.user32.GetSystemMetrics(76)
    top = ctypes.windll.user32.GetSystemMetrics(77)
    width = ctypes.windll.user32.GetSystemMetrics(78)
    height = ctypes.windll.user32.GetSystemMetrics(79)
    return left, top, width, height


def get_window_info(hwnd):
    """获取窗口信息"""
    if not hwnd:
        return {}
    class_name = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, class_name, 255)
    title = ctypes.create_unicode_buffer(1024)
    ctypes.windll.user32.GetWindowTextW(hwnd, title, 1023)
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return {
        "hwnd": hwnd,
        "class": class_name.value,
        "title": title.value,
        "rect": (rect.left, rect.top, rect.right, rect.bottom)
    }


def window_from_point(x, y):
    """根据屏幕坐标获取窗口句柄"""
    pt = wintypes.POINT(x, y)
    return ctypes.windll.user32.WindowFromPoint(pt)


def get_top_parent(hwnd):
    """获取最顶层父窗口"""
    while True:
        parent = ctypes.windll.user32.GetParent(hwnd)
        if parent == 0:
            break
        hwnd = parent
    return hwnd


# ═══════════════════════════════════════════
# DevourViewfinder — 吞噬窗口
# ═══════════════════════════════════════════

class DevourViewfinder(wx.Frame):
    """吞噬取景框 — 把目标窗口的一块区域嵌入到自己的容器里"""
    
    def __init__(self, probe_id, name, hwnd_target, crop_abs_region, crop_rel_region, on_close_callback=None):
        crop_w, crop_h = crop_rel_region[2], crop_rel_region[3]
        bar_h = 32
        
        super().__init__(None, title=f"🔍 {name}",
                         size=(crop_w, crop_h + bar_h),
                         pos=(crop_abs_region[0], crop_abs_region[1]),
                         style=wx.STAY_ON_TOP | wx.CAPTION | wx.CLOSE_BOX | wx.FRAME_NO_TASKBAR)
        
        self.probe_id = probe_id
        self.name = name
        self.hwnd_target = hwnd_target
        self.crop_rel_region = crop_rel_region
        self.on_close_callback = on_close_callback
        
        self.orig_rect = None
        self.orig_parent = None
        self.orig_style = None
        self._closed = False
        self._lock = threading.Lock()
        
        self.SetBackgroundColour("#1e1e1e")
        
        # ── 信号灯条 ──
        self.bar = wx.Panel(self, size=(-1, bar_h))
        self.bar.SetBackgroundColour("#2d2d2d")
        bar_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.dot = wx.Panel(self.bar, size=(10, 10))
        self.dot.SetBackgroundColour("#22c55e")
        bar_sizer.Add(self.dot, 0, wx.ALL | wx.ALIGN_CENTER, 5)
        
        self.label = wx.StaticText(self.bar, label=f"{name[:15]}")
        self.label.SetForegroundColour("#cccccc")
        bar_sizer.Add(self.label, 0, wx.ALL | wx.ALIGN_CENTER, 5)
        
        self.value_label = wx.StaticText(self.bar, label="等待中...")
        self.value_label.SetForegroundColour("#888888")
        bar_sizer.Add(self.value_label, 0, wx.ALL | wx.ALIGN_CENTER, 5)
        
        self.mode_label = wx.StaticText(self.bar, label="")
        self.mode_label.SetForegroundColour("#6366f1")
        bar_sizer.Add(self.mode_label, 0, wx.ALL | wx.ALIGN_CENTER, 5)
        
        bar_sizer.AddStretchSpacer()
        
        self.close_btn = wx.Button(self.bar, label="✕", size=(24, 24))
        self.close_btn.Bind(wx.EVT_BUTTON, self._on_close_btn)
        bar_sizer.Add(self.close_btn, 0, wx.ALL | wx.ALIGN_CENTER, 5)
        
        self.bar.SetSizer(bar_sizer)
        
        # ── 容器（目标窗口被吞噬到这里） ──
        self.container = wx.Panel(self)
        self.container.SetBackgroundColour("#000000")
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.bar, 0, wx.EXPAND)
        sizer.Add(self.container, 1, wx.EXPAND)
        self.SetSizer(sizer)
        
        self.Show()
        self.Bind(wx.EVT_CLOSE, self._on_close)
        
        # 延迟吞噬，等待容器句柄就绪
        wx.CallAfter(self._try_devour)
    
    def _try_devour(self):
        """尝试吞噬，容器句柄没好就重试"""
        container_hwnd = self.container.GetHandle()
        if container_hwnd:
            self._devour()
        else:
            wx.CallLater(100, self._try_devour)
    
    def _devour(self):
        """执行窗口吞噬"""
        print(f"[DevourViewfinder] _devour called for {self.probe_id}")
        try:
            if not self.hwnd_target or not ctypes.windll.user32.IsWindow(self.hwnd_target):
                print(f"[DevourViewfinder] target window invalid")
                return
            
            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self.hwnd_target, ctypes.byref(rect))
            self.orig_rect = (rect.left, rect.top, rect.right, rect.bottom)
            self.orig_parent = ctypes.windll.user32.GetParent(self.hwnd_target)
            self.orig_style = ctypes.windll.user32.GetWindowLongW(self.hwnd_target, -16)
            
            orig_w = self.orig_rect[2] - self.orig_rect[0]
            orig_h = self.orig_rect[3] - self.orig_rect[1]
            offset_x = -self.crop_rel_region[0]
            offset_y = -self.crop_rel_region[1]
            
            container_hwnd = self.container.GetHandle()
            
            # 设置父窗口
            ctypes.windll.user32.SetParent(self.hwnd_target, container_hwnd)
            
            # 修改窗口样式
            style = self.orig_style
            style &= ~(0x00C00000 | 0x00080000 | 0x00040000 | 0x00800000 | 0x00020000)
            style |= 0x40000000
            ctypes.windll.user32.SetWindowLongW(self.hwnd_target, -16, style)
            
            # 移动到容器内
            ctypes.windll.user32.SetWindowPos(
                self.hwnd_target, 0, offset_x, offset_y, orig_w, orig_h, 0x0004 | 0x0040
            )
            print(f"[DevourViewfinder] devoured window {self.hwnd_target}")
        except Exception as e:
            print(f"[DevourViewfinder] _devour error: {e}")
            traceback.print_exc()
    
    def _on_close_btn(self, event):
        """点击关闭按钮"""
        self._do_close()
    
    def _on_close(self, event):
        """窗口关闭事件"""
        self._do_close()
        if event:
            event.Skip()
    
    def _do_close(self):
        """统一的关闭处理"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        
        if self.on_close_callback:
            self.on_close_callback()
        
        self.Hide()
    
    def close_viewfinder(self):
        """线程安全的关闭"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        
        def _do():
            try:
                if self.on_close_callback:
                    wx.CallAfter(self.on_close_callback)
                self.Hide()
                self.Destroy()
            except Exception:
                pass
        wx.CallAfter(_do)
    
    def release(self):
        """释放窗口回原位"""
        print(f"[DevourViewfinder] release called for {self.probe_id}")
        try:
            if self.hwnd_target and ctypes.windll.user32.IsWindow(self.hwnd_target):
                if self.orig_parent:
                    ctypes.windll.user32.SetParent(self.hwnd_target, self.orig_parent)
                else:
                    ctypes.windll.user32.SetParent(self.hwnd_target, None)
                
                if self.orig_style:
                    ctypes.windll.user32.SetWindowLongW(self.hwnd_target, -16, self.orig_style)
                
                if self.orig_rect:
                    x1, y1, x2, y2 = self.orig_rect
                    ctypes.windll.user32.SetWindowPos(self.hwnd_target, 0, x1, y1, x2-x1, y2-y1, 0x0004)
        except Exception as e:
            print(f"[DevourViewfinder] release error: {e}")
            traceback.print_exc()
        
        try:
            self.Destroy()
        except Exception:
            pass
    
    def update_display(self, text, mode_label=""):
        """线程安全更新显示"""
        if self._closed:
            return
        
        def _do():
            try:
                if not self._closed:
                    self.value_label.SetLabel(text[:40] if text else '--')
                    if mode_label:
                        self.mode_label.SetLabel(mode_label)
                    self.dot.SetBackgroundColour("#22c55e" if text else "#555555")
                    self.dot.Refresh()
            except Exception:
                pass
        wx.CallAfter(_do)
    
    def set_alert(self, alert=True):
        """设置告警状态"""
        if self._closed:
            return
        def _do():
            try:
                if not self._closed:
                    self.dot.SetBackgroundColour("#ef4444" if alert else "#22c55e")
                    self.dot.Refresh()
            except Exception:
                pass
        wx.CallAfter(_do)


# ═══════════════════════════════════════════
# ProbeInstance — 探头实例
# ═══════════════════════════════════════════

class ProbeInstance:
    def __init__(self, agent, probe_id, name, region, interval, rules, mode="vision",
                 hwnd_target=None):
        print(f"[ProbeInstance.__init__] START: {name}, mode={mode}")
        self.agent = agent
        self.id = probe_id
        self.name = name
        self.region = region  # {x1, y1, x2, y2}
        self.interval = interval
        self.rules = rules  # [{type, label, prompt, trigger}]
        self.mode = mode
        self.hwnd_target = hwnd_target
        self.running = False
        self._loop_task = None
        self.history = []
        
        # 变化检测
        self._last_fingerprint = None
        self._last_image = None
        
        # 窗口吞噬
        self._viewfinder = None
        self._viewfinder_ready = asyncio.Event()
        self._crop_rel_region = None
        
        # 调试
        self._debug_dir = Path("data/screenshots")
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        print(f"[ProbeInstance.__init__] END")
    
    def _on_viewfinder_closed(self):
        """viewfinder 被用户关闭"""
        # print(f"[ProbeInstance] viewfinder closed for {self.id}")
        # self._viewfinder = None
        # self._viewfinder_ready.clear()
        # self.stop()
        print(f"[ProbeInstance] viewfinder closed for {self.id}")
        self.stop()  # ← 先 stop，会调用 _release_viewfinder()
        self._viewfinder = None
        self._viewfinder_ready.clear()
    
    def start(self):
        """启动探头"""
        print(f"[ProbeInstance.start] START: {self.name}")
        if self.running:
            return
        self.running = True
        self._loop_task = asyncio.ensure_future(self._loop())
        self._start_wx()
        print(f"[ProbeInstance.start] END")
    
    def stop(self):
        """停止探头"""
        print(f"[ProbeInstance.stop] START: {self.name}")
        self.running = False
        if self._loop_task:
            self._loop_task.cancel()
        if self._viewfinder:
            self._release_viewfinder()
        print(f"[ProbeInstance.stop] END")
    
    def _start_wx(self):
        """在 wx 主线程创建 viewfinder"""
        _wx_app = get_app()
        wx.CallAfter(self._create_viewfinder)
        
        # 等待创建完成
        async def wait_ready():
            try:
                await asyncio.wait_for(self._viewfinder_ready.wait(), timeout=5)
            except asyncio.TimeoutError:
                print(f"[ProbeInstance] viewfinder creation timeout")
        
        asyncio.ensure_future(wait_ready())
    
    def _create_viewfinder(self):
        """在 wx 主线程中创建吞噬窗口"""
        print(f"[ProbeInstance._create_viewfinder] START for {self.id}")
        try:
            abs_x1 = self.region["x1"]
            abs_y1 = self.region["y1"]
            abs_w = self.region["x2"] - self.region["x1"]
            abs_h = self.region["y2"] - self.region["y1"]
            
            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self.hwnd_target, ctypes.byref(rect))
            win_x1, win_y1 = rect.left, rect.top
            
            rel_x = abs_x1 - win_x1
            rel_y = abs_y1 - win_y1
            self._crop_rel_region = (rel_x, rel_y, abs_w, abs_h)
            
            self._viewfinder = DevourViewfinder(
                self.id, self.name, self.hwnd_target,
                (abs_x1, abs_y1, abs_w, abs_h),
                self._crop_rel_region,
                on_close_callback=self._on_viewfinder_closed
            )
            
            # 通知 asyncio 端
            self.agent.loop.call_soon_threadsafe(self._viewfinder_ready.set)
            print(f"[ProbeInstance] viewfinder created for {self.id}")
        except Exception as e:
            print(f"[ProbeInstance._create_viewfinder] error: {e}")
            traceback.print_exc()
    
    def _release_viewfinder(self):
        """释放 viewfinder"""
        vf = self._viewfinder
        if not vf:
            return
        self._viewfinder = None
        self._viewfinder_ready.clear()
        
        vf.release()
    
    async def _loop(self):
        """主循环"""
        print(f"[ProbeInstance._loop] START: {self.name}")
        
        # 等待 viewfinder 就绪
        try:
            await asyncio.wait_for(self._viewfinder_ready.wait(), timeout=5)
        except asyncio.TimeoutError:
            print(f"[ProbeInstance] viewfinder not ready, stopping")
            self.running = False
            return
        
        error_count = 0
        max_errors = 10  # 🔥 连续报错 10 次就停
        
        while self.running:
            if not self._viewfinder or self._viewfinder._closed:
                print(f"[ProbeInstance] viewfinder gone, stopping")
                break
            
            try:
                await self._process_cycle()
                error_count = 0  # 正常执行，重置计数
            except asyncio.CancelledError:
                break
            except Exception as e:
                error_count += 1
                print(f"[ProbeInstance] loop error ({error_count}/{max_errors}): {e}")
                traceback.print_exc()
                
                if error_count >= max_errors:
                    print(f"[ProbeInstance] too many errors, stopping")
                    self.running = False
                    break
            
            await asyncio.sleep(self.interval)
        
        # 🔥 无论如何都要清理
        print(f"[ProbeInstance._loop] exiting, cleaning up...")
        if self._viewfinder:
            self._release_viewfinder()
        print(f"[ProbeInstance._loop] END")
    
    async def _process_cycle(self):
        """处理一个监控周期"""
        # 1. 截图吞噬窗口内容
        image_b64 = self._capture_viewfinder()
        if not image_b64:
            return
        
        # 2. 变化检测
        if not self._has_changed(image_b64):
            return
        
        self._last_image = image_b64
        
        # 3. 遍历规则，执行匹配的
        for rule in self.rules:
            # 🔥 兼容字符串规则
            if isinstance(rule, str):
                prompt = rule
                allow_actions = False
                rule_label = rule[:30]
                rule_type = "vision_analyze"
            else:
                rule_type = rule.get("type", "vision_analyze")
                allow_actions = (rule_type == "vision_action")
                prompt = rule.get("prompt", "分析这张截图的内容")
                rule_label = rule.get("label", prompt[:30])
            
            if not self._should_trigger(rule):
                continue
            
            mode_label = "🔧操作" if allow_actions else "🔍分析"
            print(f"[Probe {self.id}] 触发: {rule_label} ({mode_label})")
            
            if self._viewfinder and not self._viewfinder._closed:
                self._viewfinder.update_display(prompt[:40], mode_label)
            
            # 4. 调 Vision Agent
            result = await self._call_vision(prompt, allow_actions)
            
            if result:
                # 5. 记录历史
                self.history.append({
                    "time": datetime.now().isoformat(),
                    "text": result.get("result", ""),
                    "thinking": result.get("thinking", ""),
                    "rule": rule_label,
                    "actions": result.get("actions", []),
                    "done": result.get("done", False),
                    "error": result.get("error", ""),
                })
                
                # 6. 推送结果
                await self._push_result(result, rule if not isinstance(rule, str) else {"label": rule_label})
                
                # 7. 更新显示
                if self._viewfinder and not self._viewfinder._closed:
                    display = result.get("result", "")[:40]
                    if result.get("error"):
                        self._viewfinder.set_alert(True)
                    self._viewfinder.update_display(display, mode_label)
                
                # 8. 触发告警
                if result.get("done") or result.get("error"):
                    await self._alert(rule if not isinstance(rule, str) else {"label": rule_label}, result)
    
    def _capture_viewfinder(self):
        """截取吞噬窗口容器内的内容"""
        if not self._viewfinder or not self._viewfinder.container:
            return None
        
        container = self._viewfinder.container
        hwnd = container.GetHandle()
        if not hwnd:
            return None
        
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        
        x, y = rect.left, rect.top
        w, h = rect.right - rect.left, rect.bottom - rect.top
        
        if w <= 0 or h <= 0:
            return None
        
        desktop_left, desktop_top, desktop_w, desktop_h = get_virtual_desktop()
        
        full_img = ImageGrab.grab(
            bbox=(desktop_left, desktop_top, desktop_left + desktop_w, desktop_top + desktop_h),
            all_screens=True
        )
        
        rel_x = x - desktop_left
        rel_y = y - desktop_top
        img = full_img.crop((rel_x, rel_y, rel_x + w, rel_y + h))
        
        buf = BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    
    def _has_changed(self, image_b64):
        """感知哈希变化检测"""
        try:
            img_data = base64.b64decode(image_b64)
            img = Image.open(BytesIO(img_data))
            
            thumb = img.resize((32, 32), Image.LANCZOS).convert("L")
            pixels = list(thumb.getdata())
            avg = sum(pixels) / len(pixels)
            fingerprint = "".join(["1" if p > avg else "0" for p in pixels])
            
            if self._last_fingerprint is None:
                self._last_fingerprint = fingerprint
                return True
            
            if self._last_fingerprint == fingerprint:
                return False
            
            diff = sum(c1 != c2 for c1, c2 in zip(fingerprint, self._last_fingerprint))
            similarity = 1 - (diff / len(fingerprint))
            
            self._last_fingerprint = fingerprint
            
            if similarity > 0.95:
                return False
            
            return True
        except Exception as e:
            print(f"[ProbeInstance] hash error: {e}")
            return True
    
    async def _call_vision(self, prompt, allow_actions):
        """调用 Vision Agent"""
        if not self._viewfinder or not self._viewfinder.container:
            return None
        
        # 🔥 获取吞噬窗口容器在屏幕上的绝对坐标
        container = self._viewfinder.container
        hwnd = container.GetHandle()
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        
        region = {
            "x": rect.left,
            "y": rect.top,
        }
        
        try:
            import core
            result = await self.agent.system.call(core.Envelop(
                sender=f"probe/{self.id}",
                receiver="builtins/agents/vision",
                payload={
                    "image": self._last_image,
                    "intent": prompt,
                    "channel": f"probe_{self.id}",
                    "reset": True,
                    "allow_actions": allow_actions,
                    "region": region
                }
            ))
            if result and result.payload:
                return result.payload
            return None
        except Exception as e:
            print(f"[Probe {self.id}] vision error: {e}")
            return None
    
    def _should_trigger(self, rule):
        """判断规则是否应该触发"""
        # 🔥 兼容旧格式（字符串规则）
        if isinstance(rule, str):
            return True  # 字符串规则始终触发
        
        trigger = rule.get("trigger", "always")
        
        if trigger == "always":
            return True
        
        if trigger == "threshold":
            last = self.history[-1] if self.history else None
            if not last:
                return False
            
            import re
            text = last.get("text", "")
            numbers = re.findall(r'[\d.]+', text)
            if not numbers:
                return False
            
            try:
                current = float(numbers[0])
                op = rule.get("op", ">")
                value = rule.get("value", 0)
                return (op == ">" and current > value) or (op == "<" and current < value)
            except:
                pass
        
        if trigger == "on_change":
            if len(self.history) >= 2:
                return self.history[-1].get("text", "") != self.history[-2].get("text", "")
            return len(self.history) == 1
        
        return False
    
    async def _push_result(self, result, rule):
        """推送 Vision 结果到 WebSocket"""
        if hasattr(self.agent, 'ws_gateway'):
            await self.agent.ws_gateway.broadcast({
                "type": "probe_vision_result",
                "probe_id": self.id,
                "name": self.name,
                "rule": rule.get("label", ""),
                "result": result.get("result", ""),
                "thinking": result.get("thinking", ""),
                "done": result.get("done", False),
                "error": result.get("error", ""),
                "actions": result.get("actions", []),
                "time": datetime.now().isoformat(),
            })
    
    async def _alert(self, rule, result):
        """触发告警"""
        alert_data = {
            "type": "probe_alert",
            "probe_id": self.id,
            "name": self.name,
            "rule": rule.get("label", ""),
            "result": result.get("result", ""),
            "error": result.get("error", ""),
            "time": datetime.now().isoformat(),
        }
        
        if hasattr(self.agent, 'ws_gateway'):
            await self.agent.ws_gateway.broadcast(alert_data)
    
    def status(self):
        """获取探头状态"""
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "region": self.region,
            "interval": self.interval,
            "running": self.running,
            "rules": self.rules,
            "history_count": len(self.history),
            "last_result": self.history[-1] if self.history else None,
        }


# ═══════════════════════════════════════════
# ProbeManager — 探头管理器
# ═══════════════════════════════════════════

class ProbeManager:
    def __init__(self, agent):
        self.agent = agent
        self._probes = {}
        self._config_file = Path("data/probes_config.json")
        print(f"[ProbeManager] initialized")
    
    def create(self, name, region, interval, rules, mode="vision",
               hwnd_target=None, probe_id=None):
        """创建探头"""
        if probe_id is None:
            probe_id = str(uuid.uuid4())[:8]
        
        probe = ProbeInstance(
            self.agent, probe_id, name, region, interval, rules,
            mode, hwnd_target
        )
        self._probes[probe_id] = probe
        
        # 异步启动
        asyncio.ensure_future(self._start_probe(probe))
        
        # 保存配置
        self._save_config()
        
        return probe_id
    
    async def _start_probe(self, probe):
        """启动探头"""
        await asyncio.sleep(0.5)
        probe.start()
        await asyncio.sleep(0.3)
    
    def start(self, probe_id):
        """启动指定探头"""
        if probe_id in self._probes and not self._probes[probe_id].running:
            asyncio.ensure_future(self._start_probe(self._probes[probe_id]))
    
    def stop(self, probe_id):
        """停止指定探头"""
        if probe_id in self._probes:
            self._probes[probe_id].stop()
    
    def delete(self, probe_id):
        """删除探头"""
        print(f"[ProbeManager.delete] deleting {probe_id}")
        self.stop(probe_id)
        if probe_id in self._probes:
            self._probes.pop(probe_id, None)
        self._save_config()
    
    def get(self, probe_id):
        """获取探头实例"""
        return self._probes.get(probe_id)
    
    def list_probes(self):
        """列出所有探头状态"""
        return [p.status() for p in self._probes.values()]
    
    def add_rule(self, probe_id, rule):
        """给探头添加规则"""
        if probe_id in self._probes:
            self._probes[probe_id].rules.append(rule)
            self._save_config()
            return True
        return False
    
    def remove_rule(self, probe_id, rule_index):
        """删除探头的某条规则"""
        if probe_id in self._probes:
            probe = self._probes[probe_id]
            if 0 <= rule_index < len(probe.rules):
                probe.rules.pop(rule_index)
                self._save_config()
                return True
        return False
    
    def _save_config(self):
        """保存探头配置到 JSON"""
        config = {"probes": []}
        for probe_id, probe in self._probes.items():
            config["probes"].append({
                "id": probe_id,
                "name": probe.name,
                "region": probe.region,
                "interval": probe.interval,
                "rules": probe.rules,
                "mode": probe.mode,
                "hwnd": probe.hwnd_target,
                "auto_start": probe.running,
            })
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        self._config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ProbeManager] saved {len(config['probes'])} probes to config")
    
    def load_saved_probes(self):
        """从配置文件恢复探头"""
        if not self._config_file.exists():
            return
        
        try:
            config = json.loads(self._config_file.read_text(encoding="utf-8"))
            for probe_cfg in config.get("probes", []):
                if probe_cfg.get("auto_start", True):
                    self.create(
                        name=probe_cfg.get("name", "未命名"),
                        region=probe_cfg.get("region", {}),
                        interval=probe_cfg.get("interval", 5),
                        rules=probe_cfg.get("rules", []),
                        mode=probe_cfg.get("mode", "vision"),
                        hwnd_target=probe_cfg.get("hwnd"),
                        probe_id=probe_cfg.get("id"),
                    )
            print(f"[ProbeManager] loaded {len(config.get('probes', []))} probes from config")
        except Exception as e:
            print(f"[ProbeManager] load config error: {e}")