import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
from fastapi import HTTPException, Request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from .config import settings
from .db import transaction

def verify_telegram(init_data: str) -> dict:
    pairs=parse_qsl(init_data,keep_blank_values=True)
    values = dict(pairs)
    if len(values)!=len(pairs):
        raise HTTPException(401,'Invalid Telegram authentication')
    received = values.pop("hash", "")
    try:
        date = int(values["auth_date"])
        user = json.loads(values["user"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(401, "Invalid Telegram authentication")
    if not received or abs(time.time() - date) > 600:
        raise HTTPException(401, "Expired Telegram authentication")
    message = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", settings()["BOT_TOKEN"].encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received, expected):
        raise HTTPException(401, "Invalid Telegram authentication")
    if not isinstance(user,dict) or type(user.get('id')) is not int or user['id']<=0:
        raise HTTPException(401,'Invalid Telegram authentication')
    return user

def serializer():
    return URLSafeTimedSerializer(settings()["SESSION_SECRET"], salt="alart-session")

def issue_session(user_id: int, admin: bool = False):
    return serializer().dumps({"id": user_id, "admin": admin})

def current_user(request: Request) -> dict:
    token = request.cookies.get("alart_session")
    try:
        data = serializer().loads(token or "", max_age=86400)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "Authentication required")
    with transaction() as conn:
        user = conn.execute("SELECT u.id,u.telegram_id,u.name,u.language,a.role FROM users u LEFT JOIN admins a ON a.user_id=u.id WHERE u.id=%s", (data["id"],)).fetchone()
    if not user or (data.get("admin") and not user["role"]):
        raise HTTPException(401, "Authentication required")
    return user

def require_admin(request: Request) -> dict:
    user = current_user(request)
    if not user["role"]:
        raise HTTPException(403, "Admin access required")
    return user

def require_owner(request: Request) -> dict:
    user = require_admin(request)
    if user["role"] != "owner":
        raise HTTPException(403, "Owner access required")
    return user

def require_operator(request: Request) -> dict:
    user = require_admin(request)
    if user["role"] not in ("owner", "operator"):
        raise HTTPException(403, "Operator access required")
    return user

def csrf(request: Request):
    origin = request.headers.get("origin", "")
    domain = settings().get("DOMAIN", "")
    if domain and origin != f"https://{domain}":
        raise HTTPException(403, "Invalid origin")
