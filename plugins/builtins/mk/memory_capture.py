"""
memory_capture.py — 一键存入记忆
Ctrl+F11：存剪贴板
Ctrl+F12：存截屏
"""

import asyncio
import os
import base64
import re
import time
import io
import shutil
import hashlib
import ctypes
from io import BytesIO
from pathlib import Path
from datetime import datetime
from typing import Tuple

import pyperclip
import pyautogui
from PIL import Image, ImageGrab

from .state import get_state


MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB 文件上限
MAX_CONTENT_SIZE = 50000  # 50KB 以上存磁盘


# ════════════════════════════════════════════════════════════════
# 系统通知
# ════════════════════════════════════════════════════════════════

def _show_notification(title, message, duration=3):
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(title, message, duration=duration, threaded=True)
    except:
        print(f"[MK] {title}: {message}")


# ════════════════════════════════════════════════════════════════
# 获取活动窗口信息
# ════════════════════════════════════════════════════════════════

def _get_active_window_info():
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        class_name = win32gui.GetClassName(hwnd)
        return {"hwnd": hwnd, "title": title, "class_name": class_name}
    except:
        return {"hwnd": None, "title": "未知窗口", "class_name": "unknown"}


# ════════════════════════════════════════════════════════════════
# 剪贴板内容类型检测
# ════════════════════════════════════════════════════════════════

def get_clipboard_content():
    """
    检测剪贴板内容，返回 (type, data)
    type: 'file', 'image', 'text', 'empty'
    """
    try:
        import win32clipboard
        import win32con
    except ImportError:
        text = pyperclip.paste()
        if text and text.strip():
            return ('text', text)
        return ('empty', None)
    
    try:
        win32clipboard.OpenClipboard()
        try:
            # 1. 检测文件
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_HDROP)
                print(f"[MK] CF_HDROP 数据: {data}")
                
                if isinstance(data, tuple):
                    files = list(data)
                    if files:
                        return ('file', files)
                
                if isinstance(data, int):
                    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
                    DragQueryFile = shell32.DragQueryFileW
                    hdrop_ptr = ctypes.c_void_p(data)
                    count = DragQueryFile(hdrop_ptr, 0xFFFFFFFF, None, 0)
                    files = []
                    for i in range(count):
                        length = DragQueryFile(hdrop_ptr, i, None, 0)
                        if length > 0:
                            buf = ctypes.create_unicode_buffer(length + 1)
                            DragQueryFile(hdrop_ptr, i, buf, length + 1)
                            files.append(buf.value)
                    if files:
                        return ('file', files)
            
            # 2. 检测图片
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_DIB)
                if data:
                    try:
                        import io
                        from PIL import Image
                        bmp_data = b'BM' + len(data).to_bytes(4, 'little') + b'\x00\x00\x00\x00' + b'\x36\x04\x00\x00' + data
                        img = Image.open(io.BytesIO(bmp_data))
                        return ('image', img)
                    except:
                        return ('image', data)
            
            # 3. 检测文本
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                if data:
                    return ('text', data)
            
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_TEXT):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT)
                if data:
                    return ('text', data.decode('gbk', errors='ignore'))
                    
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        print(f"[MK] 剪贴板检测失败: {e}")
        import traceback
        traceback.print_exc()
    
    text = pyperclip.paste()
    if text and text.strip():
        return ('text', text)
    
    return ('empty', None)


# ════════════════════════════════════════════════════════════════
# 处理文件内容
# ════════════════════════════════════════════════════════════════

