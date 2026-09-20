# plugins/builtins/agents/main_agent/__init__.py
from .main_agent import execute, help

# ★ 自动注册到 core.plugins
import core
core.plugins["builtins/agents/main_agent"] = execute

__all__ = ["execute", "help"]