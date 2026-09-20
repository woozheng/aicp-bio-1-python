"""
插件发现与注册中心 — 协议 v4.0
提供插件的注册、注销、查询、热重载能力，作为系统的权威插件来源。
"""

import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set
from datetime import datetime

import core


# ============================================================
# 常量
# ============================================================

PLUGINS_ROOT = Path("plugins")
CACHE_FILE = Path("data/registry_cache.json")
SYNC_LOCK = asyncio.Lock()


# ============================================================
# 插件元数据模型
# ============================================================

class PluginMetadata:
    """插件元数据"""
    
    def __init__(self, name: str, file_path: Path):
        self.name = name
        self.file_path = file_path
        self.file_hash = self._calc_hash()
        self.last_modified = file_path.stat().st_mtime if file_path.exists() else 0
        self.help_data = None
        self.execute_func = None
        self.is_loaded = False
        self.load_error = None
        self.last_loaded = None
    
    def _calc_hash(self) -> str:
        """计算文件 hash（快速）"""
        if not self.file_path.exists():
            return "missing"
        try:
            with open(self.file_path, "rb") as f:
                return hashlib.md5(f.read(65536)).hexdigest()[:12]
        except Exception:
            return "error"
    
    def refresh_hash(self) -> str:
        """刷新 hash"""
        self.file_hash = self._calc_hash()
        if self.file_path.exists():
            self.last_modified = self.file_path.stat().st_mtime
        return self.file_hash
    
    def is_changed(self) -> bool:
        """检查文件是否变化"""
        if not self.file_path.exists():
            return self.file_hash != "missing"
        current_hash = self._calc_hash()
        return current_hash != self.file_hash
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "name": self.name,
            "file_path": str(self.file_path),
            "file_hash": self.file_hash,
            "last_modified": self.last_modified,
            "is_loaded": self.is_loaded,
            "load_error": self.load_error,
            "last_loaded": self.last_loaded.isoformat() if self.last_loaded else None,
            "help": self.help_data
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PluginMetadata":
        """从字典反序列化"""
        meta = cls(data["name"], Path(data["file_path"]))
        meta.file_hash = data.get("file_hash", "unknown")
        meta.last_modified = data.get("last_modified", 0)
        meta.is_loaded = data.get("is_loaded", False)
        meta.load_error = data.get("load_error")
        meta.help_data = data.get("help")
        if data.get("last_loaded"):
            meta.last_loaded = datetime.fromisoformat(data["last_loaded"])
        return meta


# ============================================================
# 工具函数：删除契约文件
# ============================================================

def _delete_contract_file(plugin_name: str, agent=None) -> bool:
    """删除插件对应的契约文件"""
    contract_file = PLUGINS_ROOT / f"{plugin_name}.contract.json"
    if contract_file.exists():
        try:
            contract_file.unlink()
            if agent:
                agent.log.info(f"[registry] 删除契约: {contract_file}")
            return True
        except Exception as e:
            if agent:
                agent.log.warning(f"[registry] 删除契约失败: {contract_file}, {e}")
            return False
    return False


# ============================================================
# 插件注册中心
# ============================================================

