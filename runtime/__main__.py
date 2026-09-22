"""AICP 本地引擎 · 极简启动器"""
import asyncio
import logging
import os
import sys
import threading
from pathlib import Path
import subprocess as _subprocess
_original_run = _subprocess.run
def _utf8_run(*args, **kwargs):
    kwargs.setdefault('encoding', 'utf-8')
    kwargs.setdefault('errors', 'ignore')
    return _original_run(*args, **kwargs)
_subprocess.run = _utf8_run

_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from logging.handlers import RotatingFileHandler
from core import Envelop, Agent, route
from runtime._config import load_config
from runtime._loader import load_all_plugins, start_hot_reload_watcher
from runtime._aicp_llm import AICP_LLM

SERVER_MODE = "--server" in sys.argv
NO_GUI = "--no-gui" in sys.argv or SERVER_MODE


def setup_logging():
    log_dir = Path("data")
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("aicp")
    logger.setLevel(logging.DEBUG)

    fh = RotatingFileHandler(
        log_dir / "gateway.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    logger.addHandler(fh)

    if sys.stdout and hasattr(sys.stdout, 'write'):
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(ch)

    return logger


def _load_app_config(app_dir: Path) -> dict | None:
    app_yaml = app_dir / "app.yaml"
    if not app_yaml.exists():
        return {"name": app_dir.name, "auto_start": True, "init_action": "init"}
    try:
        import yaml
        with open(app_yaml, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            cfg.setdefault("name", app_dir.name)
            cfg.setdefault("auto_start", True)
            cfg.setdefault("init_action", "init")
            return cfg
    except Exception:
        return {"name": app_dir.name, "auto_start": True, "init_action": "init"}


async def main():

    sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
    sys.stderr.reconfigure(encoding='utf-8', errors='ignore')
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONUTF8'] = '1'
    logger = setup_logging()
    config = load_config()

    port = config.get("port", 9000)
    host = config.get("host", "127.0.0.1")

    logger.info("=" * 50)
    logger.info(f"  AICP Engine · http://{host}:{port}")
    if SERVER_MODE:
        logger.info("  Mode: Server (no GUI)")
    logger.info("=" * 50)

    if not SERVER_MODE:
        from runtime.wx_utils import init as init_wx
        init_wx()

    # LLM
    llm = None
    aicp_llm = None
    if config.get("models", {}).get("providers"):
        try:
            from runtime._llm import LLM
            llm = LLM(config)
            logger.info(f"LLM: {llm.default_model}")
            aicp_llm = AICP_LLM(config, is_remote=True, llm=llm)   # ← 传入
            logger.info("AICP_LLM: chatEnvelop ready")
        except Exception as e:
            logger.warning(f"LLM init failed: {e}")

    stop_event = asyncio.Event()

    def shutdown():
        if not stop_event.is_set():
            logger.info("Shutting down...")
            stop_event.set()

    loop = asyncio.get_event_loop()

    # System
    if SERVER_MODE:
        from runtime._system import SystemServer
        system = SystemServer(loop, route, config=config)
    else:
        from runtime._system import System
        system = System(loop, route, config=config, on_shutdown=shutdown)

    # Agent
    agent = Agent()
    agent.config = config
    agent.loop = loop
    agent.log = logger
    agent.data_dir = Path("data")
    agent.base_url = f"http://127.0.0.1:{port}"
    agent.llm = llm
    agent.aicp_llm = aicp_llm
    agent.chatEnvelop = aicp_llm.chatEnvelop if aicp_llm else None
    agent.Envelop = Envelop
    agent.system = system
    system.bind(agent)

    # ---------- 启动可视化 worker ----------
    if getattr(system, '_viz_enabled', False):
        await system.start_visualizer()
        logger.info(f"Visualizer: enabled on channel {system._viz_channel}")
    else:
        logger.debug("Visualizer: disabled")

    import core
    count = load_all_plugins()
    logger.info(f"Plugins: {count} loaded")
    for name in sorted(core.plugins.keys()):
        logger.debug(f"  /api/{name}")

    asyncio.create_task(start_hot_reload_watcher(agent=agent))
    logger.info("Restoring cron tasks...")
    cron_plugin = core.plugins.get("os/_cron")
    if cron_plugin:
        try:
            cron_env = Envelop(
                sender="__main__",
                receiver="os/_cron",
                payload={"action": "restore"}
            )
            cron_result = await cron_plugin(cron_env, agent)
            if cron_result and cron_result.payload.get("ok"):
                restored_count = cron_result.payload.get("restored", 0)
                if restored_count > 0:
                    logger.info(f"  ✅ 恢复 {restored_count} 个定时任务")
                else:
                    logger.info("  无定时任务需要恢复")
            else:
                logger.warning("  ⚠️ 定时任务恢复失败")
        except Exception as e:
            logger.warning(f"  ❌ 定时任务恢复异常: {e}")
    else:
        logger.warning("  ⚠️ os/_cron 插件未找到")
    # HTTP
    gateway = core.plugins.get("os/_gateway")
    if not gateway:
        logger.error("os/_gateway not found!")
        return

    logger.info("Starting os/_gateway...")
    env = Envelop(
        sender="__main__",
        receiver="os/_gateway",
        intent="START",
        payload={"port": port, "host": host},
    )

    try:
        result = await gateway(env, agent)
        if result and result.payload.get("status") == "listening":
            logger.info(f"Gateway listening on http://{host}:{port}")
        else:
            logger.error("Gateway start failed")
            return
    except Exception as e:
        logger.error(f"Gateway start failed: {e}")
        return

    # WebSocket
    ws_port = port + 1
    ws_runner = None
    try:
        from plugins.os._websocket import start_websocket_server
        ws_runner = await start_websocket_server(agent, host=host, port=ws_port)
        logger.info(f"WebSocket listening on ws://{host}:{ws_port}/ws")
    except Exception as e:
        logger.warning(f"WebSocket start failed: {e}")

    # FileReceiver
    file_port = port + 2
    file_runner = None
    try:
        from plugins.os._file_receiver import start_file_receiver
        file_runner = await start_file_receiver(agent, host=host, port=file_port)
        logger.info(f"FileReceiver listening on http://{host}:{file_port}/upload")
    except Exception as e:
        logger.warning(f"FileReceiver start failed: {e}")

    # ================================================================
    # Applications — 先启动，让 registry 初始化
    # ================================================================
    logger.info("Starting applications...")
    for scan_dir in ["plugins/builtins", "plugins/applications"]:
        apps_dir = Path(scan_dir)
        if not apps_dir.exists():
            continue
        for app_dir in sorted(apps_dir.iterdir()):
            if not app_dir.is_dir():
                continue
            if app_dir.name.startswith(".") or app_dir.name.startswith("_"):
                continue
            if SERVER_MODE and app_dir.name in ("mk", "studio", "shell", "screen_probe", "newbula_plus"):
                continue

            init_file = app_dir / "_init.py"
            if not init_file.exists():
                continue

            app_config = _load_app_config(app_dir)
            if not app_config.get("auto_start"):
                continue

            app_route = f"{scan_dir.replace('plugins/', '')}/{app_dir.name}/_init"
            init_plugin = core.plugins.get(app_route)
            if not init_plugin:
                # logger.warning(f"  {app_config.get('name', app_dir.name)}: _init not found")
                continue

            init_env = Envelop(
                sender="__main__",
                receiver=app_route,
                payload={"action": app_config.get("init_action", "init")},
            )
            try:
                await init_plugin(init_env, agent)
                logger.info(f"  ✅ {app_config.get('name', app_dir.name)}")
            except Exception as e:
                logger.warning(f"  ❌ {app_config.get('name', app_dir.name)}: {e}")



    # GUI
    if not NO_GUI:
        system.start_gui()
        logger.info("GUI: ready")

    def listen_stdin():
        while not stop_event.is_set():
            try:
                line = sys.stdin.readline()
                if line.strip().lower() in ("quit", "exit", "q"):
                    shutdown()
            except EOFError:
                break

    threading.Thread(target=listen_stdin, daemon=True).start()

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        shutdown()

    logger.info("Stopping services...")

    stop_env = Envelop(
        sender="__main__",
        receiver="os/_gateway",
        intent="STOP",
        payload={"action": "STOP"},
    )
    try:
        await gateway(stop_env, agent)
    except Exception:
        pass

    if ws_runner:
        try:
            if hasattr(agent, 'ws_gateway'):
                gw = agent.ws_gateway
                for channel_id in list(gw._channels.keys()):
                    for ws in list(gw._channels.get(channel_id, set())):
                        try:
                            await ws.close(code=1001, message=b"Server shutting down")
                        except Exception:
                            pass
                logger.info("WebSocket connections closed")
            await ws_runner.cleanup()
        except Exception as e:
            logger.warning(f"WebSocket cleanup failed: {e}")

    if file_runner:
        try:
            await file_runner.cleanup()
            logger.info("FileReceiver stopped")
        except Exception as e:
            logger.warning(f"FileReceiver cleanup failed: {e}")

    pool = core.plugins.get("os/_pool")
    if pool:
        try:
            pool_env = Envelop(
                sender="__main__",
                receiver="os/_pool",
                payload={"action": "_cleanup"},
            )
            await pool(pool_env, agent)
        except Exception:
            pass

    if not SERVER_MODE:
        try:
            from runtime.wx_utils import shutdown as shutdown_wx
            shutdown_wx()
        except Exception:
            pass

    logger.info("Goodbye")


if __name__ == "__main__":
    asyncio.run(main())