def process_file(file_path: str, kb_dir: Path = None) -> Tuple[str, str, dict, str]:
    """
    处理文件：存磁盘 + 生成摘要 + 返回元数据
    """
    path = Path(file_path)
    if not path.exists():
        return "", "文件不存在", {}, "file_info"
    
    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        return "", f"文件太大 ({path.name} > 50MB)", {}, "file_info"
    
    if kb_dir is None:
        kb_dir = Path("data/knowledge_base")
    kb_dir.mkdir(parents=True, exist_ok=True)
    
    file_id = hashlib.md5(f"{path.name}_{path.stat().st_mtime}".encode()).hexdigest()[:12]
    file_dir = kb_dir / "files" / file_id
    file_dir.mkdir(parents=True, exist_ok=True)
    
    ext = path.suffix.lower()
    content_type = "file"
    metadata = {
        "file_id": file_id,
        "file_name": path.name,
        "file_size": size,
        "file_type": ext,
        "original_path": str(path.absolute())
    }
    
    # ============================================================
    # 1. 图片处理
    # ============================================================
    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.ico']:
        try:
            from PIL import Image
            
            shutil.copy2(path, file_dir / path.name)
            metadata["file_path"] = str(file_dir / path.name)
            metadata["file_relative"] = f"files/{file_id}/{path.name}"
            
            img = Image.open(path)
            thumb = img.copy()
            thumb.thumbnail((200, 200), Image.LANCZOS)
            thumb_path = file_dir / "thumb.jpg"
            thumb.convert("RGB").save(thumb_path, "JPEG", quality=70)
            metadata["thumb_path"] = str(thumb_path)
            metadata["thumb_relative"] = f"files/{file_id}/thumb.jpg"
            metadata["image_size"] = img.size
            
            content = f"图片: {path.name} ({img.size[0]}x{img.size[1]})"
            summary = f"图片: {path.name}"
            content_type = "image"
            
            return content, summary, metadata, content_type
            
        except Exception as e:
            print(f"[MK] 图片处理失败: {e}")
            return "", f"图片处理失败: {e}", metadata, "file_info"
    
    # ============================================================
    # 2. 文本文件
    # ============================================================
    if ext in ['.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.xml', '.yaml', '.yml', '.csv', '.log', '.sql', '.go', '.rs', '.c', '.cpp', '.h']:
        try:
            content = ""
            for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                try:
                    with open(path, 'r', encoding=encoding) as f:
                        content = f.read()
                    break
                except:
                    continue
            
            if content:
                shutil.copy2(path, file_dir / path.name)
                metadata["file_path"] = str(file_dir / path.name)
                metadata["file_relative"] = f"files/{file_id}/{path.name}"
                
                summary = content[:200].strip().replace('\n', ' ') + "..."
                if len(content) <= MAX_CONTENT_SIZE:
                    content_type = "text"
                    return content, summary, metadata, content_type
                else:
                    content_type = "file"
                    return f"文本文件: {path.name} ({len(content)} 字符)", summary, metadata, content_type
        except Exception as e:
            print(f"[MK] 文本读取失败: {e}")
            return "", f"文本读取失败: {e}", metadata, "file_info"
    
    # ============================================================
    # 3. PDF / Word
    # ============================================================
    if ext in ['.pdf']:
        try:
            import PyPDF2
            with open(path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                text = ""
                for page in reader.pages[:10]:
                    text += page.extract_text() or ""
            
            shutil.copy2(path, file_dir / path.name)
            metadata["file_path"] = str(file_dir / path.name)
            metadata["file_relative"] = f"files/{file_id}/{path.name}"
            
            summary = text[:200].strip().replace('\n', ' ') + "..." if text else f"PDF: {path.name}"
            content = f"PDF: {path.name} ({len(reader.pages)} 页)"
            content_type = "file"
            return content, summary, metadata, content_type
        except ImportError:
            pass
        except Exception as e:
            print(f"[MK] PDF 处理失败: {e}")
    
    if ext in ['.docx', '.doc']:
        try:
            import docx
            doc = docx.Document(path)
            text = "\n".join([p.text for p in doc.paragraphs[:50]])
            
            shutil.copy2(path, file_dir / path.name)
            metadata["file_path"] = str(file_dir / path.name)
            metadata["file_relative"] = f"files/{file_id}/{path.name}"
            
            summary = text[:200].strip().replace('\n', ' ') + "..." if text else f"Word: {path.name}"
            content = f"Word: {path.name}"
            content_type = "file"
            return content, summary, metadata, content_type
        except ImportError:
            pass
        except Exception as e:
            print(f"[MK] Word 处理失败: {e}")
    
    # ============================================================
    # 4. 其他文件：只存路径
    # ============================================================
    shutil.copy2(path, file_dir / path.name)
    metadata["file_path"] = str(file_dir / path.name)
    metadata["file_relative"] = f"files/{file_id}/{path.name}"
    
    content = f"文件: {path.name} ({size // 1024}KB)"
    summary = f"文件: {path.name}"
    content_type = "file"
    
    return content, summary, metadata, content_type


async def process_file_async(file_path: str, agent=None, kb_dir: Path = None):
    """异步处理文件（支持 Vision Agent 生成描述）"""
    content, summary, metadata, content_type = process_file(file_path, kb_dir)
    
    # 🔥 如果 metadata 是空的或者没有 file_id，直接返回
    if not metadata or not metadata.get("file_id"):
        print(f"[MK] process_file_async: metadata 缺少 file_id, metadata={metadata}")
        return content, summary, metadata, content_type
    
    # 如果有 agent，用 Vision 生成图片描述
    if agent and metadata.get("image_size") and hasattr(agent, 'llm'):
        try:
            import core
            from PIL import Image
            import io
            
            img_path = Path(metadata["file_path"])
            if img_path.exists():
                img = Image.open(img_path)
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=80)
                img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                
                result = await agent.system.call(core.Envelop(
                    sender="builtins/mk/memory_capture",
                    receiver="builtins/agents/vision",
                    payload={
                        "image": img_base64,
                        "intent": "描述这张图片的内容，一句话概括，不超过30字",
                        "channel": f"vision_desc_{int(datetime.now().timestamp())}",
                        "reset": True,
                        "allow_actions": False,
                        "region": {}
                    }
                ))
                if result and result.payload:
                    description = result.payload.get("result", "")
                    if description:
                        metadata["description"] = description
                        summary = description[:50]
                        content = f"图片: {Path(file_path).name} - {description}"
        except Exception as e:
            print(f"[MK] Vision 描述生成失败: {e}")
    
    # 🔥 打印调试信息
    print(f"[MK] process_file_async 返回: file_id={metadata.get('file_id')}, content_type={content_type}")
    
    return content, summary, metadata, content_type


