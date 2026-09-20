"""MK 全局状态 — 单一数据源"""
from dataclasses import dataclass, field
from typing import Optional
import threading


@dataclass
class MKState:
    left_down_pos: Optional[tuple] = None
    left_down_time: Optional[float] = None
    pending_select: bool = False
    select_pos: tuple = (0, 0)
    selected_text: str = ""
    middle_action: str = "auto"
    input_mode: str = "clipboard"
    output_to_desktop: bool = False
    selecting_region: bool = False
    region_select_end_time: float = 0.0
    chat_region: Optional[dict] = None
    chat_running: bool = False
    chat_memory: list = field(default_factory=list)
    agent: Optional[object] = None
    base_url: str = ""
    
    # 锁定捕获的文字，防止被覆盖
    captured_text: str = ""
    captured_text_lock: bool = False
    
    # 🔥 新增：监控线程控制
    monitor_running: bool = False
    monitor_thread: Optional[threading.Thread] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    magic_active: bool = False
    magic_stop_requested: bool = False  # 新增：停止请求标志


_state = MKState()


def get_state() -> MKState:
    return _state