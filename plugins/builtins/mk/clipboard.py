"""
MK 鼠标事件 — 完整稳定版
所有逻辑都在这里，不依赖 state 的新字段
"""
import asyncio
import time
import threading
import pyautogui
import pyperclip
import wx
from pynput import mouse as pynput_mouse
from .state import get_state
from .prompts import MENU_ITEMS
from .panel import get_panel




# ============================================================
# 安全读取选中文本（Ctrl+Insert，备份剪贴板）
# ============================================================
def get_selected_text_safe():
    """用 Ctrl+Insert 读取选中文字，备份剪贴板"""
    try:
        old = pyperclip.paste()
        pyautogui.hotkey('ctrl', 'insert')
        time.sleep(0.15)
        selected = pyperclip.paste()
        pyperclip.copy(old)
        return selected
    except Exception:
        return ""


# ============================================================
# Popup 菜单回调
# ============================================================
def _on_menu_choice(label):
    if not label:
        return

    state = get_state()
    panel = get_panel()

    if "AI-Eat" in str(label):
        panel.show()
        
        def do_fill():
            text = state.captured_text
            if text and text.strip():
                panel.content_text.SetValue(text[:500])
                panel.status("已捕获: " + text[:50])
                state.captured_text = ""
                state.captured_text_lock = False
            else:
                panel.status("未捕获到文字，请手动输入")
        
        wx.CallLater(100, do_fill)
        return


# ============================================================
# 鼠标事件（只设置 pending_select）
# ============================================================
# clipboard.py
def on_mouse(x, y, button, pressed):
    state = get_state()
    if state.selecting_region:
        return

    if button == pynput_mouse.Button.left:
        if pressed:
            state.left_down_pos = (x, y)
        else:
            if state.left_down_pos:
                sx, sy = state.left_down_pos
                if abs(x - sx) > 10 or abs(y - sy) > 10:
                    state.pending_select = True
                    state.select_pos = (x, y)
            state.left_down_pos = None

    elif button == pynput_mouse.Button.middle:
        # 🔥 检查魔法鼠标是否激活
        if not state.magic_active:
            return  # 如果魔法鼠标未激活，忽略中键
        
        if not pressed:
            pyautogui.click(x, y)
            if state.selecting_region:
                state.selecting_region = False
                try:
                    state.agent.system.hide_all()
                except:
                    pass
                return
            
            async def do_magic():
                # 🔥 再次检查，防止并发问题
                if not state.magic_active:
                    return
                    
                state.selecting_region = True
                try:
                    sel = await state.agent.system.start_selection(fullscreen=True)
                    if sel.get("selection"):
                        import core
                        await state.agent.system.call(core.Envelop(
                            sender="builtins/mk/clipboard",
                            receiver="builtins/mk/_init",
                            payload={
                                "action": "magic_select_done",
                                "selection": sel["selection"],
                            },
                        ))
                finally:
                    state.selecting_region = False
            
            try:
                asyncio.run_coroutine_threadsafe(do_magic(), state.agent.loop)
            except Exception:
                pass
# ============================================================
# 后台轮询线程
# ============================================================
def _monitor_loop():
    """后台监控循环 - 带退出检查"""
    state = get_state()
    print("[MK] 监控循环开始")
    
    while state.monitor_running:  # 🔥 检查运行标志
        time.sleep(0.1)

        if not state.pending_select:
            continue

        state.pending_select = False

        # 读取选中文字
        selected = get_selected_text_safe()
        if not selected or not selected.strip():
            continue
        
        state.captured_text = selected
        state.captured_text_lock = True
        state.selected_text = selected

        # 面板已开 → 直接填入
        panel = get_panel()
        if panel.frame and panel.frame.IsShown():
            panel.content_text.SetValue(selected[:500])
            panel.status("已捕获: " + selected[:50])
            continue

        # 面板没开 → 弹 popup
        px, py = state.select_pos

        async def show_menu():
            from .popup import MKPopup
            actions = [{"label": item, "value": item} for item in MENU_ITEMS]

            import ctypes
            screen_left = ctypes.windll.user32.GetSystemMetrics(76)
            screen_top = ctypes.windll.user32.GetSystemMetrics(77)
            screen_w = ctypes.windll.user32.GetSystemMetrics(78)
            screen_h = ctypes.windll.user32.GetSystemMetrics(79)

            mx = px + 10
            my = py + 10
            popup_w = 220
            popup_h = 40
            if mx + popup_w > screen_left + screen_w:
                mx = screen_left + screen_w - popup_w - 10
            if my + popup_h > screen_top + screen_h:
                my = screen_top + screen_h - popup_h - 10
            if mx < screen_left:
                mx = screen_left + 10
            if my < screen_top:
                my = screen_top + 10

            result = await MKPopup.show(actions, mx, my, timeout=6.0)
            if result.get("selected"):
                _on_menu_choice(result["selected"])

        try:
            asyncio.run_coroutine_threadsafe(show_menu(), state.agent.loop)
        except Exception:
            pass
    
    print("[MK] 监控循环已退出")


async def process_clipboard(x, y, custom_prompt):
    state = get_state()
    panel = get_panel()
    input_text = custom_prompt
    if not input_text or not input_text.strip():
        return

    panel.status("处理中...")
    try:
        stream = state.agent.llm.chat_stream([
            {"role": "system", "content": "直接输出结果，不要解释。"},
            {"role": "user", "content": input_text[:3000]},
        ])

        if panel.result_text:
            try:
                panel.result_text.SetValue("")
            except:
                pass

        full_reply = []
        async for chunk in stream:
            if chunk:
                full_reply.append(chunk)
                if panel.result_text:
                    wx.CallAfter(lambda c=chunk: (
                        panel.result_text.AppendText(c),
                        panel.result_text.ShowPosition(panel.result_text.GetLastPosition())
                    ))

        reply = ''.join(full_reply)
        panel.status("完成")
        get_panel().set_processing(False)
    except Exception as e:
        panel.status("失败: " + str(e))
        get_panel().set_processing(False)


# ============================================================
# 安全的启动/停止函数
# ============================================================
def start_monitor():
    """安全启动监控线程（自动处理旧线程）"""
    state = get_state()
    
    with state._lock:
        # 如果已有线程在运行，先停止
        if state.monitor_running:
            print("[MK] 监控已在运行，先停止旧的")
            state.monitor_running = False
            
            if state.monitor_thread and state.monitor_thread.is_alive():
                state.monitor_thread.join(timeout=2)
                state.monitor_thread = None
        
        # 启动新线程
        state.monitor_running = True
        state.monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
        state.monitor_thread.start()
        print("[MK] 监控线程已启动")


def stop_monitor():
    """停止监控线程"""
    state = get_state()
    
    with state._lock:
        if not state.monitor_running:
            print("[MK] 监控已停止，无需重复操作")
            return
        
        state.monitor_running = False
        
        if state.monitor_thread and state.monitor_thread.is_alive():
            state.monitor_thread.join(timeout=2)
            state.monitor_thread = None
        
        print("[MK] 监控已停止")


# ============================================================
# 兼容旧接口
# ============================================================
def start_monitor_legacy():
    """兼容旧代码的启动函数"""
    start_monitor()