"""探头持久化存储"""
import json
import time
from pathlib import Path

DATA_DIR = Path("data/probes")
CONFIG_FILE = DATA_DIR / "probes.json"


class PersistManager:
    def __init__(self, manager):
        self.manager = manager
        self._ensure_dir()
    
    def _ensure_dir(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    def _load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save(self, data):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def save_probe(self, probe_id, config):
        data = self._load()
        data[probe_id] = config
        self._save(data)
    
    def update_probe(self, probe_id, updates):
        data = self._load()
        if probe_id in data:
            data[probe_id].update(updates)
            self._save(data)
    
    def delete_probe(self, probe_id):
        data = self._load()
        if probe_id in data:
            del data[probe_id]
            self._save(data)
    # 添加规则相关方法
    def get_history_rules(self, limit=20):
        """获取历史规则列表（按使用次数排序）"""
        data = self._load()
        rules = data.get("saved_rules", [])
        # 按使用次数排序，取前 limit 条
        rules.sort(key=lambda x: x.get("use_count", 0), reverse=True)
        return [r["text"] for r in rules[:limit]]

    def save_rule(self, rule_text):
        """保存规则到历史库"""
        data = self._load()
        rules = data.get("saved_rules", [])
        
        # 查找是否已存在
        for r in rules:
            if r["text"] == rule_text:
                r["use_count"] = r.get("use_count", 0) + 1
                self._save(data)
                return
        
        # 新增规则
        rules.append({
            "text": rule_text,
            "use_count": 1,
            "created": time.time()
        })
        data["saved_rules"] = rules
        self._save(data)

    def restore_all_probes(self):
        """启动时恢复所有探头"""
        data = self._load()
        for probe_id, config in data.items():
            if config.get("enabled", True):
                self.manager.create(
                    name=config.get("name", "restored"),
                    region=config.get("region", {}),
                    interval=config.get("interval", 5),
                    rules=config.get("rules", []),
                    mode=config.get("mode", "ocr"),
                    hwnd_target=config.get("hwnd"),
                    program_path=config.get("program_path"),
                    program_args=config.get("program_args"),
                    program_url=config.get("program_url")
                )