"""Newbula Plus 引擎 — 多窗口并行 AI 操作引擎"""
import asyncio
import json
import uuid
import ctypes
from pathlib import Path
import time
import requests

PROJECT = Path(__file__).parent.name
CONFIG_FILE = Path(__file__).parent / "config.json"


class ClusterEngine:
    def __init__(self, agent):
        self.agent = agent
        self.probes = {}
        self.status = "idle"
        self._config = self._load_config()
        self._initialized = False

    def _load_config(self):
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"platforms": []}

    def _save_config(self):
        CONFIG_FILE.write_text(json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8")

    # ============================================================
    # 启动/停止
    # ============================================================
    async def start(self):
        if self._initialized:
            return {"error": "Engine already running"}
        self.status = "starting"
        platforms = self._config.get("platforms", [])
        enabled = [p for p in platforms if p.get("enabled", True)]
        if not enabled:
            self.status = "idle"
            return {"error": "No enabled platforms"}

        print(f"[NewbulaPlus] 串行启动 {len(enabled)} 个探头...")
        from . import chrome_manager, probe_wrapper, vision_helper

        index = 0
        for platform in enabled:
            try:
                print(f"[NewbulaPlus] 启动平台: {platform['name']}")
                proc, hwnd = chrome_manager.launch_chrome(
                    profile_dir=platform["profile_dir"], url=platform["url"],
                    port=platform["chrome_port"], platform_name=platform["name"],
                    position=(0, 0), size=(800, 600)
                )
                if not hwnd:
                    self.probes[platform["id"]] = {"probe": None, "config": platform, "status": "error: 找不到窗口"}
                    continue

                input_region = await vision_helper.find_input_region(self.agent, hwnd, platform["name"])
                x = index * 250
                probe = probe_wrapper.NewbulaProbe(
                    agent=self.agent, probe_id=platform["id"], name=platform["name"],
                    region=input_region, hwnd_target=hwnd,
                    chrome_port=platform["chrome_port"], platform_id=platform["id"],
                    position=(x, 0),
                    reader_config=platform.get("reader", {})
                )
                probe.start()
                await asyncio.sleep(2)
                await probe.connect_cdp()
                self.probes[platform["id"]] = {"probe": probe, "config": platform, "status": "ready"}
                print(f"[NewbulaPlus] ✅ 探头就绪: {platform['name']}")
                index += 1
            except Exception as e:
                print(f"[NewbulaPlus] ❌ 启动失败 {platform.get('name')}: {e}")
                import traceback
                traceback.print_exc()
                self.probes[platform.get("id", "unknown")] = {"probe": None, "config": platform, "status": f"error: {e}"}

        self.status = "running"
        self._initialized = True
        return self.get_status()

    async def stop(self):
        for pid, info in self.probes.items():
            if info["probe"]:
                if hasattr(info["probe"], '_release_viewfinder'):
                    info["probe"]._release_viewfinder()
                info["probe"].stop()
                info["status"] = "stopped"
        self.probes = {}
        self.status = "idle"
        self._initialized = False
        return {"engine": "newbula_plus", "status": "stopped", "total": 0, "ready": 0}

    # ============================================================
    # 🔥 重新吞噬窗口 — 完整版
    # ============================================================
    async def redevour(self):
        """重新吞噬：X 掉后重新启动 probe"""
        if self.status != "running":
            return {"error": "Engine not running"}

        results = []
        ready_count = 0

        for pid, info in self.probes.items():
            probe = info.get("probe")
            platform = info.get("config", {})
            name = platform.get("name", pid)

            if not probe:
                results.append(f"❌ {name}: 探头不存在")
                continue

            try:
                # 1. 停掉旧的
                if probe.running:
                    probe.stop()
                await asyncio.sleep(1)

                # 2. 重新 start（Chrome 还活着，hwnd 还在）
                probe.start()
                await asyncio.sleep(2)

                # 3. 重连 CDP
                ok = await probe.connect_cdp()
                if not ok:
                    # CDP 死了，用 requests 确认 Chrome 端口
                    try:
                        resp = requests.get(f"http://localhost:{platform['chrome_port']}/json", timeout=3)
                        if resp.status_code == 200:
                            ok = await probe.connect_cdp()
                    except Exception:
                        pass
                
                if ok:
                    info["status"] = "ready"
                    ready_count += 1
                    results.append(f"✅ {name}: 重新吞噬成功")
                else:
                    info["status"] = "error: CDP 连接失败"
                    results.append(f"❌ {name}: CDP 连接失败")

            except Exception as e:
                import traceback
                traceback.print_exc()
                info["status"] = f"error: {e}"
                results.append(f"❌ {name}: {e}")

        return {"summary": "\n".join(results), "ready": ready_count}

    def get_status(self):
        statuses = {}
        for pid, info in self.probes.items():
            statuses[pid] = {
                "name": info["config"].get("name", pid),
                "status": info["status"],
                "running": info["probe"].running if info["probe"] else False
            }
        return {"engine": "newbula_plus", "status": self.status, "probes": statuses,
                "total": len(statuses), "ready": sum(1 for s in statuses.values() if s["status"] == "ready")}

    # ============================================================
    # 核心方法
    # ============================================================
    def _get_ready_probes(self):
        return [(pid, info) for pid, info in self.probes.items()
                if info["status"] == "ready" and info["probe"]]

    async def _send_all(self, query, **kwargs):
        probes = self._get_ready_probes()
        for pid, info in probes:
            await info["probe"].send(text=query, **kwargs)
            await asyncio.sleep(0.5)
        return probes

    async def _wait_one(self, pid, info, timeout=60, is_generate=False):
        result = await info["probe"].wait_for_reply(timeout=timeout, is_generate=is_generate)
        return pid, {
            "reply": result.get("reply", ""),
            "media_urls": result.get("media_urls", []),
            "name": info["config"]["name"]
        }

    async def _wait_all(self, probes, timeout=60, is_generate=False):
        tasks = [asyncio.create_task(self._wait_one(pid, info, timeout, is_generate)) for pid, info in probes]
        merged = {}
        for task in asyncio.as_completed(tasks):
            pid, data = await task
            merged[pid] = data
        return merged

    async def _wait_all_race(self, probes, timeout=60, is_generate=False):
        tasks = [asyncio.create_task(self._wait_one(pid, info, timeout, is_generate)) for pid, info in probes]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=timeout)
        for task in pending:
            task.cancel()
        merged = {}
        for task in done:
            pid, data = task.result()
            if data.get("reply") or data.get("media_urls"):
                merged[pid] = data
        return merged

    def _aggregate(self, results):
        lines = []
        for pid, data in results.items():
            name = data.get("name", pid)
            reply = data.get("reply", "")
            error = data.get("error")
            if error:
                lines.append(f"❌ **{name}**: {error}")
            elif reply:
                lines.append(f"📌 **{name}**:\n{reply[:500]}")
            else:
                lines.append(f"⏳ **{name}**: 无回复")
        return "\n\n---\n\n".join(lines)

    def _auto_route(self, query):
        keyword_map = {
            "天气": ["豆包"], "新闻": ["豆包", "Grok"],
            "代码": ["DeepSeek", "千问"], "Python": ["DeepSeek"],
            "数学": ["DeepSeek"], "分析": ["千问", "Kimi"],
            "总结": ["Kimi", "千问"], "翻译": ["豆包"],
            "文档": ["千问"], "长文": ["Kimi", "Claude"],
        }
        matched = set()
        for keyword, platforms in keyword_map.items():
            if keyword in query:
                matched.update(platforms)
        enabled_platforms = {p["name"]: p["id"] for p in self._config.get("platforms", []) if p.get("enabled")}
        result = [pid for name, pid in enabled_platforms.items() if name in matched]
        if not result:
            result = list(enabled_platforms.values())
        return result

    # ============================================================
    # 对外接口
    # ============================================================
    async def search(self, query):
        if self.status != "running":
            return {"error": "Engine not running"}
        probes = self._get_ready_probes()
        if not probes:
            return {"error": "No ready probes"}
        await self._send_all(query)
        merged = await self._wait_all(probes)
        return {"query": query, "results": merged, "aggregated": self._aggregate(merged), "total": len(merged)}

    async def ask_some(self, platform_ids: list, query: str, images: list = None, files: list = None, race: bool = False):
        if not platform_ids:
            platform_ids = self._auto_route(query)
        probes = [(pid, self.probes[pid]) for pid in platform_ids
                  if pid in self.probes and self.probes[pid]["status"] == "ready"]
        if not probes:
            return {"error": "No ready probes"}
        import pyautogui
        orig_mouse = pyautogui.position()
        for pid, info in probes:
            await info["probe"].send(text=query, images=images, files=files)
            await asyncio.sleep(0.5)
        pyautogui.moveTo(orig_mouse[0], orig_mouse[1])
        
        if race:
            merged = await self._wait_all_race(probes)
        else:
            merged = await self._wait_all(probes)
        return {"query": query, "results": merged, "aggregated": self._aggregate(merged), "total": len(merged)}

    async def search_with_file(self, query: str, file_path: str):
        if self.status != "running":
            return {"error": "Engine not running"}
        probes = self._get_ready_probes()
        if not probes:
            return {"error": "No ready probes"}
        for pid, info in probes:
            await info["probe"].send(text=query, files=[file_path])
            await asyncio.sleep(0.5)
        merged = await self._wait_all(probes, timeout=120)
        return {"query": query, "file": file_path, "results": merged, "aggregated": self._aggregate(merged), "total": len(merged)}

    async def search_with_image(self, query: str, image_path: str):
        if self.status != "running":
            return {"error": "Engine not running"}
        probes = self._get_ready_probes()
        if not probes:
            return {"error": "No ready probes"}
        for pid, info in probes:
            await info["probe"].send(text=query, images=[image_path])
            await asyncio.sleep(0.5)
        merged = await self._wait_all(probes, timeout=120)
        return {"query": query, "image": image_path, "results": merged, "aggregated": self._aggregate(merged), "total": len(merged)}

    # ============================================================
    # 平台管理
    # ============================================================
    async def get_config(self):
        return {"platforms": self._config.get("platforms", []), "total": len(self._config.get("platforms", [])),
                "engine_status": self.status}

    async def open_chrome_only(self, url, name):
        for p in self._config.get("platforms", []):
            if p.get("url") == url or p.get("name") == name:
                from . import chrome_manager
                proc, hwnd = chrome_manager.launch_chrome(
                    profile_dir=p["profile_dir"], url=url, port=p["chrome_port"],
                    platform_name=p["name"], position=(0, 0), size=(800, 600))
                if not hwnd:
                    return {"error": "无法启动Chrome"}
                return {"ok": True, "platform": p}
        port = 9230 + len(self._config.get("platforms", []))
        profile_dir = f"./profiles/{name or 'new_platform'}"
        from . import chrome_manager
        proc, hwnd = chrome_manager.launch_chrome(
            profile_dir=profile_dir, url=url, port=port,
            platform_name=name or "新平台", position=(0, 0), size=(800, 600))
        if not hwnd:
            return {"error": "无法启动Chrome"}
        platform = {"id": str(uuid.uuid4())[:8], "name": name, "url": url,
                    "profile_dir": profile_dir, "chrome_port": port,
                    "reader": {"selector": "", "id_marker": ""}, "enabled": True}
        self._config.setdefault("platforms", []).append(platform)
        self._save_config()
        return {"ok": True, "platform": platform}

    async def add_platform_auto(self, url: str, name: str = None, force: bool = False):
        if not force:
            for platform in self._config.get("platforms", []):
                if platform.get("url") == url or platform.get("name") == name:
                    return {"error": f"平台已存在: {platform['name']}", "hint": "传入 force=true 强制重新分析"}
        if force:
            self._config["platforms"] = [p for p in self._config.get("platforms", [])
                                         if p.get("url") != url and p.get("name") != name]
        port = 9230 + len(self._config.get("platforms", []))
        profile_dir = f"./profiles/{name or 'new_platform'}"
        profile_exists = Path(profile_dir).exists()
        from . import chrome_manager
        proc, hwnd = chrome_manager.launch_chrome(
            profile_dir=profile_dir, url=url, port=port,
            platform_name=name or "新平台", position=(0, 0), size=(800, 600))
        if not hwnd:
            all_windows = chrome_manager.list_all_chrome_windows()
            for h, t in all_windows:
                if "Google Chrome" in t:
                    hwnd = h
                    break
        if not hwnd:
            return {"error": "无法找到Chrome窗口"}
        if hwnd:
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 1200, 900, 0x0004)
        if profile_exists:
            await asyncio.sleep(3)
        else:
            await asyncio.sleep(15)
        try:
            import websockets
            resp = requests.get(f"http://localhost:{port}/json", timeout=3)
            tabs = resp.json()
            ws = None
            for tab in tabs:
                if tab["type"] == "page":
                    ws = await websockets.connect(tab["webSocketDebuggerUrl"])
                    break
            if not ws:
                return {"error": "CDP连接失败"}
        except Exception as e:
            return {"error": f"CDP连接失败: {e}"}
        platform = {"id": str(uuid.uuid4())[:8], "name": name or url.split("//")[1].split("/")[0],
                    "url": url, "profile_dir": profile_dir, "chrome_port": port,
                    "reader": {"file": "scripts/default.js"}, "enabled": True}
        self._config.setdefault("platforms", []).append(platform)
        self._save_config()
        return {"ok": True, "platform": platform}

    async def remove_platform(self, platform_id: str):
        self._config["platforms"] = [p for p in self._config.get("platforms", []) if p.get("id") != platform_id]
        self._save_config()
        return {"ok": True}

    async def update_platform(self, platform_id: str, updates: dict):
        for p in self._config.get("platforms", []):
            if p.get("id") == platform_id:
                p.update(updates)
                self._save_config()
                return {"ok": True}
        return {"error": "平台不存在"}


