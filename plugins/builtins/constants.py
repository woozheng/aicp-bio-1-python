# plugins/builtins/constants.py
"""全局路径常量"""
from pathlib import Path

DATA_DIR = Path("data")
TASKS_DIR = DATA_DIR / "agents" / "tasks"
SCHEDULES_DIR = DATA_DIR / "agents" / "schedules"
MEMORIES_DIR = DATA_DIR / "memories"
CONFIG_PATH = DATA_DIR / "agents" / "config.json"
CAPABILITY_INDEX_PATH = DATA_DIR / "capability_index.json"