"""Operator / maintenance APIs. Hotel tokens cannot access these routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.adapters.ota.official_http import ContractValidationError, PLATFORMS, set_platform_write_enabled
from app.auth import SCOPE_OPS, create_token, get_current_operator, is_operator_user, verify_password
from app.database import get_db
from app.models import Hotel, User
from app.adapters.pms.official_http import KINDS as PMS_KINDS
from app.services.channels import (
    list_ops_channels,
    ops_public_channel,
    save_official_api_credentials,
)
from app.services.official_contracts import (
    install_official_contract,
    uninstall_official_contract,
    validation_report,
)
from app.services.official_pms_contracts import (
    install_official_pms_contract,
    uninstall_official_pms_contract,
    validation_report as pms_validation_report,
)
from app.services.pms import (
    list_ops_pms_connections,
    ops_public_pms_connection,
    save_official_api_credentials as save_pms_official_api_credentials,
    set_pms_read_flag,
    set_pms_write_flag,
)
from app.services.onboarding import public_entitlement
from app.services.ops_permissions import ops_me_payload, require_ops, sanitize_ops_connection
from app.services.tenancy import public_hotel
from app.services.workflow import write_audit
from app.services.write_flags import public_pending, request_or_confirm_write_flag

router = APIRouter(prefix="/api/ops", tags=["ops"])


class OpsLoginRequest(BaseModel):
    username: str
    password: str


class OfficialCredentialsBody(BaseModel):
    key: str | None = Field(default=None, description="Official API key; omitted keeps the stored value, empty clears it.")
    secret: str | None = None
    token: str | None = None
    session: str | None = None


class WriteFlagBody(BaseModel):
    enabled: bool


def _platform(platform: str) -> str:
    value = (platform or "").strip().lower()
    if value not in PLATFORMS:
        raise HTTPException(status_code=404, detail="仅支持携程、美团、飞猪渠道")
    return value


def _hotel(db: Session, hotel_id: int) -> Hotel:
    hotel = db.get(Hotel, hotel_id)
    if hotel is None:
        raise HTTPException(status_code=404, detail="酒店不存在")
    return hotel

def _pms_kind(kind: str) -> str:
    value = (kind or "").strip().lower()
    if value not in PMS_KINDS:
        raise HTTPException(status_code=404, detail="仅支持 PMS 或 CRS 官方接口预留接入")
    return value


@router.post("/auth/login")
def login(body: OpsLoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if getattr(user, "disabled", False):
        raise HTTPException(status_code=401, detail="账号已停用，无法登录")
    if not is_operator_user(user):
        raise HTTPException(status_code=403, detail="请使用运维账号从运维端登录")
    payload = ops_me_payload(user)
    return {
        "access_token": create_token(user, scope=SCOPE_OPS),
        "token_type": "bearer",
        "scope": SCOPE_OPS,
        **payload,
    }


@router.get("/auth/me")
def me(user: User = Depends(get_current_operator)):
    payload = ops_me_payload(user)
    payload["scope"] = SCOPE_OPS
    return payload


def _ops_hotel_payload(db: Session, hotel: Hotel, user: User) -> dict:
    payload = {**public_hotel(hotel), **public_entitlement(db, hotel)}
    return payload


def _channel_payload(db: Session, hotel_id: int, platform: str, user: User) -> dict:
    public = ops_public_channel(db, hotel_id, platform)
    pending = public_pending(db, hotel_id, platform)
    if pending:
        public["write_flag_pending"] = pending
    return sanitize_ops_connection(public, user)


def _pms_payload(db: Session, hotel_id: int, kind: str, user: User) -> dict:
    public = ops_public_pms_connection(db, hotel_id, kind)
    pending = public_pending(db, hotel_id, f"pms_{kind}")
    if pending:
        public["write_flag_pending"] = pending
    return sanitize_ops_connection(public, user)


@router.get("/hotels")
def list_hotels(db: Session = Depends(get_db), user: User = Depends(require_ops("view_hotels"))):
    rows = db.query(Hotel).order_by(Hotel.id.asc()).all()
    return {"hotels": [_ops_hotel_payload(db, row, user) for row in rows]}


@router.get("/hotels/{hotel_id}/channels")
def list_channels(hotel_id: int, db: Session = Depends(get_db), user: User = Depends(require_ops("view_channels"))):
    hotel = _hotel(db, hotel_id)
    channels = [_channel_payload(db, hotel.id, row["platform"], user) if "platform" in row else sanitize_ops_connection(row, user) for row in list_ops_channels(db, hotel.id)]
    return {"hotel": _ops_hotel_payload(db, hotel, user), "channels": channels}


@router.get("/hotels/{hotel_id}/channels/{platform}")
def get_channel(
    hotel_id: int,
    platform: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("view_channels")),
):
    hotel = _hotel(db, hotel_id)
    return _channel_payload(db, hotel.id, _platform(platform), user)


@router.post("/hotels/{hotel_id}/channels/{platform}/contract/validate")
def validate_contract(
    hotel_id: int,
    platform: str,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("install_contracts")),
):
    _hotel(db, hotel_id)
    report = validation_report(body, _platform(platform))
    if not report["valid"]:
        raise HTTPException(status_code=400, detail="；".join(report["errors"] or [report["message"]]))
    return report


@router.put("/hotels/{hotel_id}/channels/{platform}/contract")
def put_contract(
    hotel_id: int,
    platform: str,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("install_contracts")),
):
    hotel = _hotel(db, hotel_id)
    platform = _platform(platform)
    try:
        result = install_official_contract(db, platform, body, actor=user.username, hotel_id=hotel.id)
    except ContractValidationError as exc:
        raise HTTPException(status_code=400, detail="；".join(exc.errors or [exc.message])) from exc
    public = _channel_payload(db, hotel.id, platform, user)
    public.update({k: result[k] for k in result if k not in {"headers", "contract", "auth_header", "key_header", "request_template", "base_url"}})
    return public


@router.delete("/hotels/{hotel_id}/channels/{platform}/contract")
def delete_contract(
    hotel_id: int,
    platform: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("install_contracts")),
):
    hotel = _hotel(db, hotel_id)
    platform = _platform(platform)
    try:
        result = uninstall_official_contract(db, platform, actor=user.username, hotel_id=hotel.id)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    public = _channel_payload(db, hotel.id, platform, user)
    public.update({k: result[k] for k in result if k not in public})
    return public


@router.put("/hotels/{hotel_id}/channels/{platform}/write-flag")
def put_write_flag(
    hotel_id: int,
    platform: str,
    body: WriteFlagBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("request_write_flag")),
):
    hotel = _hotel(db, hotel_id)
    platform = _platform(platform)

    def _apply(enabled: bool) -> bool:
        return set_platform_write_enabled(db, hotel.id, platform, enabled)

    result = request_or_confirm_write_flag(db, hotel.id, platform, body.enabled, user=user, apply=_apply)
    write_audit(
        db,
        actor=user.username,
        action="ops_write_flag",
        entity_type="channel_connection",
        entity_id=f"{hotel.id}:{platform}",
        hotel_id=hotel.id,
        after={"platform": platform, "applied": result.get("applied"), "requested_enabled": body.enabled},
        reason=result.get("message") or "线上写入开关双人确认",
    )
    public = _channel_payload(db, hotel.id, platform, user)
    public["message"] = result.get("message")
    public["write_flag_applied"] = result.get("applied")
    return public


@router.put("/hotels/{hotel_id}/channels/{platform}/official-credentials")
def put_official_credentials(
    hotel_id: int,
    platform: str,
    body: OfficialCredentialsBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("install_contracts")),
):
    hotel = _hotel(db, hotel_id)
    platform = _platform(platform)
    stored = save_official_api_credentials(db, hotel.id, platform, body.model_dump(exclude_unset=True))
    write_audit(
        db,
        actor=user.username,
        action="ops_official_credentials",
        entity_type="channel_connection",
        entity_id=f"{hotel.id}:{platform}",
        hotel_id=hotel.id,
        after={"platform": platform, "has_official_credentials": bool(stored)},
        reason="运维端保存官方接口凭证（密钥不写入审计，也不回显）",
    )
    return _channel_payload(db, hotel.id, platform, user)

@router.get("/hotels/{hotel_id}/pms")
def list_pms(hotel_id: int, db: Session = Depends(get_db), user: User = Depends(require_ops("view_channels"))):
    hotel = _hotel(db, hotel_id)
    connections = []
    for row in list_ops_pms_connections(db, hotel.id):
        kind = row.get("kind")
        connections.append(_pms_payload(db, hotel.id, kind, user) if kind else sanitize_ops_connection(row, user))
    return {"hotel": _ops_hotel_payload(db, hotel, user), "connections": connections}


@router.get("/hotels/{hotel_id}/pms/{kind}")
def get_pms(
    hotel_id: int,
    kind: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("view_channels")),
):
    hotel = _hotel(db, hotel_id)
    return _pms_payload(db, hotel.id, _pms_kind(kind), user)


@router.post("/hotels/{hotel_id}/pms/{kind}/contract/validate")
def validate_pms_contract(
    hotel_id: int,
    kind: str,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("install_contracts")),
):
    _hotel(db, hotel_id)
    report = pms_validation_report(body, _pms_kind(kind))
    if not report["valid"]:
        raise HTTPException(status_code=400, detail="；".join(report["errors"] or [report["message"]]))
    return report


@router.put("/hotels/{hotel_id}/pms/{kind}/contract")
def put_pms_contract(
    hotel_id: int,
    kind: str,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("install_contracts")),
):
    hotel = _hotel(db, hotel_id)
    kind = _pms_kind(kind)
    try:
        result = install_official_pms_contract(db, kind, body, actor=user.username, hotel_id=hotel.id)
    except ContractValidationError as exc:
        raise HTTPException(status_code=400, detail="；".join(exc.errors or [exc.message])) from exc
    public = _pms_payload(db, hotel.id, kind, user)
    public.update({k: result[k] for k in result if k not in {"headers", "contract", "auth_header", "key_header", "request_template", "base_url"}})
    return public


@router.delete("/hotels/{hotel_id}/pms/{kind}/contract")
def delete_pms_contract(
    hotel_id: int,
    kind: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("install_contracts")),
):
    hotel = _hotel(db, hotel_id)
    kind = _pms_kind(kind)
    try:
        result = uninstall_official_pms_contract(db, kind, actor=user.username, hotel_id=hotel.id)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    public = _pms_payload(db, hotel.id, kind, user)
    public.update({k: result[k] for k in result if k not in public})
    return public


class PmsFlagBody(BaseModel):
    enabled: bool


@router.put("/hotels/{hotel_id}/pms/{kind}/read-flag")
def put_pms_read_flag(
    hotel_id: int,
    kind: str,
    body: PmsFlagBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("install_contracts")),
):
    hotel = _hotel(db, hotel_id)
    kind = _pms_kind(kind)
    enabled = set_pms_read_flag(db, hotel.id, kind, body.enabled)
    write_audit(
        db,
        actor=user.username,
        action="ops_pms_read_flag",
        entity_type="pms_connection",
        entity_id=f"{hotel.id}:{kind}",
        hotel_id=hotel.id,
        after={"kind": kind, "read_flag_enabled": enabled},
        reason="运维端更新 PMS/CRS 读取开关（不会单独发起出站请求）",
    )
    return _pms_payload(db, hotel.id, kind, user)


@router.put("/hotels/{hotel_id}/pms/{kind}/write-flag")
def put_pms_write_flag(
    hotel_id: int,
    kind: str,
    body: PmsFlagBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("request_write_flag")),
):
    hotel = _hotel(db, hotel_id)
    kind = _pms_kind(kind)

    def _apply(enabled: bool) -> bool:
        return set_pms_write_flag(db, hotel.id, kind, enabled)

    result = request_or_confirm_write_flag(db, hotel.id, f"pms_{kind}", body.enabled, user=user, apply=_apply)
    write_audit(
        db,
        actor=user.username,
        action="ops_pms_write_flag",
        entity_type="pms_connection",
        entity_id=f"{hotel.id}:{kind}",
        hotel_id=hotel.id,
        after={"kind": kind, "applied": result.get("applied"), "requested_enabled": body.enabled},
        reason=result.get("message") or "PMS/CRS 线上写入开关双人确认",
    )
    public = _pms_payload(db, hotel.id, kind, user)
    public["message"] = result.get("message")
    public["write_flag_applied"] = result.get("applied")
    return public


@router.put("/hotels/{hotel_id}/pms/{kind}/official-credentials")
def put_pms_official_credentials(
    hotel_id: int,
    kind: str,
    body: OfficialCredentialsBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops("install_contracts")),
):
    hotel = _hotel(db, hotel_id)
    kind = _pms_kind(kind)
    stored = save_pms_official_api_credentials(db, hotel.id, kind, body.model_dump(exclude_unset=True))
    write_audit(
        db,
        actor=user.username,
        action="ops_pms_official_credentials",
        entity_type="pms_connection",
        entity_id=f"{hotel.id}:{kind}",
        hotel_id=hotel.id,
        after={"kind": kind, "has_official_credentials": bool(stored)},
        reason="运维端保存官方 PMS/CRS 接口凭证（密钥不写入审计，也不回显）",
    )
    return _pms_payload(db, hotel.id, kind, user)

