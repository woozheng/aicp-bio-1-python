# plugins/builtins/agents/config.py
"""全局配置管理 — 通道可扩展"""
import json
from pathlib import Path
from plugins.builtins.constants import CONFIG_PATH

CHANNEL_TEMPLATES = {
    "email": {
        "enabled": False,
        "imap_host": "",
        "imap_port": 993,
        "smtp_host": "",
        "smtp_port": 465,
        "user": "",
        "password": "",
        "check_interval": 60
    },
    "feishu": {
        "enabled": False,
        "app_id": "",
        "app_secret": "",
        "webhook_url": ""
    },
    "wechat": {
        "enabled": False,
        "corp_id": "",
        "corp_secret": "",
        "agent_id": "",
        "webhook_url": ""
    },
    "dingtalk": {
        "enabled": False,
        "app_key": "",
        "app_secret": "",
        "webhook_url": ""
    },
}

DEFAULT = {"channels": {k: v.copy() for k, v in CHANNEL_TEMPLATES.items()}}


def load() -> dict:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg.setdefault("channels", {})
        for ch_name, ch_template in CHANNEL_TEMPLATES.items():
            if ch_name not in cfg["channels"]:
                cfg["channels"][ch_name] = ch_template.copy()
        return cfg
    return DEFAULT.copy()


def write(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_channel_config(channel: str) -> dict:
    return load().get("channels", {}).get(channel, {})


def is_channel_enabled(channel: str) -> bool:
    return get_channel_config(channel).get("enabled", False)


async def get(envelop, agent):
    envelop.payload = {"ok": True, "data": load()}
    return envelop


async def save(envelop, agent):
    new_cfg = envelop.payload.get("config", {})
    cfg = load()
    if "channels" in new_cfg:
        cfg["channels"].update(new_cfg["channels"])
    write(cfg)
    envelop.payload = {"ok": True}
    return envelop