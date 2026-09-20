"""HTTP 入口插件 — 纯网关，只做协议转换和路由转发

v3.0: 移除中间件架构，回归纯转发
"""
from aiohttp import web
from inspect import isasyncgen
import core
import json
import asyncio


async def execute(envelop, agent):
    action = envelop.payload.get("action", "START")
    
    if action == "START":
        port = envelop.payload.get("port", 9000)
        host = envelop.payload.get("host", "127.0.0.1")
        
        app = web.Application(client_max_size=50 * 1024 * 1024)
        
        # ============================================================
        # CORS 中间件
        # ============================================================
        @web.middleware
        async def cors(request, handler):
            if request.method == "OPTIONS":
                return web.Response(status=200, headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, X-AICP-Token, Authorization",
                })
            resp = await handler(request)
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        
        app.middlewares.append(cors)
        
        
        # ============================================================
        # 工具函数
        # ============================================================
        def _extract_token(request):
            token = request.headers.get("X-AICP-Token", "")
            if not token:
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
            if not token:
                token = request.cookies.get("aicp_token", "")
            return token
        
        def _is_local(request):
            forwarded_for = request.headers.get("X-Forwarded-For", "")
            if forwarded_for:
                return False
            return request.remote in ('127.0.0.1', 'localhost', '::1')
        
        def _is_same_origin(request):
            origin = request.headers.get("Origin", "")
            if not origin:
                return False
            server_host = request.host
            return origin == f"http://{server_host}" or origin == f"https://{server_host}"
        
        async def _verify_auth(request, agent, route, envelop_meta=None):
            token = _extract_token(request)
            
            meta = {
                "token": token,
                "authorization": request.headers.get("Authorization", ""),
                "cookie_token": request.cookies.get("aicp_token", ""),
            }
            if envelop_meta:
                meta.update(envelop_meta)
            
            auth_env = core.Envelop(
                sender="os/_gateway",
                receiver="os/_auth",
                payload={"action": "verify", "route": route},
                meta=meta,
            )
            auth_result = await agent.system.call(auth_env)
            
            if auth_result and auth_result.payload.get("ok"):
                return True, auth_result.payload
            return False, None
        
        # ============================================================
        # handle_v1_chat — OpenAI 兼容端点
        # ============================================================
        async def handle_v1_chat(request):
            """处理 /v1/chat/completions 请求，转发给 Newbula Plus"""
            if request.method != "POST":
                return web.json_response({"error": "Method not allowed"}, status=405)

            try:
                body = await request.json()
            except:
                return web.json_response({"error": "Invalid JSON"}, status=400)

            # ★ 判断是否流式
            stream = body.get("stream", False)

            env = core.Envelop(
                sender="os/_gateway",
                receiver="builtins/newbula_plus/engine",
                payload={
                    "action": "chat_completions",
                    "model": body.get("model", "newbula-plus"),
                    "messages": body.get("messages", []),
                    "stream": stream,
                }
            )
            result = await agent.system.call(env)

            if result is None:
                return web.json_response({"error": "no response"}, status=500)

            # ★ 如果是流式，返回 SSE
            if stream:
                resp = web.StreamResponse()
                resp.headers['Content-Type'] = 'text/event-stream'
                resp.headers['Cache-Control'] = 'no-cache'
                await resp.prepare(request)

                async for chunk in result.payload:
                    if isinstance(chunk, str):
                        await resp.write(chunk.encode())
                    elif isinstance(chunk, bytes):
                        await resp.write(chunk)
                    else:
                        await resp.write(str(chunk).encode())

                return resp

            # ★ 非流式：直接返回 JSON
            return web.json_response(result.payload)
        
        # ============================================================
        # handle_api — POST /api/{path}
        # ============================================================
        async def handle_api(request):
            path = request.match_info["path"]

            # ★★★ 修复：在函数开头定义 raw_body 默认值，防止 PDF 上传时 UnboundLocalError ★★★
            raw_body = b""

            # WebSocket 配置（特殊端点，不认证）
            if path == "ws_config":
                ws_config = agent.config.get("websocket", {})
                ws_external_url = ws_config.get("external_url", "")
                ws_port = agent.config.get("port", 9000) + 1

                is_secure = request.headers.get("X-Forwarded-Proto", request.scheme) == "https"
                hostname = request.host.split(':')[0]

                if ws_external_url:
                    url = ws_external_url
                else:
                    protocol = "wss" if is_secure else "ws"
                    url = f"{protocol}://{hostname}/ws"

                return web.json_response({
                    "url": url,
                    "port": ws_port,
                    "secure": is_secure
                })

            if path == "auth/verify":
                ok, _ = await _verify_auth(request, agent, "auth/verify")
                if ok:
                    return web.json_response({"ok": True})
                else:
                    return web.json_response({"ok": False}, status=401)

            # 读取请求体（限制 10MB）
            MAX_BODY_SIZE = 10 * 1024 * 1024
            body = {}

            # ★★★ 调试：打印请求头和 Content-Length ★★★
            content_length = request.headers.get("Content-Length", "unknown")
            content_type = request.headers.get("Content-Type", "unknown")
            #print(f"[GATEWAY] ====== 开始处理请求 ======")
            #print(f"[GATEWAY] path: {path}")
            #print(f"[GATEWAY] Content-Length: {content_length}")
            #print(f"[GATEWAY] Content-Type: {content_type}")
            #print(f"[GATEWAY] method: {request.method}")

            try:
                raw_body = await request.read()
                #print(f"[GATEWAY] request.read() 成功，读取了 {len(raw_body)} 字节")

                if len(raw_body) > MAX_BODY_SIZE:
                    #print(f"[GATEWAY] ❌ 请求体过大: {len(raw_body)} > {MAX_BODY_SIZE}")
                    return web.json_response({"error": f"Request body too large: {len(raw_body)} > {MAX_BODY_SIZE}"}, status=413)

                if raw_body:
                    # ★★★ 检查原始数据的前几个字符，判断是否是 JSON ★★★
                    preview = raw_body[:100]
                    #print(f"[GATEWAY] 原始数据前100字符: {preview}")
                    
                    # ★★★ 尝试多种解析方式 ★★★
                    try:
                        # 方式1：直接 UTF-8 解码
                        raw_text = raw_body.decode('utf-8')
                        body = json.loads(raw_text)
                        #print(f"[GATEWAY] ✅ UTF-8 JSON 解析成功，keys: {list(body.keys())}")
                    except UnicodeDecodeError as ude:
                        #print(f"[GATEWAY] ⚠️ UTF-8 解码失败: {ude}")
                        # 方式2：忽略错误解码
                        raw_text = raw_body.decode('utf-8', errors='ignore')
                        try:
                            body = json.loads(raw_text)
                            #print(f"[GATEWAY] ✅ ignore 模式 JSON 解析成功，keys: {list(body.keys())}")
                        except json.JSONDecodeError as jde:
                            #print(f"[GATEWAY] ❌ JSON 解析失败: {jde}")
                            #print(f"[GATEWAY] 尝试解析的文本前500字符: {raw_text[:500]}")
                            body = {}
                    except json.JSONDecodeError as jde:
                        #print(f"[GATEWAY] ❌ JSON 解析失败: {jde}")
                        #print(f"[GATEWAY] 尝试解析的文本前500字符: {raw_body[:500]}")
                        body = {}

            except asyncio.TimeoutError:
                #print(f"[GATEWAY] ❌ request.read() 超时")
                body = {}
            except Exception as e:
                #print(f"[GATEWAY] ❌ request.read() 异常: {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()
                body = {}

            #print(f"[GATEWAY] body 最终类型: {type(body)}, 内容: {str(body)[:200]}...")
            #print(f"[GATEWAY] ====== 处理完成 ======")

            if not isinstance(body, dict):
                body = {"data": body}

            # 提取 body 中的 meta
            body_meta = body.get("meta", {}) if isinstance(body.get("meta"), dict) else {}

            # 认证判断
            need_auth = not (_is_local(request) or _is_same_origin(request))
            #print(f"[GATEWAY DEBUG] method={request.method}, path={path}, remote={request.remote}, need_auth={need_auth}, token={_extract_token(request)[:10]}")

            auth_payload = None

            if need_auth:
                ok, auth_payload = await _verify_auth(request, agent, path, body_meta)
                if not ok:
                    return web.json_response({"error": "Unauthorized"}, status=401)

            # 构造 Envelop
            body_payload = body.get("payload", body)
            if not isinstance(body_payload, dict):
                body_payload = {"data": body_payload}

            env_meta = {
                **body_meta,
                "token": _extract_token(request),
                "method": request.method,
                "path": path,
                "headers": {k: v for k, v in request.headers.items()},
                "raw_body": raw_body.decode('utf-8', errors='ignore') if raw_body else "",
            }

            if auth_payload:
                env_meta["auth_result"] = auth_payload

            env = core.Envelop(
                sender="os/_gateway",
                receiver=path,
                intent=body.get("intent", "API_CALL") if isinstance(body.get("intent"), str) else "API_CALL",
                payload=body_payload,
                meta=env_meta,
            )

            result = await agent.system.call(env)

            if result is None:
                return web.json_response({"error": "no response"}, status=500)

            # 流式响应
            if isasyncgen(result.payload):
                resp = web.StreamResponse()
                resp.headers['Content-Type'] = 'text/event-stream'
                resp.headers['Cache-Control'] = 'no-cache'
                await resp.prepare(request)

                async for chunk in result.payload:
                    if chunk is None:
                        continue
                    if isinstance(chunk, str):
                        await resp.write(chunk.encode())
                    elif isinstance(chunk, bytes):
                        await resp.write(chunk)
                    else:
                        await resp.write(str(chunk).encode())

                return resp

            # 静态文件响应
            if result.meta and result.meta.get("static_content"):
                return web.Response(
                    body=result.meta["static_content"],
                    content_type=result.meta.get("content_type", "application/octet-stream"),
                )

            return web.json_response(result.payload if result.payload else {"ok": True})
        
        # ============================================================
        # handle_static — GET 请求
        # ============================================================
        async def handle_static(request):
            file_path = request.match_info.get("path", "")
            if not file_path:
                file_path = "index.html"

            # ============================================================
            # 内置端点（不转发给插件）
            # ============================================================

            # ★ WebSocket 配置（必须放在最前面，不被 startswith("api/") 捕获）
            if file_path == "api/ws_config":
                ws_config = agent.config.get("websocket", {})
                ws_external_url = ws_config.get("external_url", "")
                ws_port = agent.config.get("port", 9000) + 1
                
                is_secure = request.headers.get("X-Forwarded-Proto", request.scheme) == "https"
                hostname = request.host.split(':')[0]
                
                if ws_external_url:
                    url = ws_external_url
                else:
                    protocol = "wss" if is_secure else "ws"
                    url = f"{protocol}://{hostname}/ws"
                
                return web.json_response({
                    "url": url,
                    "port": ws_port,
                    "secure": is_secure
                })

            # 插件列表
            if file_path == "api/list":
                return await _route_get("os/_registry", request, action="list")

            # 页面列表
            if file_path == "api/pages":
                return await _route_get("os/_static", request, action="list")

            # ============================================================
            # 其他 API 路由
            # ============================================================
            if file_path.startswith("api/"):
                route = file_path[4:]
                return await _route_get(route, request)

            # data/ 目录文件
            if file_path.startswith("data/"):
                return await _route_get("os/_static", request,
                                    action="serve_data", file_path=file_path[5:])

            # 静态文件认证：根目录 .html 需要认证
            is_root_file = '/' not in file_path
            is_login_page = file_path in ['login.html', '404.html']

            if file_path.endswith('.html') and not is_login_page and is_root_file:
                ok, _ = await _verify_auth(request, agent, "__static__")
                if not ok:
                    from urllib.parse import quote
                    return web.Response(
                        text=f'<script>location.href="/login.html?redirect={quote(request.path)}"</script>',
                        content_type='text/html',
                    )

            # 静态文件
            return await _route_get("os/_static", request,
                                action="serve", file_path=file_path)
        
        async def _route_get(receiver, request, action=None, file_path=None):
            from urllib.parse import parse_qs
            
            payload = {}
            
            if action:
                payload["action"] = action
            if file_path:
                payload["file_path"] = file_path
            
            query = parse_qs(request.query_string)
            for key, values in query.items():
                if values:
                    payload[key] = values[0]
            
            env = core.Envelop(
                sender="os/_gateway",
                receiver=receiver,
                intent="API_CALL",
                payload=payload,
                meta={
                    "token": _extract_token(request),
                    "method": "GET",
                    "path": file_path or receiver,
                },
            )
            
            result = await agent.system.call(env)
            
            if not result:
                return web.json_response({"error": "no response"}, status=500)
            
            # 静态文件
            if result.meta and result.meta.get("static_content"):
                return web.Response(
                    body=result.meta["static_content"],
                    content_type=result.meta.get("content_type", "text/html"),
                )
            
            # 文件响应
            if result.meta and result.meta.get("response_type") == "file":
                fp = result.meta.get("file_path")
                if not fp or not __import__('pathlib').Path(fp).exists():
                    return web.json_response({"error": "File not found"}, status=404)
                return web.FileResponse(
                    path=fp,
                    headers={
                        "Content-Type": result.meta.get("content_type", "application/octet-stream"),
                        "Content-Disposition": f'{result.meta.get("content_disposition", "inline")}; filename="{result.meta.get("file_name", "file")}"'
                    }
                )
            
            # 错误
            if result.payload and result.payload.get("error"):
                status = 403 if "Forbidden" in str(result.payload.get("error", "")) else 404
                return web.Response(text=result.payload["error"], status=status)
            
            return web.json_response(result.payload if result.payload else {"ok": True})
        
        # ============================================================
        # 路由注册
        # ============================================================
        app.router.add_post("/v1/chat/completions", handle_v1_chat)  # ★ 新增
        app.router.add_post("/api/{path:.*}", handle_api)
        app.router.add_get("/", handle_static)
        app.router.add_get("/{path:.*}", handle_static)
        app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))
        
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        
        agent.log.info(f"[Gateway] HTTP server listening on http://{host}:{port}")
        
        envelop.payload = {"status": "listening", "port": port, "host": host}
        return envelop
    
    elif action == "STOP":
        envelop.payload = {"status": "stopped"}
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop