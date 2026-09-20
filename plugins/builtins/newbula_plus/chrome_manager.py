"""Chrome 管理 — 启动/窗口查找/置顶"""
import subprocess
import ctypes
import time
import requests
import os
import shutil
import re
from ctypes import wintypes
from pathlib import Path

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_chrome_path():
    """自动查找 Chrome 可执行文件路径"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
        path = winreg.QueryValue(key, None)
        winreg.CloseKey(key)
        if path and Path(path).exists():
            print(f"[ChromeManager] 从注册表找到 Chrome: {path}")
            return str(Path(path))
    except Exception as e:
        print(f"[ChromeManager] 注册表查找失败: {e}")

    common_paths = [
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        f"C:/Users/{os.getenv('USERNAME')}/AppData/Local/Google/Chrome/Application/chrome.exe",
    ]

    for path in common_paths:
        p = Path(path)
        if p.exists():
            print(f"[ChromeManager] 从路径找到 Chrome: {path}")
            return str(p)

    chrome = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")
    if chrome:
        print(f"[ChromeManager] 从 PATH 找到 Chrome: {chrome}")
        return chrome

    raise Exception("Chrome not found. Please install Google Chrome.")


def find_window_by_title(title_keyword):
    """通过窗口标题找句柄（精确匹配关键词）"""
    hwnds = []

    @WNDENUMPROC
    def callback(hwnd, lparam):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(1024)
            ctypes.windll.user32.GetWindowTextW(hwnd, title, 1023)
            if "Google Chrome" in title.value and title_keyword in title.value:
                hwnds.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(callback, 0)
    return hwnds[0] if hwnds else None


def list_all_chrome_windows():
    """列出所有 Chrome 窗口标题（调试用）"""
    hwnds = []

    @WNDENUMPROC
    def callback(hwnd, lparam):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(1024)
            ctypes.windll.user32.GetWindowTextW(hwnd, title, 1023)
            if "Google Chrome" in title.value:
                hwnds.append((hwnd, title.value))
        return True

    ctypes.windll.user32.EnumWindows(callback, 0)
    return hwnds


def bring_window_to_front(hwnd):
    """把窗口置顶"""
    if hwnd:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        ctypes.windll.user32.BringWindowToTop(hwnd)
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE


def launch_chrome(profile_dir, url, port, platform_name, position=(0, 0), size=(800, 600)):
    """启动 Chrome 实例，返回 (proc, hwnd)"""
    profile_path = Path(profile_dir).absolute()
    profile_path.mkdir(parents=True, exist_ok=True)

    chrome_path = find_chrome_path()
    print(f"[ChromeManager] 使用 Chrome: {chrome_path}")

    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_path}",
        f"--window-position={position[0]},{position[1]}",
        f"--window-size={size[0]},{size[1]}",
        "--new-window",
        url
    ]

    print(f"[ChromeManager] 执行命令: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, shell=False)
    time.sleep(5)

    # 用平台名称找窗口（如"豆包"）
    hwnd = find_window_by_title(platform_name)
    print(f"[ChromeManager] 用平台名 '{platform_name}' 找到窗口句柄: {hwnd}")

    # 如果没找到，列出所有 Chrome 窗口
    if not hwnd:
        print("[ChromeManager] 未找到匹配窗口，列出所有 Chrome 窗口:")
        for h, t in list_all_chrome_windows():
            print(f"  - {t}")

    if hwnd:
        bring_window_to_front(hwnd)

    time.sleep(1)
    return proc, hwnd


def get_cdp_ws_url(port):
    """获取 CDP WebSocket URL"""
    try:
        resp = requests.get(f"http://localhost:{port}/json", timeout=3)
        tabs = resp.json()
        if tabs:
            return tabs[0].get("webSocketDebuggerUrl")
    except Exception:
        pass
    return None


def get_window_rect(hwnd):
    """获取窗口矩形"""
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)