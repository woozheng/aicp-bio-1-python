"""Vision Helper — 使用配置向导手动定位 + 模拟键鼠激活页面"""
import asyncio
import time
import pyautogui
import pyperclip
import json
from pathlib import Path


async def find_input_region(agent, hwnd, platform_name):
    """
    使用配置向导手动框选的坐标，模拟键鼠激活页面。
    不再使用Vision Agent截图分析。
    """
    try:
        print(f"[VisionHelper] {platform_name} - 等待页面加载...")
        await asyncio.sleep(5)

        config_file = Path(__file__).parent / "config.json"
        input_click = None
        input_region = None

        if config_file.exists():
            try:
                config = json.loads(config_file.read_text(encoding="utf-8"))
                for p in config.get("platforms", []):
                    if p.get("name") == platform_name:
                        input_click = p.get("input_click")
                        input_region = p.get("input_region")
                        break
            except:
                pass

        if input_click:
            print(f"[VisionHelper] {platform_name} - 点击输入框位置: ({input_click['x']}, {input_click['y']})")
            pyautogui.click(input_click["x"], input_click["y"])
            await asyncio.sleep(0.3)
            pyperclip.copy("hello")
            pyautogui.hotkey('ctrl', 'v')
            await asyncio.sleep(0.3)
            pyautogui.press("enter")
            print(f"[VisionHelper] {platform_name} - hello已发送")
        else:
            print(f"[VisionHelper] {platform_name} - 没有点击坐标，跳过激活")

        await asyncio.sleep(3)

        if input_region:
            result = {
                "x1": input_region["x1"],
                "y1": input_region["y1"]-10 ,
                "x2": input_region["x2"],
                "y2": input_region["y2"]+50
            }
            print(f"[VisionHelper] {platform_name} - 手动框选区域(上扩60): {result}")
            return result

        print(f"[VisionHelper] {platform_name} - 无手动框选，使用默认值")
        return {"x1": 400, "y1": 440, "x2": 800, "y2": 600}

    except Exception as e:
        print(f"[VisionHelper] find_input_region error: {e}")
        import traceback
        traceback.print_exc()
        return {"x1": 400, "y1": 440, "x2": 800, "y2": 600}