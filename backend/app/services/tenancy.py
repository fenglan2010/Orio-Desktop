"""Hotel membership and current-hotel resolution. Auth is membership-based, not tenant_key=demo."""

from __future__ import annotations

from contextvars import ContextVar

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import Hotel, HotelMembership, SystemSetting, User

_request_hotel_id: ContextVar[int | None] = ContextVar("oiro_request_hotel_id", default=None)

POLICY_SETTING = "execution_policy"
CONTRACT_PREFIX = "official_contract_"
LEGACY_PER_HOTEL_KEYS = (POLICY_SETTING, "official_contract_ctrip", "official_contract_meituan", "official_contract_fliggy")


def policy_setting_key(hotel_id: int) -> str:
    return f"hotel:{int(hotel_id)}:{POLICY_SETTING}"


def set_request_hotel_id(hotel_id: int | None):
    return _request_hotel_id.set(int(hotel_id) if hotel_id is not None else None)


def reset_request_hotel_id(token) -> None:
    try:
        _request_hotel_id.reset(token)
    except ValueError:
        _request_hotel_id.set(None)


def request_hotel_id() -> int | None:
    return _request_hotel_id.get()


def bind_session_hotel(db: Session | None, hotel_id: int | None) -> None:
    """Remember the hotel on the SQLAlchemy session (request-scoped, thread-safe)."""
    if db is not None and hotel_id is not None:
        db.info["oiro_hotel_id"] = int(hotel_id)
    if hotel_id is not None:
        set_request_hotel_id(hotel_id)


def session_hotel_id(db: Session | None = None) -> int | None:
    if db is not None:
        raw = db.info.get("oiro_hotel_id") if getattr(db, "info", None) is not None else None
        if raw is not None:
            return int(raw)
    return request_hotel_id()


def memberships_for_user(db: Session, user_id: int) -> list[HotelMembership]:
    return (
        db.query(HotelMembership)
        .filter(HotelMembership.user_id == user_id)
        .order_by(HotelMembership.id.asc())
        .all()
    )


def membership_for(db: Session, user_id: int, hotel_id: int) -> HotelMembership | None:
    return (
        db.query(HotelMembership)
        .filter(HotelMembership.user_id == user_id, HotelMembership.hotel_id == hotel_id)
        .one_or_none()
    )


def users_for_hotel(db: Session, hotel_id: int) -> list[User]:
    rows = (
        db.query(User, HotelMembership)
        .join(HotelMembership, HotelMembership.user_id == User.id)
        .filter(HotelMembership.hotel_id == hotel_id)
        .order_by(User.id.asc())
        .all()
    )
    users: list[User] = []
    for user, membership in rows:
        # Keep the in-memory role aligned with this hotel without dirtying a different stored role.
        if user.role != membership.role:
            user.role = membership.role
        users.append(user)
    return users


def ensure_membership(db: Session, user: User, hotel: Hotel, role: str | None = None) -> HotelMembership:
    role = (role or user.role or "analyst").strip()
    from app.services.ops_permissions import is_ops_role

    if is_ops_role(role) or is_ops_role(getattr(user, "role", "")):
        raise ValueError("运维账号不能加入酒店")
    row = membership_for(db, user.id, hotel.id)
    if row is None:
        row = HotelMembership(user_id=user.id, hotel_id=hotel.id, role=role)
        db.add(row)
        db.flush()
    elif role and row.role != role:
        row.role = role
    if user.role != row.role:
        user.role = row.role
    return row


def public_hotel(hotel: Hotel) -> dict:
    return {
        "id": hotel.id,
        "name": hotel.name,
        "city": hotel.city,
        "address": getattr(hotel, "address", "") or "",
        "positioning": getattr(hotel, "positioning", "") or "",
        "platform": hotel.platform,
        "tenant_key": hotel.tenant_key,
    }


def _parse_hotel_id(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_current_hotel(
    db: Session,
    user: User,
    *,
    jwt_hotel_id: int | None = None,
    header_hotel_id: int | str | None = None,
    login_hotel_id: int | None = None,
) -> Hotel:
    """Pick the hotel this request is about from membership, never from a hardcoded tenant_key."""
    members = memberships_for_user(db, user.id)
    if not members:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前账号未加入任何酒店")
    allowed = {row.hotel_id: row for row in members}

    requested = _parse_hotel_id(header_hotel_id)
    if requested is None:
        requested = _parse_hotel_id(login_hotel_id)
    if requested is None:
        requested = _parse_hotel_id(jwt_hotel_id)

    if requested is not None:
        if requested not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前账号无权访问该酒店")
        hotel = db.get(Hotel, requested)
        if hotel is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="酒店不存在")
        if user.role != allowed[requested].role:
            user.role = allowed[requested].role
        return hotel

    if len(members) == 1:
        hotel = db.get(Hotel, members[0].hotel_id)
        if hotel is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="酒店不存在")
        if user.role != members[0].role:
            user.role = members[0].role
        return hotel

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请指定要进入的酒店")


def migrate_legacy_settings(db: Session, hotel_id: int) -> None:
    """Move global execution_policy / official_contract_* keys onto the first demo hotel."""
    for legacy in LEGACY_PER_HOTEL_KEYS:
        scoped = f"hotel:{int(hotel_id)}:{legacy}"
        old = db.get(SystemSetting, legacy)
        if old is None:
            continue
        existing = db.get(SystemSetting, scoped)
        if existing is None:
            db.add(SystemSetting(key=scoped, value=old.value))
        db.delete(old)
    db.commit()


def backfill_audit_hotel(db: Session, hotel_id: int) -> None:
    from app.models import AuditLog

    db.query(AuditLog).filter(AuditLog.hotel_id.is_(None)).update(
        {AuditLog.hotel_id: hotel_id}, synchronize_session=False
    )
    db.commit()
