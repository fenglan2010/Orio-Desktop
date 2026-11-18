"""Dual-confirmation hook for official live-write flags.

合同 and 管理员 may request or confirm a change. Neither role can apply a
live-write flag alone. This is not a payment or billing product.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import SystemSetting
from app.services.ops_permissions import OPS_ADMIN, OPS_CONTRACT, ops_role_of

PENDING_TRUE = {"1", "true", "yes", "on"}


def pending_key(hotel_id: int, scope: str) -> str:
    return f"hotel:{int(hotel_id)}:{scope}_write_flag_pending"


def load_pending(db: Session, hotel_id: int, scope: str) -> dict[str, Any] | None:
    row = db.get(SystemSetting, pending_key(hotel_id, scope))
    if row is None or not (row.value or "").strip():
        return None
    try:
        data = json.loads(row.value)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _save_pending(db: Session, hotel_id: int, scope: str, payload: dict | None) -> None:
    key = pending_key(hotel_id, scope)
    row = db.get(SystemSetting, key)
    if payload is None:
        if row is not None:
            db.delete(row)
            db.commit()
        return
    raw = json.dumps(payload, ensure_ascii=False)
    if row is None:
        db.add(SystemSetting(key=key, value=raw))
    else:
        row.value = raw
        row.updated_at = datetime.utcnow()
    db.commit()


def public_pending(db: Session, hotel_id: int, scope: str) -> dict[str, Any] | None:
    data = load_pending(db, hotel_id, scope)
    if not data:
        return None
    return {
        "enabled": bool(data.get("enabled")),
        "requested_by": data.get("requested_by") or "",
        "requested_by_role": data.get("requested_by_role") or "",
        "requested_at": data.get("requested_at") or "",
        "waiting_for": data.get("waiting_for") or "",
    }


def request_or_confirm_write_flag(
    db: Session,
    hotel_id: int,
    scope: str,
    enabled: bool,
    *,
    user,
    apply,
) -> dict[str, Any]:
    """apply(enabled) persists the flag. Returns {applied, enabled, pending}."""
    role = ops_role_of(user)
    if role not in {OPS_ADMIN, OPS_CONTRACT}:
        raise HTTPException(status_code=403, detail="线上写入开关须由合同岗与管理员双人确认，当前岗位不能操作。")
    other = OPS_ADMIN if role == OPS_CONTRACT else OPS_CONTRACT
    pending = load_pending(db, hotel_id, scope)
    if (
        pending
        and bool(pending.get("enabled")) == bool(enabled)
        and pending.get("requested_by_role")
        and pending.get("requested_by_role") != role
    ):
        apply(enabled)
        _save_pending(db, hotel_id, scope, None)
        return {
            "applied": True,
            "enabled": bool(enabled),
            "write_flag_pending": None,
            "message": "双方已确认，线上写入开关已更新。",
        }
    payload = {
        "enabled": bool(enabled),
        "requested_by": user.username,
        "requested_by_role": role,
        "requested_at": datetime.utcnow().isoformat(),
        "waiting_for": other,
    }
    _save_pending(db, hotel_id, scope, payload)
    return {
        "applied": False,
        "enabled": None,
        "write_flag_pending": public_pending(db, hotel_id, scope),
        "message": "已记录变更请求，须由另一岗位（管理员或合同）确认后才会生效。任何一方不能单独打开线上写入。",
    }
