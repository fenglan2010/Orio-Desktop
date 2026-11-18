"""Password hashing and JWT helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"pbkdf2${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return hmac.compare_digest(dk.hex(), digest)


SCOPE_HOTEL = "hotel"
SCOPE_OPS = "ops"
ROLE_OPERATOR = "operator"
# Legacy "operator" plus the internal-staff roles. Hotel tenant roles stay separate.
OPS_ROLE_NAMES = frozenset(
    {
        "operator",
        "ops_admin",
        "ops_contract",
        "ops_sysops",
        "ops_operations",
        "ops_support",
    }
)


def is_operator_user(user: User | None) -> bool:
    return bool(user is not None and getattr(user, "role", "") in OPS_ROLE_NAMES)


def create_token(user: User, hotel_id: int | None = None, *, scope: str = SCOPE_HOTEL) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "exp": now + timedelta(hours=settings.jwt_expire_hours),
        "iat": now,
        "scope": scope,
    }
    if hotel_id is not None:
        payload["hotel_id"] = int(hotel_id)
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    if getattr(user, "disabled", False):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号已停用")
    if payload.get("scope") == SCOPE_OPS or is_operator_user(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="运维账号不能访问酒店端")
    return user


def get_current_operator(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    if payload.get("scope") != SCOPE_OPS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请使用运维账号登录")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    if getattr(user, "disabled", False):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号已停用")
    if not is_operator_user(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请使用运维账号登录")
    return user