_engine_instance = None


def get_engine(agent):
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = ClusterEngine(agent)
    return _engine_instance


def clean_reply(text: str) -> str:
    """清理 HTML 标签和平台前缀，返回纯文本"""
    import re
    cleaned = re.sub(r'<[^>]+>', '', text)
    cleaned = re.sub(r'\n\s*\n', '\n', cleaned).strip()
    if "📌 **" in cleaned:
        cleaned = cleaned.split("📌 **")[-1].split("**:", 1)[-1].strip()
    return cleaned


async def execute(envelop, agent):
    action = envelop.payload.get("action", "status")
    engine = get_engine(agent)
    engine._config = engine._load_config()

    if action == "start":
        envelop.payload = await engine.start()
    elif action == "stop":
        envelop.payload = await engine.stop()
    elif action == "status":
        envelop.payload = engine.get_status()
    elif action == "redevour":
        envelop.payload = await engine.redevour()
    elif action == "search":
        query = envelop.payload.get("query", "")
        platform_ids = envelop.payload.get("platform_ids", [])
        if not platform_ids:
            platform_ids = [pid for pid, info in engine.probes.items() if info["status"] == "ready"]
        envelop.payload = await engine.ask_some(platform_ids, query)
    elif action == "ask_some":
        platform_ids = envelop.payload.get("platform_ids", [])
        query = envelop.payload.get("query", "")
        images = envelop.payload.get("images", [])
        files = envelop.payload.get("files", [])
        race = envelop.payload.get("race", False)
        stream = envelop.payload.get("stream", False)
        
        if stream:
            async def sse_stream():
                probes = [(pid, engine.probes[pid]) for pid in platform_ids
                          if pid in engine.probes and engine.probes[pid]["status"] == "ready"]
                if not probes:
                    yield f"data: {json.dumps({'error': 'No ready probes'})}\n\n"
                    return
                for pid, info in probes:
                    await info["probe"].send(text=query, images=images, files=files)
                    await asyncio.sleep(0.5)
                async def wait_one(pid, info):
                    result = await info["probe"].wait_for_reply(timeout=60)
                    return pid, {
                        "reply": result.get("reply", ""),
                        "media_urls": result.get("media_urls", []),
                        "name": info["config"]["name"]
                    }
                tasks = [asyncio.create_task(wait_one(pid, info)) for pid, info in probes]
                merged = {}
                for task in asyncio.as_completed(tasks):
                    pid, data = await task
                    merged[pid] = data
                    yield f"data: {json.dumps({'pid': pid, 'data': data, 'total_done': len(merged), 'total': len(probes)}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'done': True, 'results': merged}, ensure_ascii=False)}\n\n"
            envelop.payload = sse_stream()
            envelop.meta["content_type"] = "text/event-stream"
            return envelop
        else:
            envelop.payload = await engine.ask_some(platform_ids, query, images=images, files=files, race=race)
            return envelop
    elif action == "chat_completions":
        model = envelop.payload.get("model", "newbula-plus")
        messages = envelop.payload.get("messages", [])
        stream = envelop.payload.get("stream", False)

        query = ""
        image_paths = []
        
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if role == "system":
                query += f"[系统指令] {content}\n"
            elif role == "user":
                if isinstance(content, str):
                    query += content
                elif isinstance(content, list):
                    for part in content:
                        part_type = part.get("type", "")
                        if part_type == "text":
                            query += part.get("text", "")
                        elif part_type == "image_url":
                            image_url = part.get("image_url", {}).get("url", "")
                            if image_url.startswith("data:image"):
                                import base64, tempfile
                                b64_data = image_url.split("base64,")[1]
                                temp_path = tempfile.mktemp(suffix=".png")
                                with open(temp_path, "wb") as f:
                                    f.write(base64.b64decode(b64_data))
                                image_paths.append(temp_path)
                            elif image_url.startswith("file://"):
                                image_paths.append(image_url[7:])
                            elif image_url.startswith("http"):
                                import requests, tempfile
                                resp = requests.get(image_url, timeout=10)
                                if resp.status_code == 200:
                                    temp_path = tempfile.mktemp(suffix=".png")
                                    with open(temp_path, "wb") as f:
                                        f.write(resp.content)
                                    image_paths.append(temp_path)
                elif role == "assistant":
                    if isinstance(content, str):
                        query += content

        engine = get_engine(agent)
        
        # 🔥 model → platform_id 路由
        model_routes = {}
        for p in engine._config.get("platforms", []):
            p_id = p.get("id", "")
            for mid in p.get("model_ids", []):
                model_routes[mid] = p_id
        
        target_platform_id = model_routes.get(model)
        
        if target_platform_id:
            platform_ids = [target_platform_id]
        else:
            platform_ids = [pid for pid, info in engine.probes.items() if info["status"] == "ready"]
        
        if not platform_ids:
            envelop.payload = {"error": "No ready platforms"}
            return envelop

        probes = [(pid, engine.probes[pid]) for pid in platform_ids
                if pid in engine.probes and engine.probes[pid]["status"] == "ready"]
        
        if not probes:
            envelop.payload = {"error": "No ready probes"}
            return envelop

        if stream:
            async def sse_generator():
                import uuid as _uuid
                import time as _time
                
                yield f"data: {json.dumps({'id': f'chatcmpl-{_uuid.uuid4().hex[:8]}', 'object': 'chat.completion.chunk', 'created': int(_time.time()), 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                
                for pid, info in probes:
                    await info["probe"].send(text=query, images=image_paths if image_paths else [])
                    await asyncio.sleep(0.5)
                
                for pid, info in probes:
                    result = await info["probe"].wait_for_reply(timeout=120)
                    reply = result.get("reply", "")
                    name = info["config"]["name"]
                    if reply:
                        clean = clean_reply(reply)
                        chunk = f"\n\n{clean}\n\n"
                        yield f"data: {json.dumps({'id': f'chatcmpl-{_uuid.uuid4().hex[:8]}', 'object': 'chat.completion.chunk', 'created': int(_time.time()), 'model': model, 'choices': [{'index': 0, 'delta': {'content': chunk}, 'finish_reason': None}]})}\n\n"
                
                yield f"data: {json.dumps({'id': f'chatcmpl-{_uuid.uuid4().hex[:8]}', 'object': 'chat.completion.chunk', 'created': int(_time.time()), 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                yield "data: [DONE]\n\n"
            
            envelop.payload = sse_generator()
            envelop.meta["content_type"] = "text/event-stream"
            return envelop

        for pid, info in probes:
            await info["probe"].send(text=query, images=image_paths if image_paths else [])
            await asyncio.sleep(0.5)

        merged = {}
        for pid, info in probes:
            result = await info["probe"].wait_for_reply(timeout=120)
            reply = result.get("reply", "")
            name = info["config"]["name"]
            merged[pid] = {"reply": reply, "name": name}

        lines = []
        for pid, data in merged.items():
            name = data.get("name", pid)
            reply = data.get("reply", "")
            if reply:
                lines.append(f"📌 **{name}**:\n{reply[:500]}")
            else:
                lines.append(f"⏳ **{name}**: 无回复")
        raw_content = "\n\n---\n\n".join(lines) if lines else "无回复"
        content = clean_reply(raw_content)

        envelop.payload = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": len(query),
                "completion_tokens": len(content),
                "total_tokens": len(query) + len(content)
            }
        }
        return envelop
    elif action == "search_with_file":
        envelop.payload = await engine.search_with_file(
            envelop.payload.get("query", ""), envelop.payload.get("file_path", ""))
    elif action == "search_with_image":
        envelop.payload = await engine.search_with_image(
            envelop.payload.get("query", ""), envelop.payload.get("image_path", ""))
    elif action == "get_config":
        envelop.payload = await engine.get_config()
    elif action == "add_platform_auto":
        envelop.payload = await engine.add_platform_auto(
            envelop.payload.get("url", ""), envelop.payload.get("name", ""),
            envelop.payload.get("force", False))
    elif action == "open_chrome":
        envelop.payload = await engine.open_chrome_only(
            envelop.payload.get("url", ""), envelop.payload.get("name", ""))
    elif action == "remove_platform":
        envelop.payload = await engine.remove_platform(envelop.payload.get("platform_id", ""))
    elif action == "update_platform":
        envelop.payload = await engine.update_platform(
            envelop.payload.get("platform_id", ""), envelop.payload.get("updates", {}))
    else:
        envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop