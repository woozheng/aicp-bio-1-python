# plugins/builtins/channels/email_channel.py
"""邮件渠道 — 收取邮件 → main_agent → 回复"""
import asyncio
import json
import re
import imaplib
import email as email_lib
from email.policy import default
from pathlib import Path
from datetime import datetime
from collections import deque
import core
from plugins.builtins.agents.config import get_channel_config

PROJECT = Path(__file__).parent.name

_processed_mail_ids = deque(maxlen=1000)
_MY_EMAILS = set()

SUBJECT_MARKERS = ["[AI]", "[助手]", "[处理]", "[任务]"]
BODY_MARKERS = ["@ai", "@助手", "@处理", "@任务"]


# ==================== 工具 ====================
def _extract_email(sender):
    match = re.search(r'<([^>]+@[^>]+)>', sender)
    return match.group(1).lower() if match else sender.lower()


def _is_system_reply(subject):
    for marker in ["✅ 任务完成", "❌ 任务失败", "⏰ 定时:", "任务结果 ["]:
        if marker in (subject or ""):
            return True
    return False


def _should_process(subject, body):
    for m in SUBJECT_MARKERS:
        if m.lower() in (subject or "").lower():
            return True
    for m in BODY_MARKERS:
        if m.lower() in (body or "").lower():
            return True
    return False


def _parse_email(raw_data):
    msg = email_lib.message_from_bytes(raw_data, policy=default)
    subject = msg["Subject"] or ""
    sender = msg["From"] or ""
    body = ""
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            if "attachment" in str(part.get("Content-Disposition", "")):
                filename = part.get_filename()
                if filename:
                    attachments.append({"filename": filename, "data": part.get_payload(decode=True)})
            elif part.get_content_type() == "text/plain" and not body:
                try:
                    raw = part.get_payload(decode=True)
                    for enc in ["utf-8", "gbk", "gb2312", "gb18030", "latin-1"]:
                        try:
                            body = raw.decode(enc)
                            if body and len(body.strip()) > 0:
                                break
                        except:
                            continue
                except:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
        except:
            pass

    return {"subject": subject, "sender": sender, "body": body, "attachments": attachments}


def _find_inbox(imap):
    status, folders = imap.list()
    if status != "OK":
        return "INBOX"
    candidates = []
    for folder in folders:
        try:
            raw = folder.decode("utf-8", errors="ignore") if isinstance(folder, bytes) else str(folder)
            parts = raw.split(' "/" ')
            name = parts[-1].strip('"').strip("'") if len(parts) >= 2 else raw.split()[-1].strip('"').strip("'")
            if name.lower() == "inbox":
                candidates.insert(0, name)
            elif "inbox" in name.lower() or "收件箱" in name:
                candidates.append(name)
        except:
            pass
    return candidates[0] if candidates else "INBOX"


# ==================== 邮件接收循环 ====================
async def email_loop(agent):
    await asyncio.sleep(15)
    while True:
        cfg = get_channel_config("email")
        if cfg.get("enabled") and cfg.get("imap_host") and cfg.get("user"):
            try:
                await _check_email(agent, cfg)
            except Exception as e:
                print(f"[email_channel] 检查失败: {e}")
        await asyncio.sleep(cfg.get("check_interval", 60))


async def _check_email(agent, cfg):
    imap = None
    try:
        _MY_EMAILS.add(cfg["user"].lower())

        print(f"[email_channel] 连接: {cfg['imap_host']}:{cfg['imap_port']}")
        imap = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
        imap.login(cfg["user"], cfg["password"])

        inbox_name = _find_inbox(imap)
        status, _ = imap.select(inbox_name)
        if status != "OK":
            imap.logout()
            return

        status, msg_nums = imap.search(None, "UNSEEN")
        if status != "OK" or not msg_nums[0]:
            imap.logout()
            return

        unseen_ids = msg_nums[0].decode().split()
        processed_nums = []

        for num in unseen_ids[-5:]:
            try:
                res, data = imap.fetch(num, "(RFC822)")
                if res != "OK":
                    processed_nums.append(num)
                    continue

                parsed = _parse_email(data[0][1])
                sender_email = _extract_email(parsed["sender"])

                if sender_email in _MY_EMAILS:
                    processed_nums.append(num)
                    continue
                if _is_system_reply(parsed["subject"]):
                    processed_nums.append(num)
                    continue

                mail_id = f"{sender_email}_{num}_{parsed['subject']}_{parsed['body'][:100]}"
                if mail_id in _processed_mail_ids:
                    processed_nums.append(num)
                    continue
                _processed_mail_ids.append(mail_id)

                if not parsed["body"].strip():
                    processed_nums.append(num)
                    continue
                if not _should_process(parsed["subject"], parsed["body"]):
                    processed_nums.append(num)
                    continue

                attachments_info = ""
                uploaded_files = []
                if parsed["attachments"]:
                    from plugins.builtins.constants import MEMORIES_DIR
                    upload_dir = MEMORIES_DIR / "uploads"
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    for att in parsed["attachments"]:
                        (upload_dir / att["filename"]).write_bytes(att["data"])
                        uploaded_files.append({"name": att["filename"], "path": str(upload_dir / att["filename"])})
                        attachments_info += f"\n  - {att['filename']}"

                user_content = f"[邮件主题: {parsed['subject']}]\n{parsed['body']}{attachments_info}"

                result = await agent.system.call(core.Envelop(
                    sender=f"channels/email",
                    receiver="builtins/agents/main_agent",
                    payload={
                        "content": user_content,
                        "session_id": sender_email,
                        "channel": "email",
                        "files": uploaded_files,
                        "_sender": sender_email
                    }
                ))

                if result:
                    payload = result.payload
                    if payload.get("type") in ("chat", "error"):
                        from plugins.builtins.notify.email_notify import send as email_send
                        await email_send(agent, sender_email,
                                         f"Re: {parsed['subject']}",
                                         payload.get("content", ""))

                processed_nums.append(num)

            except Exception as e:
                print(f"[email_channel] 处理 #{num} 异常: {e}")
                processed_nums.append(num)
                continue

        if processed_nums:
            for num in processed_nums:
                try:
                    imap.store(num, '+FLAGS', '\\Seen')
                except:
                    pass

        imap.logout()

    except Exception as e:
        print(f"[email_channel] 检查异常: {e}")
        if imap:
            try:
                imap.logout()
            except:
                pass


# ==================== 手动操作接口 ====================
async def check_now(agent) -> dict:
    cfg = get_channel_config("email")
    if not cfg.get("enabled"):
        return {"ok": False, "error": "邮箱未启用"}
    try:
        await _check_email(agent, cfg)
        return {"ok": True, "message": "检查完成"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def test_send(agent) -> dict:
    cfg = get_channel_config("email")
    if not cfg.get("user"):
        return {"ok": False, "error": "请先配置邮箱"}
    from plugins.builtins.notify.email_notify import send as email_send
    ok = await email_send(agent, cfg["user"],
                          "🎉 测试邮件",
                          f"通道配置成功！\n时间: {datetime.now().isoformat()}")
    return {"ok": ok, "message": "测试邮件已发送" if ok else "发送失败"}