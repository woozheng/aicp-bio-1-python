"""MK 工具扫描 + 工具菜单"""
from pathlib import Path
from urllib.parse import quote
import asyncio
from .state import get_state


def scan_tools() -> list:
    tools = []
    www = Path("www")
    if www.exists():
        for f in sorted(www.rglob("*.html")):
            if f.name == "index.html":
                continue
            name = f.stem.replace("-", " ").replace("_", " ").title()
            tools.append({"label": name, "url": str(f.relative_to(www)).replace("\\", "/")})
    return tools


def show_tools_menu(agent):
    state = get_state()
    tools = scan_tools()
    
    actions = [{"label": "🏠 Index", "value": "__index__"}]
    for t in tools:
        actions.append({"label": f"  {t['label']}", "value": t['url']})
    actions.append({"label": "  ❌ 关闭", "value": "__close__"})

    px, py = state.select_pos if state.select_pos else (100, 100)

    def on_choice(label):
        if not label or "__close__" in str(label):
            return
        if "__index__" in str(label):
            import webbrowser
            webbrowser.open(f"{state.base_url}/")
            return
        for t in tools:
            if t['url'] == label:
                import webbrowser
                webbrowser.open(f"{state.base_url}/{t['url']}?text={quote(state.selected_text)}")
                break

    async def show():
        from .popup import MKPopup
        result = await MKPopup.show(actions, px + 30, py, timeout=8.0)
        if result.get("selected"):
            on_choice(result["selected"])

    try:
        asyncio.run_coroutine_threadsafe(show(), state.agent.loop)
    except Exception:
        pass