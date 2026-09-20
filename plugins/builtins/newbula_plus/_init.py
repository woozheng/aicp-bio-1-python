# plugins/builtins/newbula_plus/_init.py
import core
from pathlib import Path

PROJECT = Path(__file__).parent.name

async def execute(envelop, agent):
    payload = envelop.payload.get("payload", envelop.payload)
    action = payload.get("action", "init")

    if action == "init":
        await agent.system.call(core.Envelop(
            sender=f"builtins/{PROJECT}/_init",
            receiver="shell/_tray",
            payload={
                "action": "add_menu",
                "app_id": PROJECT,
                "label": "🤖 Newbula Plus",
                "items": [
                    {"label": "▶ 启动集群", "action": "start", "receiver": f"builtins/{PROJECT}/_init"},
                    {"label": "⏹ 停止集群", "action": "stop", "receiver": f"builtins/{PROJECT}/_init"},
                    {"label": "📊 集群状态", "action": "status", "receiver": f"builtins/{PROJECT}/_init"},
                    {"label": "-"},
                    {"label": "🔍 重新吞噬窗口", "action": "redevour", "receiver": f"builtins/{PROJECT}/_init"},
                    {"label": "-"},
                    {"label": "⚙️ 平台配置", "action": "open_config", "receiver": f"builtins/{PROJECT}/_init"},
                    {"label": "🌐 打开面板", "action": "open_web", "url": f"/{PROJECT}/index.html", "receiver": f"builtins/{PROJECT}/_init"},
                ],
            },
        ))
        envelop.payload = {"ok": True}
        return envelop

    elif action == "start":
        result = await agent.system.call(core.Envelop(
            sender=f"builtins/{PROJECT}/_init",
            receiver=f"builtins/{PROJECT}/engine",
            payload={"action": "start"}
        ))
        envelop.payload = result.payload if result else {"error": "无响应"}
        return envelop

    elif action == "stop":
        result = await agent.system.call(core.Envelop(
            sender=f"builtins/{PROJECT}/_init",
            receiver=f"builtins/{PROJECT}/engine",
            payload={"action": "stop"}
        ))
        envelop.payload = result.payload if result else {"error": "无响应"}
        return envelop

    elif action == "status":
        result = await agent.system.call(core.Envelop(
            sender=f"builtins/{PROJECT}/_init",
            receiver=f"builtins/{PROJECT}/engine",
            payload={"action": "status"}
        ))
        envelop.payload = result.payload if result else {"error": "无响应"}
        return envelop

    # 🔥 新增：重新吞噬窗口
    elif action == "redevour":
        result = await agent.system.call(core.Envelop(
            sender=f"builtins/{PROJECT}/_init",
            receiver=f"builtins/{PROJECT}/engine",
            payload={"action": "redevour"}
        ))
        envelop.payload = result.payload if result else {"error": "无响应"}
        return envelop

    elif action == "open_config":
        from .config_wizard import show_config_wizard
        show_config_wizard(agent)
        envelop.payload = {"ok": True}
        return envelop

    elif action == "open_web":
        import webbrowser
        url = payload.get("url", f"/{PROJECT}/index.html")
        webbrowser.open(f"{agent.base_url}{url}")
        return None

    else:
        envelop.payload = {"error": f"Unknown action: {action}"}
        return envelop