# ════════════════════════════════════════════════════════════════
# 图片压缩
# ════════════════════════════════════════════════════════════════

def _compress_image(image, max_size=1024, quality=60):
    w, h = image.size
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        new_w = int(w * ratio)
        new_h = int(h * ratio)
        image = image.resize((new_w, new_h), Image.LANCZOS)
    buf = BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode('utf-8')


# ════════════════════════════════════════════════════════════════
# 通用：存入知识库（支持 metadata）
# ════════════════════════════════════════════════════════════════

async def _save_to_knowledge_base(agent, content, summary, source, content_type, query=None, metadata=None):
    import core
    meta = {
        "captured_at": datetime.now().isoformat(),
        "source_type": content_type,
        "source_detail": source,
        "level": 0,
        "summary": summary,
        "query": query or "memory_capture"
    }
    if metadata:
        meta.update(metadata)
    
    result = await agent.system.call(core.Envelop(
        sender="builtins/mk/memory_capture",
        receiver="builtins/knowledge_base/main",
        payload={
            "action": "auto_save",
            "params": {
                "content": content,
                "source": f"mk_memory_{source}",
                "type": content_type,
                "title": summary[:50] if summary else content[:50],
                "metadata": meta
            }
        }
    ))
    return result


# ════════════════════════════════════════════════════════════════
# Ctrl+F11：存剪贴板（支持文件、图片、文本）
# ════════════════════════════════════════════════════════════════

async def memory_capture_clipboard(agent):
    """存剪贴板内容（Ctrl+F11）"""
    print("[MK] 存剪贴板 (Ctrl+F11)")
    
    content_type, data = get_clipboard_content()
    
    if content_type == 'file':
        files = data
        for file_path in files:
            print(f"[MK] 处理文件: {file_path}")
            content, summary, metadata, ctype = await process_file_async(file_path, agent=agent)
            print(f"[MK] 得到的 metadata: {metadata}")  # 🔥 加这行
            if content:
                await _save_to_knowledge_base(
                    agent, 
                    content, 
                    summary, 
                    f"文件: {Path(file_path).name}", 
                    ctype,
                    query="剪贴板文件", 
                    metadata=metadata  # 🔥 确保传了 metadata
                )
                _show_notification("📁 已存入文件", Path(file_path).name[:50])
        return
    
    elif content_type == 'image':
        print("[MK] 检测到剪贴板图片")
        img = data
        try:
            compressed = _compress_image(img, max_size=1024, quality=70)
            await _save_to_knowledge_base(
                agent, 
                compressed, 
                "剪贴板图片", 
                "剪贴板图片", 
                "image",
                query="剪贴板图片"
            )
            _show_notification("🖼️ 已存入图片", "剪贴板图片")
        except Exception as e:
            print(f"[MK] 图片处理失败: {e}")
            _show_notification("❌ 图片处理失败", str(e)[:50])
        return
    
    elif content_type == 'text':
        text = str(data).strip()
        print(f"[MK] 检测到文本: {len(text)} 字符")
        if not text:
            _show_notification("⚠️ 剪贴板为空", "请先复制内容再按 Ctrl+F11")
            return
        
        summary = text[:50] if len(text) > 50 else text
        source = "剪贴板"
        ctype = "text"
        
        if text.startswith(("http://", "https://")):
            ctype = "link"
            summary = f"链接: {text[:50]}"
        
        elif os.path.exists(text):
            path = Path(text)
            content, summary, metadata, ctype = await process_file_async(text, agent=agent)
            if content:
                await _save_to_knowledge_base(
                    agent, content, summary, f"文件: {path.name}", ctype,
                    query="剪贴板文件", metadata=metadata
                )
                _show_notification("📁 已存入文件", path.name[:50])
            return
        
        await _save_to_knowledge_base(
            agent, text, summary, source, ctype, query="剪贴板"
        )
        _show_notification("📝 已存入文本", summary[:50])
        return
    
    else:
        _show_notification("⚠️ 剪贴板为空", "请先复制内容或文件再按 Ctrl+F11")


