"""数字探头 — 菜单注册"""
import core


async def execute(envelop, agent):
    payload = envelop.payload.get("payload", envelop.payload)
    action = payload.get("action", "init")

    if action == "init":
        await agent.system.call(core.Envelop(
            sender="builtins/screen_probe/_init",
            receiver="shell/_tray",
            payload={
                "action": "add_menu",
                "app_id": "screen_probe",
                "label": "📊 数字探头",
                "items": [
                    {"label": "📱 监控台", "action": "open_web", "receiver": "builtins/screen_probe/_init"},
                    {"label": "➕ 新建探头", "action": "create_probe", "receiver": "builtins/screen_probe/routes"},
                    {"label": "📋 探头列表", "action": "list_probes", "receiver": "builtins/screen_probe/routes"},
                    {"label": "🛑 停止所有", "action": "stop_all", "receiver": "builtins/screen_probe/routes"},
                    {"label": "▶️ 启动所有", "action": "start_all", "receiver": "builtins/screen_probe/routes"},
                    {"label": "🗑️ 清理数据", "action": "clear_all", "receiver": "builtins/screen_probe/routes"},
                ],
            },
        ))
        envelop.payload = {"ok": True}
        return envelop

    elif action == "open_web":
        import webbrowser
        webbrowser.open(f"{agent.base_url}/screen_probe/index.html")
        envelop.payload = {"ok": True}
        return envelop

    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop