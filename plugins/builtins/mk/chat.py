"""MK 自动聊天 — 视觉模式 + 画面变化检测 + LLM 回复循环"""
import asyncio
import time
import cv2
import numpy as np
import base64
import io
from PIL import Image
from .state import get_state
from .panel import get_panel


def _screenshot_to_base64(region, max_size=500):
    """截图并缩放，长边不超过 max_size"""
    import pyautogui
    screenshot = pyautogui.screenshot(
        region=(region["x"], region["y"], region["w"], region["h"])
    )
    w, h = screenshot.width, screenshot.height
    if max(w, h) > max_size:
        scale = max_size / max(w, h)
        w, h = int(w * scale), int(h * scale)
        screenshot = screenshot.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    screenshot.save(buf, format='JPEG', quality=50)
    return base64.b64encode(buf.getvalue()).decode()


def _images_similar(b64_1: str, b64_2: str, threshold: float = 0.95) -> bool:
    """对比两张 base64 图片，相似度 > threshold 返回 True"""
    try:
        img1 = Image.open(io.BytesIO(base64.b64decode(b64_1)))
        img2 = Image.open(io.BytesIO(base64.b64decode(b64_2)))
        gray1 = cv2.cvtColor(np.array(img1), cv2.COLOR_RGB2GRAY)
        gray2 = cv2.cvtColor(np.array(img2), cv2.COLOR_RGB2GRAY)
        score = cv2.matchTemplate(gray1, gray2, cv2.TM_CCOEFF_NORMED)[0][0]
        return score > threshold
    except:
        return False


def _safe_append_text(panel, text):
    """在 wx 主线程追加文本"""
    import wx
    def append():
        if panel.result_text:
            try:
                panel.result_text.AppendText(text)
                panel.result_text.ShowPosition(panel.result_text.GetLastPosition())
            except Exception:
                pass
    wx.CallAfter(append)


def _safe_clear_text(panel):
    """在 wx 主线程清空文本"""
    import wx
    def clear():
        if panel.result_text:
            try:
                panel.result_text.SetValue("")
            except Exception:
                pass
    wx.CallAfter(clear)


async def start_chat(agent, personality: str):
    state = get_state()
    panel = get_panel()
    r = state.chat_region
    if not r:
        panel.status("请先框选聊天区域")
        state.chat_running = False
        panel.set_processing(False)
        return

    state.chat_running = True
    state.chat_memory = []

    _safe_append_text(panel, "🤖 自动聊天已启动，监控中...\n")
    panel.status("自动聊天已启动")

    last_img = _screenshot_to_base64(r)
    await asyncio.sleep(2)

    try:
        while state.chat_running:
            img_b64 = _screenshot_to_base64(r)

            if last_img and _images_similar(last_img, img_b64):
                panel.status("画面无变化")
                await asyncio.sleep(2)
                continue

            last_img = img_b64
            panel.status("截图已发送，等待回复...")

            reply = None
            try:
                reply = await agent.llm.chat(
                    [
                        {
                            "role": "system",
                            "content": personality + "\n替用户自动回复。如果聊天没有新消息或不需要回复，回复 SKIP。需要回复时直接返回回复文字，不要引号，不要解释。",
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/jpeg;base64," + img_b64
                                    },
                                }
                            ],
                        },
                    ],
                    model="nebula",
                )
            except Exception as e:
                panel.status("异常: " + str(e))
                await asyncio.sleep(5)
                continue

            if reply is None:
                panel.status("无响应")
                await asyncio.sleep(3)
                continue

            reply = reply.strip().strip('"').strip("'")

            if reply == "SKIP" or not reply or len(reply) < 2 or reply.startswith("[Nebula"):
                panel.status("跳过回复")
                await asyncio.sleep(2)
                continue

            state.chat_memory.append(reply)
            if len(state.chat_memory) > 20:
                state.chat_memory = state.chat_memory[-20:]

            _safe_append_text(panel, f"📤 {reply}\n")
            panel.output_reply(reply)
            panel.status("已回复: " + reply[:30] + "...")

            await asyncio.sleep(2)
            last_img = _screenshot_to_base64(r)
            await asyncio.sleep(2)

    except Exception as e:
        print(f"[chat] Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print(f"[chat] start_chat END, chat_running={state.chat_running}")
        state.chat_running = False
        state.chat_memory = []
        panel.set_processing(False)
        panel.status("聊天已停止")
        _safe_append_text(panel, "⏹ 自动聊天已停止\n")


def stop_chat():
    get_state().chat_running = False