class PluginRegistry:
    """插件注册中心 - 单例"""
    
    def __init__(self):
        self._plugins: Dict[str, PluginMetadata] = {}
        self._loaded_modules: Set[str] = set()
        self._initialized = False
        self._agent = None
        self._cache_dirty = False
    
    def initialize(self, agent):
        """初始化注册中心"""
        if self._initialized:
            return
        self._agent = agent
        self._load_cache()
        self._scan_plugins()
        
        loaded = 0
        failed = 0
        for name in list(self._plugins.keys()):
            if self.load_plugin(name):
                loaded += 1
            else:
                failed += 1
        
        self._initialized = True
        agent.log.info(f"[registry] 初始化完成，发现 {len(self._plugins)} 个插件，加载 {loaded} 个，失败 {failed} 个")
    
    # ============================================================
    # 插件发现与扫描
    # ============================================================
    
    def _scan_plugins(self) -> int:
        """扫描文件系统发现插件，检测变化时自动删除过期契约"""
        discovered = 0
        plugin_files = self._find_plugin_files()
        
        for name, file_path in plugin_files.items():
            if name in self._plugins:
                if self._plugins[name].is_changed():
                    self._plugins[name].refresh_hash()
                    self._cache_dirty = True
                    
                    # ★★★ 检测到文件变化，删除过期契约文件 ★★★
                    _delete_contract_file(name, self._agent)
                    
                    if self._agent:
                        self._agent.log.info(f"[registry] 检测到插件变化: {name}（契约已删除）")
            else:
                self._plugins[name] = PluginMetadata(name, file_path)
                self._cache_dirty = True
                discovered += 1
                if self._agent:
                    self._agent.log.info(f"[registry] 发现新插件: {name}")
        
        # 检查已删除的插件
        for name in list(self._plugins.keys()):
            if not self._plugins[name].file_path.exists():
                if self._agent:
                    self._agent.log.info(f"[registry] 检测到插件删除: {name}")
                
                # ★★★ 插件被删除，删除契约文件 ★★★
                _delete_contract_file(name, self._agent)
                
                del self._plugins[name]
                self._cache_dirty = True
        
        if discovered > 0 or self._cache_dirty:
            self._save_cache()
        
        return discovered
    
    def _find_plugin_files(self) -> Dict[str, Path]:
        """查找所有插件文件，支持包模式（__init__.py）"""
        plugins = {}
        
        if not PLUGINS_ROOT.exists():
            return plugins
        
        # 第一遍：收集所有 __init__.py 所在的目录（包入口）
        init_dirs: Set[Path] = set()
        for py_file in PLUGINS_ROOT.rglob("__init__.py"):
            init_dirs.add(py_file.parent)
        
        for py_file in PLUGINS_ROOT.rglob("*.py"):
            if py_file.name in ("_init.py",):
                continue
            
            rel_path = py_file.relative_to(PLUGINS_ROOT)
            
            # 如果是 __init__.py → 用目录名作为路由
            if py_file.name == "__init__.py":
                # 检查这个目录是否有 __init__.py（已有）
                name = str(rel_path.parent).replace("\\", "/")
                if name == ".":
                    continue
                plugins[name] = py_file
                continue
            
            # 普通 .py 文件：检查是否在包目录内
            parent_dir = py_file.parent
            if parent_dir in init_dirs:
                # ★ 这是包内部的模块，跳过（由包的 __init__.py 暴露）
                continue
            
            # 不在包内，正常注册
            name = str(rel_path.with_suffix("")).replace("\\", "/")
            
            if "_ui" in name or "ui_generator" in name:
                continue
            
            plugins[name] = py_file
        
        return plugins
    
    # ============================================================
    # 缓存管理（持久化）
    # ============================================================
    
    def _load_cache(self):
        """从缓存加载插件元数据"""
        if not CACHE_FILE.exists():
            return
        
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            for name, meta_data in data.get("plugins", {}).items():
                self._plugins[name] = PluginMetadata.from_dict(meta_data)
            if self._agent:
                self._agent.log.info(f"[registry] 加载缓存: {len(self._plugins)} 个插件")
        except Exception as e:
            if self._agent:
                self._agent.log.warning(f"[registry] 缓存加载失败: {e}")
    
    def _save_cache(self):
        """保存缓存"""
        if not self._cache_dirty:
            return
        
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "4.0",
                "updated": datetime.now().isoformat(),
                "total": len(self._plugins),
                "plugins": {name: meta.to_dict() for name, meta in self._plugins.items()}
            }
            CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self._cache_dirty = False
        except Exception as e:
            if self._agent:
                self._agent.log.error(f"[registry] 缓存保存失败: {e}")
    
    # ============================================================
    # 插件加载与执行
    # ============================================================
    
    def load_plugin(self, name: str) -> bool:
        """加载插件（动态导入）"""
        meta = self._plugins.get(name)
        if not meta:
            return False

        if meta.is_loaded and not meta.is_changed():
            return True

        try:
            module_name = f"plugins.{name.replace('/', '.')}"

            if module_name in sys.modules:
                del sys.modules[module_name]

            spec = importlib.util.spec_from_file_location(module_name, meta.file_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load spec for {name}")

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, "execute"):
                return True

            core.plugins[name] = module.execute
            meta.execute_func = module.execute
            meta.is_loaded = True
            meta.load_error = None
            meta.last_loaded = datetime.now()

            if hasattr(module, "help"):
                try:
                    help_data = module.help()
                    json.dumps(help_data)
                    meta.help_data = help_data
                except Exception as e:
                    meta.help_data = {"error": str(e)}

            self._cache_dirty = True
            self._save_cache()

            if self._agent:
                self._agent.log.debug(f"[registry] 加载成功: {name}")
            return True

        except Exception as e:
            meta.is_loaded = False
            meta.load_error = str(e)
            self._cache_dirty = True
            if self._agent:
                self._agent.log.debug(f"[registry] 加载失败 {name}: {e}")
            return False
    
    def unload_plugin(self, name: str):
        """卸载插件"""
        meta = self._plugins.get(name)
        if meta:
            meta.is_loaded = False
            meta.execute_func = None
            self._cache_dirty = True
        
        module_name = f"plugins.{name.replace('/', '.')}"
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        if name in core.plugins:
            del core.plugins[name]
    
    # ============================================================
    # 查询接口
    # ============================================================
    
    def list_plugins(self, include_unloaded: bool = False) -> List[dict]:
        """列出所有插件"""
        result = []
        for name, meta in self._plugins.items():
            if not include_unloaded and not meta.is_loaded:
                continue
            result.append({
                "route": f"/api/{name}",
                "name": name,
                "file_path": str(meta.file_path),
                "file_hash": meta.file_hash,
                "is_loaded": meta.is_loaded,
                "help": meta.help_data,
                "load_error": meta.load_error
            })
        return result
    
    def get_plugin_info(self, name: str) -> Optional[dict]:
        """获取单个插件信息"""
        meta = self._plugins.get(name)
        if not meta:
            return None
        return {
            "route": f"/api/{name}",
            "name": name,
            "file_path": str(meta.file_path),
            "file_hash": meta.file_hash,
            "is_loaded": meta.is_loaded,
            "help": meta.help_data,
            "load_error": meta.load_error,
            "last_loaded": meta.last_loaded.isoformat() if meta.last_loaded else None
        }
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        loaded = sum(1 for m in self._plugins.values() if m.is_loaded)
        with_error = sum(1 for m in self._plugins.values() if m.load_error)
        return {
            "total": len(self._plugins),
            "loaded": loaded,
            "with_error": with_error,
            "cache_file": str(CACHE_FILE)
        }


