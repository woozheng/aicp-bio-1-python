"""知识库 — 注册菜单入口"""
import core
import webbrowser
from pathlib import Path

PROJECT = Path(__file__).parent.name


async def execute(envelop, agent):
    action = envelop.payload.get("action", "init")

    if action == "init":
        await agent.system.call(core.Envelop(
            sender=f"builtins/{PROJECT}/_init",
            receiver="shell/_tray",
            payload={
                "action": "add_menu",
                "app_id": PROJECT,
                "label": "📚 知识库",
                "items": [
                    {"label": "📚 知识库", "action": "open_web", "url": f"/{PROJECT}/index.html", "receiver": f"builtins/{PROJECT}/_init"},
                    {"label": "📊 统计", "action": "stats", "receiver": f"builtins/{PROJECT}/_init"},
                ]
            }
        ))
        envelop.payload = {"ok": True}
        return envelop

    elif action == "open_web":
        webbrowser.open(f"{agent.base_url}/{PROJECT}/index.html")
        return envelop

    elif action == "stats":
        stats = await agent.system.call(core.Envelop(
            sender=f"builtins/{PROJECT}/_init",
            receiver=f"builtins/{PROJECT}/main",
            payload={"action": "stats"}
        ))
        envelop.payload = stats.payload if stats else {"ok": False, "error": "无响应"}
        return envelop

    envelop.payload = {"error": f"未知操作: {action}"}
    return envelop