"""动作执行器 — 执行 vision_agent 返回的操作指令"""
import time
import json
import pyautogui
import pyperclip

# 动作执行函数映射
EXECUTORS = {}

def register(action_type):
    """装饰器：注册动作执行函数"""
    def decorator(func):
        EXECUTORS[action_type] = func
        return func
    return decorator

@register("click")
def exec_click(action, region):
    x = action["x"] + region.get("x", 0)
    y = action["y"] + region.get("y", 0)
    pyautogui.click(x, y)
    time.sleep(0.3)
    return {"ok": True, "detail": f"点击 ({x}, {y})"}

@register("type")
def exec_type(action, region):
    text = action.get("text", "")
    pyperclip.copy(text)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.2)
    # 输入完自动回车
    if not action.get("no_enter"):
        pyautogui.press('enter')
        time.sleep(0.3)
    return {"ok": True, "detail": f"输入: {text[:50]}"}

@register("hotkey")
def exec_hotkey(action, region):
    keys = action.get("keys", [])
    pyautogui.hotkey(*keys)
    time.sleep(0.2)
    return {"ok": True, "detail": f"按键: {keys}"}

@register("scroll")
def exec_scroll(action, region):
    x = action["x"] + region.get("x", 0)
    y = action["y"] + region.get("y", 0)
    delta = action.get("delta", 0)
    pyautogui.moveTo(x, y)
    pyautogui.scroll(delta)
    time.sleep(0.2)
    return {"ok": True, "detail": f"滚动 ({x}, {y}) delta={delta}"}

@register("wait")
def exec_wait(action, region):
    seconds = action.get("seconds", 1)
    time.sleep(seconds)
    return {"ok": True, "detail": f"等待 {seconds}s"}

@register("screenshot")
def exec_screenshot(action, region):
    return {"ok": True, "detail": "等待截图", "need_screenshot": True}

@register("excel")
def exec_excel(action, region):
    # 调 Eater 生成的 Excel 插件
    import core
    import asyncio
    loop = asyncio.get_event_loop()
    result = loop.run_until_complete(
        core.call_plugin("lib/excel_formatter", action)
    )
    return {"ok": True, "detail": f"Excel 操作完成"}

@register("code")
def exec_code(action, region):
    # 调 Eater 生成的代码插件
    import core
    import asyncio
    loop = asyncio.get_event_loop()
    result = loop.run_until_complete(
        core.call_plugin("lib/code_formatter", action)
    )
    return {"ok": True, "detail": f"代码操作完成"}


async def execute(envelop, agent):
    """
    输入 payload:
      actions: 操作指令列表
      region: 取景框区域 {x, y, w, h}（用于坐标换算）
    
    输出 payload:
      results: 执行结果列表
      need_screenshot: 是否需要截图确认
    """
    actions = envelop.payload.get("actions", [])
    region = envelop.payload.get("region", {"x": 0, "y": 0})

    results = []
    need_screenshot = False

    for i, action in enumerate(actions):
        action_type = action.get("type", "")
        executor = EXECUTORS.get(action_type)

        if executor:
            try:
                result = executor(action, region)
                results.append(result)
                if result.get("need_screenshot"):
                    need_screenshot = True
            except Exception as e:
                results.append({"ok": False, "error": str(e), "action": action})
        else:
            results.append({"ok": False, "error": f"未知操作类型: {action_type}"})

    envelop.payload = {
        "results": results,
        "need_screenshot": need_screenshot,
        "all_ok": all(r.get("ok", False) for r in results)
    }
    return envelop