# ============================================================
# 全局单例
# ============================================================

_registry = PluginRegistry()


# ============================================================
# 插件入口
# ============================================================

async def execute(envelop, agent):
    """注册中心入口"""
    action = envelop.payload.get("action", "list")
    
    _registry.initialize(agent)
    
    handlers = {
        "list": _handle_list,
        "reload": _handle_reload,
        "sync": _handle_sync,
        "get": _handle_get,
        "stats": _handle_stats,
        "load": _handle_load,
        "unload": _handle_unload,
        "refresh": _handle_refresh
    }
    
    handler = handlers.get(action)
    if not handler:
        envelop.payload = {"error": f"Unknown action: {action}"}
        return envelop
    
    return await handler(envelop, agent)


# ============================================================
# 动作处理器
# ============================================================

async def _handle_list(envelop, agent):
    """列出所有已加载插件"""
    include_unloaded = envelop.payload.get("include_unloaded", False)
    plugins = _registry.list_plugins(include_unloaded)
    envelop.payload = {
        "ok": True,
        "apis": plugins,
        "total": len(plugins),
        "stats": _registry.get_stats()
    }
    return envelop


async def _handle_reload(envelop, agent):
    """热重载指定插件（支持新增、修改、删除）"""
    route_name = envelop.payload.get("route", "")
    if not route_name:
        envelop.payload = {"error": "No route specified"}
        return envelop
    
    # 先重新扫描，发现文件变化
    _registry._scan_plugins()
    
    # ★★★ 强制删除契约文件，确保下次 query_plugin 重新提取 ★★★
    _delete_contract_file(route_name, agent)
    
    meta = _registry._plugins.get(route_name)
    
    # 插件已被删除
    if not meta:
        if route_name in core.plugins:
            del core.plugins[route_name]
            agent.log.info(f"[registry] 已移除插件: {route_name}")
        
        # 契约文件已在 _delete_contract_file 中删除，但再检查一次
        _delete_contract_file(route_name, agent)
        
        envelop.payload = {
            "ok": True,
            "route": route_name,
            "removed": True,
            "message": f"Plugin {route_name} removed"
        }
        return envelop
    
    # 加载插件（新增或修改）
    success = _registry.load_plugin(route_name)
    if not success:
        envelop.payload = {
            "ok": False,
            "error": meta.load_error if meta else f"Plugin not found: {route_name}"
        }
        return envelop
    
    agent.log.info(f"[registry] 重载成功: {route_name}（契约已删除，下次查询将重新提取）")
    
    envelop.payload = {
        "ok": True,
        "route": route_name,
        "info": _registry.get_plugin_info(route_name),
        "contract_deleted": True,
        "message": f"Plugin {route_name} reloaded, contract file removed"
    }
    return envelop


