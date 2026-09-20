"""Engine Pool — 跨请求状态管理 · 协议 v3.0 系统插件
其他插件通过 HTTP POST /api/os/_pool 或 agent.system.call() 访问。
"""
import asyncio
import time
from typing import Dict, Optional

DEFAULT_TTL = 3600
GC_INTERVAL = 300


class SubEngine:
    def __init__(self, engine_id: str, config: dict):
        self.id = engine_id
        self.config = config
        self.created_at = time.time()
        self.last_access = time.time()
        self.ttl = config.get("ttl", DEFAULT_TTL)
        # 从config初始化state（排除系统字段）
        self.state: dict = {k: v for k, v in config.items() if k not in ("ttl",)}
    
    def touch(self):
        self.last_access = time.time()
    
    def expired(self) -> bool:
        return time.time() - self.last_access > self.ttl
    
    def time_remaining(self) -> float:
        return max(0, self.ttl - (time.time() - self.last_access))
    
    async def cleanup(self):
        self.state.clear()


class EnginePool:
    def __init__(self):
        self._engines: Dict[str, SubEngine] = {}
        self._lock = asyncio.Lock()
        self._gc_task: Optional[asyncio.Task] = None
    
    async def create(self, engine_id: str, config: dict) -> str:
        async with self._lock:
            if engine_id in self._engines:
                self._engines[engine_id].touch()
                return engine_id
            self._engines[engine_id] = SubEngine(engine_id, config)
            return engine_id
    
    async def get(self, engine_id: str) -> Optional[SubEngine]:
        async with self._lock:
            eng = self._engines.get(engine_id)
            if eng:
                eng.touch()
            return eng
    
    async def exists(self, engine_id: str) -> bool:
        async with self._lock:
            return engine_id in self._engines
    
    async def destroy(self, engine_id: str) -> bool:
        async with self._lock:
            if engine_id not in self._engines:
                return False
            eng = self._engines.pop(engine_id)
            await eng.cleanup()
            return True
    
    def list_engines(self) -> dict:
        return {
            eid: {
                "ttl": eng.ttl,
                "expired": eng.expired(),
                "time_remaining": eng.time_remaining(),
            }
            for eid, eng in self._engines.items()
        }
    
    async def start_gc(self):
        if self._gc_task and not self._gc_task.done():
            return
        
        async def gc_loop():
            while True:
                await asyncio.sleep(GC_INTERVAL)
                async with self._lock:
                    expired = [eid for eid, eng in self._engines.items() if eng.expired()]
                    for eid in expired:
                        eng = self._engines.pop(eid)
                        await eng.cleanup()
        
        self._gc_task = asyncio.create_task(gc_loop())
    
    async def cleanup_all(self):
        if self._gc_task and not self._gc_task.done():
            self._gc_task.cancel()
        async with self._lock:
            for eng in self._engines.values():
                await eng.cleanup()
            self._engines.clear()


_pool: Optional[EnginePool] = None


async def execute(envelop, agent):
    global _pool
    
    if _pool is None:
        _pool = EnginePool()
        await _pool.start_gc()
    
    action = envelop.payload.get("action", "")
    engine_id = envelop.payload.get("engine_id", "")
    
    if not engine_id and action not in ("list",):
        envelop.payload = {"error": "engine_id required"}
        return envelop
    
    if action == "create":
        config = envelop.payload.get("config", {})
        await _pool.create(engine_id, config)
        eng = await _pool.get(engine_id)
        envelop.payload = {
            "engine_id": engine_id,
            "ttl": eng.ttl if eng else DEFAULT_TTL,
            "time_remaining": eng.time_remaining() if eng else 0,
            "ok": True,
        }
    
    elif action == "get":
        key = envelop.payload.get("key")
        eng = await _pool.get(engine_id)
        if not eng:
            envelop.payload = {"error": "Engine not found"}
        elif key:
            envelop.payload = {key: eng.state.get(key)}
        else:
            envelop.payload = dict(eng.state)
    
    elif action == "set":
        key = envelop.payload.get("key")
        value = envelop.payload.get("value")
        updates = envelop.payload.get("updates")
        eng = await _pool.get(engine_id)
        if eng:
            if updates and isinstance(updates, dict):
                eng.state.update(updates)
            elif key is not None:
                eng.state[key] = value
            else:
                envelop.payload = {"error": "key+value or updates required"}
                return envelop
            envelop.payload = {"ok": True}
        else:
            envelop.payload = {"error": "Engine not found"}
    
    elif action == "exists":
        envelop.payload = {"exists": await _pool.exists(engine_id)}
    
    elif action == "destroy":
        ok = await _pool.destroy(engine_id)
        envelop.payload = {"ok": ok}
    
    elif action == "list":
        envelop.payload = {"engines": _pool.list_engines()}
    
    else:
        envelop.payload = {"error": f"Unknown action: {action}"}
    
    return envelop