# ════════════════════════════════════════════════════════════════
# Ctrl+F12：存截屏（Vision 分析 + LLM 摘要）
# ════════════════════════════════════════════════════════════════

async def memory_capture_screenshot(agent):
    """截屏并分析（Ctrl+F12）"""
    print("[MK] 存截屏 (Ctrl+F12)")
    
    window = _get_active_window_info()
    
    try:
        import win32gui
        hwnd = window.get("hwnd")
        if hwnd:
            rect = win32gui.GetWindowRect(hwnd)
            x1, y1, x2, y2 = rect
            screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
        else:
            screenshot = ImageGrab.grab()
    except:
        screenshot = ImageGrab.grab()
    
    img_base64 = _compress_image(screenshot, max_size=1024, quality=60)
    
    import core
    result = await agent.system.call(core.Envelop(
        sender="builtins/mk/memory_capture",
        receiver="builtins/agents/vision",
        payload={
            "image": img_base64,
            "intent": """
请分析这张截图，提取关键信息，按以下结构返回：

类型：浏览器 / 代码编辑器 / 终端 / 文档 / 桌面 / 其他
URL：如果是浏览器，提取完整网址；否则写 无
标题：当前窗口标题或页面标题

关键信息：
- 提取截图中最核心的信息（3-5 个要点）
- 如果是网页，提取主要内容、数据、配置、关键数字
- 如果是代码，提取函数名、逻辑、关键变量
- 如果是文档，提取标题、章节、重点

内容摘要：一段话总结（50字以内）

输出格式：
类型：xxx
URL：xxx
标题：xxx
关键信息：
1. xxx
2. xxx
3. xxx
内容摘要：xxx
""",
            "channel": f"memory_capture_{int(time.time())}",
            "reset": True,
            "allow_actions": False,
            "region": {}
        }
    ))
    
    if result and result.payload:
        raw = result.payload.get("result", "")
        thinking = result.payload.get("thinking", "")
        
        type_match = re.search(r"类型[：:]\s*(.+)", raw)
        url_match = re.search(r"URL[：:]\s*(.+?)(?:\n|$)", raw)
        title_match = re.search(r"标题[：:]\s*(.+?)(?:\n|$)", raw)
        key_info_match = re.search(r"关键信息[：:]\s*(.*?)(?=\n内容摘要|$)", raw, re.DOTALL)
        summary_match = re.search(r"内容摘要[：:]\s*(.+)", raw)
        
        source_type = type_match.group(1).strip() if type_match else "未知"
        url = url_match.group(1).strip() if url_match else ""
        title = title_match.group(1).strip() if title_match else window.get("title", "")
        key_info = key_info_match.group(1).strip() if key_info_match else "无关键信息"
        summary = summary_match.group(1).strip() if summary_match else "截图内容"
        
        content = f"类型：{source_type}\nURL：{url}\n标题：{title}\n\n关键信息：\n{key_info}"
        
        if len(content) < 20 and thinking:
            content = thinking
            if not summary:
                summary = content[:50]
        
        source = f"{source_type}: {title if title else '截图'}"
    else:
        content = f"截图: {window.get('title', '未知窗口')}"
        summary = content[:50]
        source = content
    
    await _save_to_knowledge_base(
        agent, content, summary, source, "screenshot", query="截屏分析"
    )
    _show_notification("📸 已存入截图", summary[:50])