async def _handle_sync(envelop, agent):
    """主动同步所有插件"""
    async with SYNC_LOCK:
        discovered = _registry._scan_plugins()
        
        loaded = 0
        for name, meta in _registry._plugins.items():
            if not meta.is_loaded:
                if _registry.load_plugin(name):
                    loaded += 1
        
        envelop.payload = {
            "ok": True,
            "discovered": discovered,
            "loaded": loaded,
            "stats": _registry.get_stats()
        }
        return envelop


async def _handle_get(envelop, agent):
    """获取单个插件信息"""
    name = envelop.payload.get("name", "")
    if not name:
        envelop.payload = {"error": "name required"}
        return envelop
    
    info = _registry.get_plugin_info(name)
    if not info:
        envelop.payload = {"error": f"Plugin not found: {name}"}
        return envelop
    
    envelop.payload = {"ok": True, "plugin": info}
    return envelop


async def _handle_stats(envelop, agent):
    """获取统计信息"""
    envelop.payload = {"ok": True, "stats": _registry.get_stats()}
    return envelop


async def _handle_load(envelop, agent):
    """加载指定插件（不触发重载）"""
    name = envelop.payload.get("name", "")
    if not name:
        envelop.payload = {"error": "name required"}
        return envelop
    
    _registry._scan_plugins()
    
    success = _registry.load_plugin(name)
    envelop.payload = {
        "ok": success,
        "name": name,
        "info": _registry.get_plugin_info(name) if success else None
    }
    return envelop


async def _handle_unload(envelop, agent):
    """卸载指定插件"""
    name = envelop.payload.get("name", "")
    if not name:
        envelop.payload = {"error": "name required"}
        return envelop
    
    _registry.unload_plugin(name)
    envelop.payload = {"ok": True, "name": name}
    return envelop


async def _handle_refresh(envelop, agent):
    """强制刷新缓存（重建所有索引）"""
    async with SYNC_LOCK:
        CACHE_FILE.unlink(missing_ok=True)
        
        # ★★★ 删除所有契约文件 ★★★
        contract_files = list(PLUGINS_ROOT.glob("*.contract.json"))
        deleted = 0
        for cf in contract_files:
            try:
                cf.unlink()
                deleted += 1
            except Exception:
                pass
        
        _registry._plugins.clear()
        _registry._cache_dirty = True
        
        _registry._scan_plugins()
        
        for name in list(_registry._plugins.keys()):
            _registry.load_plugin(name)
        
        agent.log.info(f"[registry] 刷新完成，删除 {deleted} 个契约文件")
        
        envelop.payload = {
            "ok": True,
            "stats": _registry.get_stats(),
            "contracts_deleted": deleted,
            "message": f"Registry fully refreshed, {deleted} contract files deleted"
        }
        return envelop


# ============================================================
# 帮助信息
# ============================================================

def help() -> dict:
    """返回插件契约信息"""
    return {
        "route": "os/_registry",
        "description": "插件注册中心 - 管理所有插件的发现、加载、重载和查询",
        "input": {
            "action": "required - list|reload|sync|get|stats|load|unload|refresh",
            "route": "optional - 插件路由名，reload 时需要",
            "name": "optional - 插件名，get/load/unload 时需要",
            "include_unloaded": "optional - list 时是否包含未加载的插件"
        },
        "output": {
            "ok": "操作是否成功",
            "error": "错误信息（失败时）",
            "apis": "插件列表",
            "stats": "统计信息",
            "plugin": "单个插件信息",
            "contract_deleted": "是否删除了契约文件（reload 时）",
            "contracts_deleted": "删除的契约文件数量（refresh 时）"
        }
    }