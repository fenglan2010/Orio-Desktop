"""Hotel-facing official PMS/CRS reserved hook. Contract install stays on the ops backend."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import get_current_user
from app.database import get_db
from app.models import Hotel, User
from app.services.permissions import require
from app.adapters.pms.official_http import KINDS
from app.services.pms import (
    disconnect_pms_connection,
    list_public_pms_connections,
    public_pms_connection,
    save_pms_connection,
    strip_hotel_pms_fields,
    verify_pms_connection,
)
from app.services.workflow import write_audit

router = APIRouter(prefix="/api/pms", tags=["pms"], dependencies=[Depends(get_current_user)])


class PmsConnectBody(BaseModel):
    account: str | None = Field(default=None, description="PMS/CRS account; omitted keeps the stored value, empty clears it.")
    password: str | None = Field(default=None, description="PMS/CRS password; omitted keeps the stored value, empty clears it.")
    username: str | None = Field(default=None, description="Alias for account.")


def _kind(kind: str) -> str:
    value = (kind or "").strip().lower()
    if value not in KINDS:
        raise HTTPException(status_code=404, detail="仅支持 PMS 或 CRS 官方接口预留接入")
    return value


def _public(db: Session, hotel: Hotel, kind: str) -> dict:
    return strip_hotel_pms_fields(public_pms_connection(db, hotel.id, kind))


@router.get("")
def list_connections(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel), user: User = Depends(require("view_channels"))):
    return {"connections": list_public_pms_connections(db, hotel.id)}


@router.get("/{kind}")
def get_connection(kind: str, db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel), user: User = Depends(require("view_channels"))):
    return _public(db, hotel, _kind(kind))


@router.put("/{kind}")
def connect(
    kind: str,
    body: PmsConnectBody,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("manage_channels")),
):
    kind = _kind(kind)
    payload = body.model_dump(exclude_unset=True)
    try:
        row = save_pms_connection(db, hotel.id, kind, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        db,
        actor=user.username,
        action="pms_connect",
        entity_type="pms_connection",
        entity_id=f"{hotel.id}:{kind}",
        after={"kind": kind, "status": row.status, "has_credentials": bool(row.credentials_encrypted)},
        reason="保存 PMS/CRS 连接（账号密码不写入审计）",
    )
    return _public(db, hotel, kind)


@router.post("/{kind}/verify")
def verify(
    kind: str,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("manage_channels")),
):
    kind = _kind(kind)
    result = verify_pms_connection(db, hotel.id, kind)
    write_audit(
        db,
        actor=user.username,
        action="pms_verify",
        entity_type="pms_connection",
        entity_id=f"{hotel.id}:{kind}",
        after={"kind": kind, "status": result.get("status"), "last_error": result.get("last_error")},
        reason="校验 PMS/CRS 连接（未回显密钥）",
    )
    return result


@router.post("/{kind}/disconnect")
def disconnect(
    kind: str,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("manage_channels")),
):
    kind = _kind(kind)
    row = disconnect_pms_connection(db, hotel.id, kind)
    write_audit(
        db,
        actor=user.username,
        action="pms_disconnect",
        entity_type="pms_connection",
        entity_id=f"{hotel.id}:{kind}",
        after={"kind": kind, "status": row.status},
        reason="断开 PMS/CRS 并清除已保存的密钥",
    )
    return _public(db, hotel, kind)


def _hotel_contract_forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail="官方 PMS/CRS 契约由运维端配置，酒店账号不能校验、安装或卸载。")


@router.post("/{kind}/contract/validate")
def validate_contract(kind: str, body: dict | None = None, user: User = Depends(get_current_user)):
    _kind(kind)
    raise _hotel_contract_forbidden()


@router.put("/{kind}/contract")
def put_contract(kind: str, body: dict | None = None, user: User = Depends(get_current_user)):
    _kind(kind)
    raise _hotel_contract_forbidden()


@router.delete("/{kind}/contract")
def delete_contract(kind: str, user: User = Depends(get_current_user)):
    _kind(kind)
    raise _hotel_contract_forbidden()
