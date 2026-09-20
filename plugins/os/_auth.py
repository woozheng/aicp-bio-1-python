"""认证插件 — 协议 v3.0 系统插件
支持 enable_auth 开关，关闭时所有请求放行。
开启时验证 X-AICP-Token / Authorization Bearer / Cookie。
"""
import core
import hmac
import hashlib
import secrets
import json


async def execute(envelop, agent):
    action = envelop.payload.get("action", "verify")
    
    if action == "verify":
        return await _verify(envelop, agent)
    elif action == "login":
        return await _login(envelop, agent)
    elif action == "logout":
        return await _logout(envelop, agent)
    elif action == "generate_app_token":
        return await _generate_app_token(envelop, agent)
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop


async def _verify(envelop, agent):
    """统一认证验证"""
    
    # ★ 来自子目录应用的请求，直接放行
    if envelop.meta.get("is_app_request"):
        envelop.payload = {"ok": True, "message": "App request allowed"}
        return envelop
    
    # 读取配置
    enable_auth = agent.config.get("enable_auth", False)
    if not enable_auth:
        envelop.payload = {"ok": True, "message": "Auth disabled"}
        return envelop
    
    route = envelop.payload.get("route", envelop.receiver)
    
    # 公开路由
    public_routes = {"auth/login", "ws_config", "health","applications/gitee_manager/gitee_webhook", }# ★ 加这个}
    if route in public_routes:
        envelop.payload = {"ok": True}
        return envelop
    
    # 回调签名验证（由 Gateway 处理，这里也支持）
    if envelop.meta.get("is_callback"):
        signature = envelop.meta.get("signature", "")
        if hasattr(core, 'verify_signature') and core.verify_signature(envelop.payload, signature):
            envelop.payload = {"ok": True, "message": "Callback signature verified"}
            return envelop
    
    # 提取 token
    token = envelop.meta.get("token", "")
    if not token:
        auth_header = envelop.meta.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        token = envelop.meta.get("cookie_token", "")
    
    # 验证 token
    valid_tokens = set(agent.config.get("tokens", []))
    if token and token in valid_tokens:
        envelop.payload = {"ok": True}
        return envelop
    
    # 验证应用 token
    if token:
        secret = agent.config.get("app_secret", "aicp_default_secret")
        # 从 token 反推应用名
        for app_name in _list_app_names():
            expected = _generate_app_token_value(app_name, secret)
            if hmac.compare_digest(token, expected):
                envelop.meta["app_name"] = app_name
                envelop.meta["is_app_request"] = True
                envelop.payload = {"ok": True, "app": app_name}
                return envelop
    
    envelop.payload = {"ok": False, "error": "Unauthorized"}
    return envelop


async def _login(envelop, agent):
    """用户登录"""
    username = envelop.payload.get("username", "")
    password = envelop.payload.get("password", "")
    
    if not username or not password:
        envelop.payload = {"ok": False, "error": "Missing username or password"}
        return envelop
    
    users = agent.config.get("users", {})
    if username in users:
        stored = users[username]
        # 支持明文或哈希密码
        if stored == password or _verify_password(password, stored):
            token = _generate_session_token(username, agent.config.get("token_secret", "aicp_session_secret"))
            envelop.payload = {"ok": True, "token": token, "username": username}
        else:
            envelop.payload = {"ok": False, "error": "Invalid credentials"}
    else:
        envelop.payload = {"ok": False, "error": "Invalid credentials"}
    
    return envelop


async def _logout(envelop, agent):
    """用户登出（无状态，不需要实际操作）"""
    envelop.payload = {"ok": True, "message": "Logged out"}
    return envelop


async def _generate_app_token(envelop, agent):
    """生成应用 token（供 Gateway 或管理端使用）"""
    app_name = envelop.payload.get("app_name", "")
    if not app_name:
        envelop.payload = {"ok": False, "error": "Missing app_name"}
        return envelop
    
    secret = agent.config.get("app_secret", "aicp_default_secret")
    token = _generate_app_token_value(app_name, secret)
    
    envelop.payload = {"ok": True, "app_name": app_name, "token": token}
    return envelop


# ============================================================
# 工具函数
# ============================================================

def _generate_app_token_value(app_name: str, secret: str) -> str:
    """生成应用 token"""
    return hmac.new(secret.encode(), app_name.encode(), hashlib.md5).hexdigest()[:16]


def _generate_session_token(username: str, secret: str) -> str:
    """生成会话 token"""
    nonce = secrets.token_hex(8)
    data = f"{username}:{nonce}"
    signature = hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()[:32]
    return f"tok_{username}_{signature}"


def _verify_password(password: str, stored: str) -> bool:
    """验证密码（支持 sha256 哈希）"""
    if stored.startswith("sha256:"):
        expected = stored[7:]
        actual = hashlib.sha256(password.encode()).hexdigest()
        return hmac.compare_digest(actual, expected)
    return False


def _list_app_names() -> list:
    """列出所有应用名（www/ 目录名）"""
    from pathlib import Path
    www = Path("www")
    if not www.exists():
        return []
    return [d.name for d in www.iterdir() if d.is_dir()]