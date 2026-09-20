"""文件接收插件 — 端口 = gateway + 2，接收 multipart 上传
兼容：拆片机（只传 file）和标准 AICP（file + session_id + callback）
"""
import os
import uuid
from pathlib import Path
from aiohttp import web
import core

UPLOAD_DIR = str(Path.home() / "VidSurg" / "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


class FileReceiver:
    def __init__(self):
        self.upload_dir = UPLOAD_DIR

    def get_status(self):
        total_size = 0
        file_count = 0
        if os.path.exists(self.upload_dir):
            for root, dirs, files in os.walk(self.upload_dir):
                file_count += len(files)
                for f in files:
                    try:
                        total_size += os.path.getsize(os.path.join(root, f))
                    except:
                        pass
        return {
            "upload_dir": self.upload_dir,
            "file_count": file_count,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
        }


async def execute(envelop, agent):
    """供其他插件通过 agent.system.call 调用"""
    receiver = getattr(agent, 'file_receiver', None)
    action = envelop.payload.get("action", "status")

    if action == "status":
        envelop.payload = receiver.get_status() if receiver else {"error": "not started"}
        return envelop

    elif action == "get_file":
        file_name = envelop.payload.get("file_name", "")
        if not file_name:
            envelop.payload = {"error": "file_name required"}
            return envelop
        file_path = os.path.join(UPLOAD_DIR, file_name)
        if not os.path.exists(file_path):
            envelop.payload = {"error": "file not found"}
            return envelop
        envelop.payload = {
            "ok": True,
            "file_path": file_path,
            "file_name": file_name,
            "file_size": os.path.getsize(file_path),
            "file_size_mb": round(os.path.getsize(file_path) / (1024 * 1024), 2),
        }
        return envelop

    else:
        envelop.payload = {"error": f"Unknown action: {action}"}
        return envelop


async def handle_upload(request):
    """处理 multipart 文件上传，兼容拆片机（无 session_id）和你的前端（带 session_id）"""
    reader = await request.multipart()
    file_path = None
    file_name = "unknown"
    session_id = None
    callback = None

    while True:
        part = await reader.next()
        if part is None:
            break

        if part.name == "file":
            file_name = part.filename or "upload.bin"
            safe_name = "".join(c for c in file_name if c.isalnum() or c in "._- ()") or "upload.bin"

            # 如果传了 session_id，用 session_id 作为子目录名
            if session_id:
                task_dir = os.path.join(UPLOAD_DIR, session_id[:8])
            else:
                task_dir = os.path.join(UPLOAD_DIR, str(uuid.uuid4())[:8])

            os.makedirs(task_dir, exist_ok=True)
            file_path = os.path.join(task_dir, safe_name)

            with open(file_path, "wb") as f:
                while True:
                    chunk = await part.read_chunk(8192)
                    if not chunk:
                        break
                    f.write(chunk)

        elif part.name == "session_id":
            session_id = (await part.text()).strip()

        elif part.name == "callback":
            callback = (await part.text()).strip()

    if not file_path or not os.path.exists(file_path):
        return web.json_response({"success": False, "error": "未收到文件"}, status=400)

    file_size = os.path.getsize(file_path)

    # ★ 如果有 callback，通知回调地址
    if callback:
        try:
            # 构造回调消息
            callback_payload = {
                "action": "file_uploaded",
                "file": {
                    "path": file_path,
                    "name": os.path.basename(file_path),
                    "size": file_size
                },
                "session_id": session_id or "default"
            }
            # 通过 agent.system.call 发送回调
            agent = request.app.get("agent")
            if agent:
                await agent.system.call(core.Envelop(
                    sender="os/_file_receiver",
                    receiver=callback,
                    payload=callback_payload,
                    meta={"session_id": session_id or "default"}
                ))
                print(f"[_file_receiver] callback 已发送到: {callback}")
        except Exception as e:
            print(f"[_file_receiver] callback 失败: {e}")

    return web.json_response({
    "success": True,
    "ok": True,           # ★ 加上这个
    "file_path": file_path,
    "file_name": os.path.basename(file_path),
    "file_size": file_size,
    "file_size_mb": round(file_size / (1024 * 1024), 2),
    "session_id": session_id,
})


async def start_file_receiver(agent, host="0.0.0.0", port=9002):
    """启动文件接收服务，挂到 agent 上"""
    receiver = FileReceiver()
    agent.file_receiver = receiver
    agent.file_receiver_port = port

    app = web.Application()
    app["agent"] = agent  # ★ 挂 agent 到 app，供 handle_upload 使用

    @web.middleware
    async def cors(request, handler):
        if request.method == "OPTIONS":
            return web.Response(status=200, headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            })
        resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    app.middlewares.append(cors)
    app.router.add_post("/upload", handle_upload)
    app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()

    return runner