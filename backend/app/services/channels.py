"""Hotel channel authorization: durable status, encrypted credentials, public views."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.adapters.ota.official_http import (
    PLATFORMS,
    contract_is_configured,
    env_credentials,
    load_official_contract,
    platform_write_enabled,
    public_action_labels,
    public_contract_status,
)
from app.config import get_settings
from app.models import ChannelConnection, SystemSetting

PLATFORM_LABELS = {
    "ctrip": "携程",
    "meituan": "美团",
    "fliggy": "飞猪",
}

STATUS_DISCONNECTED = "disconnected"
STATUS_CREDENTIALS_MISSING = "credentials_missing"
STATUS_AUTHORIZED = "authorized"
STATUS_ERROR = "error"


def _now() -> datetime:
    return datetime.utcnow()


def _fernet() -> Fernet:
    settings = get_settings()
    secret = (settings.oiro_credentials_key or settings.jwt_secret or "oiro-demo-secret-change-me").strip()
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_credentials(payload: dict[str, str]) -> str:
    cleaned = {k: v for k, v in payload.items() if v}
    if not cleaned:
        return ""
    token = _fernet().encrypt(json.dumps(cleaned, ensure_ascii=False).encode("utf-8"))
    return token.decode("ascii")


def decrypt_credentials(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    try:
        raw = _fernet().decrypt(token.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (InvalidToken, ValueError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in data.items() if v is not None and str(v)}


def _get_or_create(db: Session, hotel_id: int, platform: str) -> ChannelConnection:
    platform = platform.lower()
    row = (
        db.query(ChannelConnection)
        .filter(ChannelConnection.hotel_id == hotel_id, ChannelConnection.platform == platform)
        .one_or_none()
    )
    if row is None:
        row = ChannelConnection(
            hotel_id=hotel_id,
            platform=platform,
            status=STATUS_DISCONNECTED,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def merged_credentials(row: ChannelConnection | None, platform: str, db: Session | None = None, hotel_id: int | None = None) -> dict[str, str]:
    stored = decrypt_credentials(row.credentials_encrypted if row else "")
    env = env_credentials(platform)
    ops = load_official_api_credentials(db, hotel_id if hotel_id is not None else (row.hotel_id if row else None), platform)
    merged = dict(env)
    merged.update({k: v for k, v in ops.items() if v})
    # Hotel account/password overlay last, but do not let a hotel password wipe an official API key
    # unless the hotel itself stored a key/token.
    hotel_overlay = {k: v for k, v in stored.items() if v}
    if not (hotel_overlay.get("key") or hotel_overlay.get("token")):
        hotel_overlay.pop("key", None)
        hotel_overlay.pop("token", None)
        # Keep official/env key when the hotel only saved an OTA account and password.
    merged.update(hotel_overlay)
    if row:
        if row.platform_hotel_id and not merged.get("platform_hotel_id"):
            merged["platform_hotel_id"] = row.platform_hotel_id
        if row.platform_account_id and not merged.get("platform_account_id"):
            merged["platform_account_id"] = row.platform_account_id
        if stored.get("account") and not merged.get("platform_account_id"):
            merged["platform_account_id"] = stored["account"]
    return merged


def has_usable_credentials(creds: dict[str, str]) -> bool:
    if (creds.get("key") or creds.get("token") or "").strip():
        return True
    account = (
        creds.get("account")
        or creds.get("username")
        or creds.get("platform_account_id")
        or ""
    ).strip()
    password = (creds.get("password") or creds.get("secret") or "").strip()
    return bool(account and password)


HOTEL_FORBIDDEN_FIELDS = (
    "key", "secret", "token", "session", "password", "account", "username",
    "credentials_encrypted", "write_key", "write_secret", "api_key", "api_secret",
    "official_api_key", "official_api_secret", "headers", "auth_header", "key_header",
    "request_template", "contract", "base_url", "contract_host", "contract_hosts",
    "contract_source", "contract_removable", "write_flag_enabled", "live_ready",
    "platform_hotel_id", "platform_account_id", "adapter", "adapters", "auth",
    "contract_path", "source_path", "path", "has_server_credentials",
    "has_official_credentials", "write_flag_source",
)

HOTEL_CONNECT_HINT = "酒店只需授权本店 OTA 账号和密码；官方契约和接口由运维端配置。打开 live 仍要等契约已启用。"

OPS_CRED_SETTING_PREFIX = "official_api_creds_"


def official_creds_setting_key(platform: str, hotel_id: int) -> str:
    return f"hotel:{int(hotel_id)}:{OPS_CRED_SETTING_PREFIX}{platform.lower()}"


def load_official_api_credentials(db: Session | None, hotel_id: int | None, platform: str) -> dict[str, str]:
    if db is None or hotel_id is None:
        return {}
    row = db.get(SystemSetting, official_creds_setting_key(platform, hotel_id))
    if row is None or not row.value:
        return {}
    return decrypt_credentials(row.value)


def save_official_api_credentials(db: Session, hotel_id: int, platform: str, payload: dict) -> dict[str, str]:
    stored = load_official_api_credentials(db, hotel_id, platform)
    mapping = {
        "key": payload.get("key") or payload.get("api_key") or payload.get("write_key"),
        "secret": payload.get("secret") or payload.get("api_secret") or payload.get("write_secret"),
        "token": payload.get("token"),
        "session": payload.get("session"),
    }
    for field, value in mapping.items():
        if field in payload and payload[field] is not None and str(payload[field]) == "":
            stored.pop(field, None)
        elif value is not None and str(value).strip():
            stored[field] = str(value).strip()
    key = official_creds_setting_key(platform, hotel_id)
    raw = encrypt_credentials(stored)
    row = db.get(SystemSetting, key)
    if not raw:
        if row is not None:
            db.delete(row)
            db.commit()
        return {}
    if row is None:
        db.add(SystemSetting(key=key, value=raw))
    else:
        row.value = raw
        row.updated_at = _now()
    db.commit()
    return stored


def sanitize_hotel_error(message: str | None) -> str:
    text = (message or "").strip()
    if not text:
        return ""
    text = re.sub(r"https?://\S+", "官方接口", text)
    text = re.sub(r"(?i)\b[A-Z0-9_]*OFFICIAL_CONTRACT[A-Z0-9_]*\b", "官方契约配置", text)
    text = re.sub(r"(?i)\b[A-Z0-9_]+_WRITE_(ENABLED|KEY|SECRET)\b", "写入配置", text)
    text = re.sub(r"(?i)(/[\w.-]+)+\.(json|pem|env|txt|key)", "服务器文件", text)
    return text


def strip_hotel_channel_fields(body: dict) -> dict:
    for forbidden in HOTEL_FORBIDDEN_FIELDS:
        body.pop(forbidden, None)
    return body


@dataclass
class LiveWriteContext:
    platform: str
    hotel_id: int | None
    status: str
    authorized: bool
    has_credentials: bool
    write_enabled: bool
    contract: dict | None
    credentials: dict[str, str]
    refuse_reason: str | None = None
    refuse_message: str | None = None

    def can_send(self, action: str) -> bool:
        return (
            not self.refuse_reason
            and self.authorized
            and self.has_credentials
            and self.write_enabled
            and contract_is_configured(self.contract, action)
        )


def resolve_live_write_context(db: Session | None, hotel_id: int | None, platform: str) -> LiveWriteContext:
    platform = (platform or "ctrip").lower()
    row = None
    if db is not None and hotel_id is not None:
        row = (
            db.query(ChannelConnection)
            .filter(ChannelConnection.hotel_id == hotel_id, ChannelConnection.platform == platform)
            .one_or_none()
        )
    creds = merged_credentials(row, platform, db, hotel_id)
    contract = load_official_contract(platform, db, hotel_id=hotel_id)
    write_enabled = platform_write_enabled(platform, db, hotel_id)
    has_creds = has_usable_credentials(creds)
    authorized = bool(row and row.status == STATUS_AUTHORIZED and has_creds)
    if not authorized and has_creds and row is None:
        # Env-only credentials (ops-provided) can authorize a live write without a UI connection,
        # but still require contract + write flag. Hotel UI connections remain the normal path.
        authorized = True
        status = STATUS_AUTHORIZED
    else:
        status = row.status if row else (STATUS_AUTHORIZED if authorized else STATUS_DISCONNECTED)

    refuse_reason = None
    refuse_message = None
    if not has_creds:
        refuse_reason = "missing_credentials"
        refuse_message = "缺少有效写入凭证，已拒绝真实写入，未发起外部请求。"
    elif not authorized:
        refuse_reason = "channel_unauthorized"
        refuse_message = "该渠道尚未授权，已拒绝真实写入，未发起外部请求。"
    elif not write_enabled:
        refuse_reason = "write_disabled"
        refuse_message = "服务器未开启该渠道写入开关，已拒绝真实写入，未发起外部请求。"
    elif not contract_is_configured(contract):
        refuse_reason = "missing_contract"
        refuse_message = "未配置官方写入契约（base URL / 路径 / 映射），已拒绝真实写入，未发起外部请求。本仓库默认不内置携程/美团/飞猪生产环境 URL。"

    return LiveWriteContext(
        platform=platform,
        hotel_id=hotel_id,
        status=status,
        authorized=authorized,
        has_credentials=has_creds,
        write_enabled=write_enabled,
        contract=contract,
        credentials=creds,
        refuse_reason=refuse_reason,
        refuse_message=refuse_message,
    )


def public_channel(db: Session, hotel_id: int, platform: str) -> dict[str, Any]:
    """Hotel-facing channel status. Never includes hosts, sources, secrets, or write-flag names."""
    platform = platform.lower()
    row = (
        db.query(ChannelConnection)
        .filter(ChannelConnection.hotel_id == hotel_id, ChannelConnection.platform == platform)
        .one_or_none()
    )
    contract = load_official_contract(platform, db, hotel_id=hotel_id)
    stored = decrypt_credentials(row.credentials_encrypted if row else "")
    configured = contract_is_configured(contract)
    actions = list((contract or {}).get("actions") or {}) if configured else []
    labels = public_action_labels(actions)
    status = row.status if row else STATUS_DISCONNECTED
    from app.services.local_integrations import get_ota_contract_path

    contract_path = get_ota_contract_path(db, hotel_id, platform) if db is not None else ""
    body = {
        "platform": platform,
        "label": PLATFORM_LABELS.get(platform, platform),
        "status": status,
        "authorized_at": row.authorized_at.isoformat() if row and row.authorized_at else None,
        "last_verified": row.last_verified.isoformat() if row and row.last_verified else None,
        "has_credentials": has_usable_credentials(stored),
        "contract_enabled": configured,
        "contract_file_path": contract_path,
        "open_actions": labels,
        "supported_actions": labels,
        "last_error": sanitize_hotel_error((row.last_error if row else "") or ""),
        "connect_hint": HOTEL_CONNECT_HINT,
    }
    return strip_hotel_channel_fields(body)


def ops_public_channel(db: Session, hotel_id: int, platform: str) -> dict[str, Any]:
    """Operator view: may include contract host/source and write-flag state, never secrets."""
    platform = platform.lower()
    row = (
        db.query(ChannelConnection)
        .filter(ChannelConnection.hotel_id == hotel_id, ChannelConnection.platform == platform)
        .one_or_none()
    )
    contract = load_official_contract(platform, db, hotel_id=hotel_id)
    env = env_credentials(platform)
    stored = decrypt_credentials(row.credentials_encrypted if row else "")
    official = load_official_api_credentials(db, hotel_id, platform)
    creds = merged_credentials(row, platform, db, hotel_id)
    write_flag = platform_write_enabled(platform, db, hotel_id)
    configured = contract_is_configured(contract)
    contract_meta = public_contract_status(platform, db, hotel_id=hotel_id)
    action_ids = contract_meta.get("supported_actions") or []
    labels = contract_meta.get("action_labels") or public_action_labels(action_ids)
    status = row.status if row else STATUS_DISCONNECTED
    live_ready = (
        status == STATUS_AUTHORIZED
        and has_usable_credentials(creds)
        and write_flag
        and configured
    )
    body = {
        "hotel_id": hotel_id,
        "platform": platform,
        "label": PLATFORM_LABELS.get(platform, platform),
        "status": status,
        "authorized_at": row.authorized_at.isoformat() if row and row.authorized_at else None,
        "last_verified": row.last_verified.isoformat() if row and row.last_verified else None,
        "has_hotel_credentials": has_usable_credentials(stored),
        "has_official_credentials": has_usable_credentials(official) or has_usable_credentials(env),
        "contract_enabled": configured,
        "contract_configured": configured,
        "contract_source": contract_meta["contract_source"],
        "contract_removable": contract_meta["contract_removable"],
        "contract_host": contract_meta["contract_host"],
        "contract_hosts": contract_meta["contract_hosts"],
        "supported_actions": action_ids,
        "action_labels": labels,
        "open_actions": labels,
        "write_flag_enabled": write_flag,
        "live_ready": live_ready,
        "last_error": sanitize_hotel_error((row.last_error if row else "") or ""),
        "connect_hint": "官方契约、接口地址和写入开关在运维端配置；酒店只需授权本店 OTA 账号和密码。安装契约本身不会发起出站写入。示例契约不会自动加载，本仓库不编造生产 URL。",
    }
    for forbidden in (
        "key", "secret", "token", "session", "password", "account", "username",
        "credentials_encrypted", "write_key", "write_secret", "api_key", "api_secret",
        "contract", "headers", "auth_header", "key_header", "request_template", "base_url",
    ):
        body.pop(forbidden, None)
    return body


def list_public_channels(db: Session, hotel_id: int) -> list[dict[str, Any]]:
    return [public_channel(db, hotel_id, platform) for platform in PLATFORMS]


def list_ops_channels(db: Session, hotel_id: int) -> list[dict[str, Any]]:
    return [ops_public_channel(db, hotel_id, platform) for platform in PLATFORMS]


def _secret_fields(payload: dict) -> dict[str, str]:
    mapping = {
        "key": payload.get("key") or payload.get("api_key") or payload.get("write_key"),
        "secret": payload.get("secret") or payload.get("api_secret") or payload.get("write_secret"),
        "session": payload.get("session"),
        "token": payload.get("token"),
    }
    return {k: str(v).strip() for k, v in mapping.items() if v is not None and str(v).strip()}


def save_channel_connection(db: Session, hotel_id: int, platform: str, payload: dict) -> ChannelConnection:
    platform = platform.lower()
    if platform not in PLATFORMS:
        raise ValueError("仅支持携程、美团、飞猪渠道授权")
    row = _get_or_create(db, hotel_id, platform)
    if "platform_hotel_id" in payload and payload["platform_hotel_id"] is not None:
        row.platform_hotel_id = str(payload["platform_hotel_id"]).strip()
    if "platform_account_id" in payload and payload["platform_account_id"] is not None:
        row.platform_account_id = str(payload["platform_account_id"]).strip()

    stored = decrypt_credentials(row.credentials_encrypted)
    account = payload.get("account") if "account" in payload else payload.get("username") if "username" in payload else None
    if account is not None:
        value = str(account).strip()
        if value:
            stored["account"] = value
            if not row.platform_account_id:
                row.platform_account_id = value
        else:
            stored.pop("account", None)
    if "password" in payload and payload["password"] is not None:
        if str(payload["password"]) == "":
            stored.pop("password", None)
        else:
            stored["password"] = str(payload["password"])

    provided = _secret_fields(payload)
    # Empty string on a secret field means "clear this field". Omitted means keep.
    # Kept for backward compatibility with previously stored key/secret/account ids.
    for field in ("key", "secret", "session", "token"):
        if field in payload and payload[field] is not None and str(payload[field]) == "":
            stored.pop(field, None)
        elif field in provided:
            stored[field] = provided[field]
    if row.platform_hotel_id:
        stored["platform_hotel_id"] = row.platform_hotel_id
    if row.platform_account_id:
        stored["platform_account_id"] = row.platform_account_id

    row.credentials_encrypted = encrypt_credentials(stored)
    usable = has_usable_credentials(stored) or has_usable_credentials(merged_credentials(row, platform, db, hotel_id))
    if usable:
        row.status = STATUS_AUTHORIZED
        if row.authorized_at is None:
            row.authorized_at = _now()
        row.last_error = ""
    else:
        row.status = STATUS_CREDENTIALS_MISSING
        row.authorized_at = None
        row.last_error = "已保存连接，但还没有可用的账号和密码。"
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def disconnect_channel(db: Session, hotel_id: int, platform: str) -> ChannelConnection:
    row = _get_or_create(db, hotel_id, platform)
    row.credentials_encrypted = ""
    row.status = STATUS_DISCONNECTED
    row.authorized_at = None
    row.last_verified = None
    row.last_error = ""
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def mark_channel_verified(db: Session, hotel_id: int, platform: str, *, error: str | None = None) -> None:
    row = (
        db.query(ChannelConnection)
        .filter(ChannelConnection.hotel_id == hotel_id, ChannelConnection.platform == platform)
        .one_or_none()
    )
    if row is None:
        return
    if error:
        row.status = STATUS_ERROR
        row.last_error = error
    else:
        if has_usable_credentials(decrypt_credentials(row.credentials_encrypted)) or has_usable_credentials(merged_credentials(row, platform)):
            row.status = STATUS_AUTHORIZED
            if row.authorized_at is None:
                row.authorized_at = _now()
        row.last_verified = _now()
        row.last_error = ""
    row.updated_at = _now()
    db.commit()


def verify_channel(db: Session, hotel_id: int, platform: str) -> dict[str, Any]:
    """Check authorization. Opens a socket only when a contract verify/write path is configured."""
    platform = platform.lower()
    row = _get_or_create(db, hotel_id, platform)
    ctx = resolve_live_write_context(db, hotel_id, platform)
    if not ctx.has_credentials:
        row.status = STATUS_CREDENTIALS_MISSING
        row.last_error = "还没有可用的账号和密码，无法校验。"
        row.updated_at = _now()
        db.commit()
        return public_channel(db, hotel_id, platform)

    contract = ctx.contract
    verify_action = None
    if contract_is_configured(contract):
        actions = contract.get("actions") or {}
        if "verify" in actions:
            verify_action = "verify"
    if not contract_is_configured(contract) or not verify_action:
        # Credentials are saved, but there is no official verify endpoint to call.
        if ctx.has_credentials:
            row.status = STATUS_AUTHORIZED
            if row.authorized_at is None:
                row.authorized_at = _now()
            row.last_error = (
                ""
                if contract_is_configured(contract)
                else "账号已保存，但运维端尚未启用官方契约，无法向官方接口做连通性校验。live 写入会被拒绝，本次未发起外部请求。"
            )
        row.updated_at = _now()
        db.commit()
        return public_channel(db, hotel_id, platform)

    from app.adapters.ota.official_http import OfficialHttpClient, OfficialHttpError

    client = OfficialHttpClient(platform, contract, ctx.credentials)
    try:
        client.send_write(verify_action, {"checkpoint_id": "channel-verify"}, [], request_id="verify")
        mark_channel_verified(db, hotel_id, platform)
    except OfficialHttpError as exc:
        mark_channel_verified(db, hotel_id, platform, error=exc.message)
    return public_channel(db, hotel_id, platform)
