# runtime/wx_utils.py
import wx
import threading
import time
import logging

_wx_app = None
_initialized = False
_app_ready = threading.Event()
_shutting_down = False


def _init_wx_thread():
    """在独立线程中创建 App 并跑 MainLoop"""
    global _wx_app, _shutting_down

    _wx_app = wx.App(False)

    # 禁用所有弹窗
    wx.Log.SetLogLevel(wx.LOG_FatalError)
    wx.DisableAsserts()
    
    keepalive = wx.Frame(None, title="", size=(1, 1))
    keepalive.Hide()

    _app_ready.set()

    logging.getLogger("aicp").info("[wx] MainLoop started")
    _wx_app.MainLoop()
    _shutting_down = False
    logging.getLogger("aicp").info("[wx] MainLoop ended")

def init():
    """系统启动时调用"""
    global _initialized

    if _initialized:
        return

    logging.getLogger("aicp").info("[wx] Initializing...")
    threading.Thread(target=_init_wx_thread, daemon=True, name="wx-thread").start()
    _app_ready.wait(timeout=5)
    _initialized = True
    logging.getLogger("aicp").info("[wx] Initialized ✓")


def get_app():
    if not _initialized:
        init()
    return _wx_app


def shutdown():
    """退出时清理所有 wx 窗口"""
    global _shutting_down, _wx_app
    _shutting_down = True

    if not _wx_app:
        return

    logger = logging.getLogger("aicp")
    logger.info("[wx] Shutting down...")

    def _destroy_all():
        try:
            for win in list(wx.GetTopLevelWindows()):
                try:
                    win.Hide()      # 先隐藏，避免关闭动画
                    win.Destroy()   # 直接销毁
                except Exception:
                    pass
        except Exception:
            pass

    wx.CallAfter(_destroy_all)
    time.sleep(0.3)

    try:
        wx.CallAfter(_wx_app.ExitMainLoop)
    except Exception:
        pass

    _wx_app = None
    logger.info("[wx] Shutdown complete")