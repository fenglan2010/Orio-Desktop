from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import get_current_user
from app.database import get_db
from app.models import Hotel, User
from app.services.permissions import require
from app.services.channels import (
    PLATFORMS,
    disconnect_channel,
    list_public_channels,
    public_channel,
    save_channel_connection,
    strip_hotel_channel_fields,
    verify_channel,
)
from app.services.workflow import write_audit

router = APIRouter(prefix="/api/channels", tags=["channels"], dependencies=[Depends(get_current_user)])


class ChannelConnectBody(BaseModel):
    account: str | None = Field(default=None, description="OTA account; omitted keeps the stored value, empty clears it.")
    password: str | None = Field(default=None, description="OTA password; omitted keeps the stored value, empty clears it.")
    username: str | None = Field(default=None, description="Alias for account.")
    # Backward-compatible aliases for previously stored key/secret/account ids.
    platform_hotel_id: str | None = None
    platform_account_id: str | None = None
    key: str | None = None
    secret: str | None = None
    session: str | None = None


def _platform(platform: str) -> str:
    value = (platform or "").strip().lower()
    if value not in PLATFORMS:
        raise HTTPException(status_code=404, detail="仅支持携程、美团、飞猪渠道")
    return value


def _public(db: Session, hotel: Hotel, platform: str) -> dict:
    return strip_hotel_channel_fields(public_channel(db, hotel.id, platform))


@router.get("")
def list_channels(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel), user: User = Depends(require("view_channels"))):
    return {"channels": list_public_channels(db, hotel.id)}


@router.get("/{platform}")
def get_channel(platform: str, db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel), user: User = Depends(require("view_channels"))):
    return _public(db, hotel, _platform(platform))


@router.put("/{platform}")
def connect_channel(
    platform: str,
    body: ChannelConnectBody,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("manage_channels")),
):
    platform = _platform(platform)
    payload = body.model_dump(exclude_unset=True)
    try:
        row = save_channel_connection(db, hotel.id, platform, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        db,
        actor=user.username,
        action="channel_connect",
        entity_type="channel_connection",
        entity_id=f"{hotel.id}:{platform}",
        after={
            "platform": platform,
            "status": row.status,
            "has_credentials": bool(row.credentials_encrypted),
        },
        reason="保存渠道连接（账号密码不写入审计）",
    )
    return _public(db, hotel, platform)


@router.post("/{platform}/verify")
def verify(
    platform: str,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("manage_channels")),
):
    platform = _platform(platform)
    result = verify_channel(db, hotel.id, platform)
    write_audit(
        db,
        actor=user.username,
        action="channel_verify",
        entity_type="channel_connection",
        entity_id=f"{hotel.id}:{platform}",
        after={"platform": platform, "status": result.get("status"), "last_error": result.get("last_error")},
        reason="校验渠道连接（未回显密钥）",
    )
    return result


@router.post("/{platform}/disconnect")
def disconnect(
    platform: str,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("manage_channels")),
):
    platform = _platform(platform)
    row = disconnect_channel(db, hotel.id, platform)
    write_audit(
        db,
        actor=user.username,
        action="channel_disconnect",
        entity_type="channel_connection",
        entity_id=f"{hotel.id}:{platform}",
        after={"platform": platform, "status": row.status},
        reason="断开渠道并清除已保存的密钥",
    )
    return _public(db, hotel, platform)


def _hotel_contract_forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail="官方契约由运维端配置，酒店账号不能校验、安装或卸载。")


@router.post("/{platform}/contract/validate")
def validate_contract(
    platform: str,
    body: dict | None = None,
    user: User = Depends(get_current_user),
):
    _platform(platform)
    raise _hotel_contract_forbidden()


@router.put("/{platform}/contract")
def put_contract(
    platform: str,
    body: dict | None = None,
    user: User = Depends(get_current_user),
):
    _platform(platform)
    raise _hotel_contract_forbidden()


@router.delete("/{platform}/contract")
def delete_contract(
    platform: str,
    user: User = Depends(get_current_user),
):
    _platform(platform)
    raise _hotel_contract_forbidden()
