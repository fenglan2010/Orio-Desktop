from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import get_current_user, hash_password
from app.database import get_db
from app.models import Hotel, HotelMembership, User
from app.schemas import UserCreate, UserUpdate
from app.services.permissions import (
    ROLE_ADMIN,
    ROLES,
    public_matrix_table,
    public_role_matrix,
    public_user,
    require,
    role_label,
)
from app.services.tenancy import ensure_membership, membership_for, users_for_hotel
from app.services.workflow import write_audit

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(get_current_user)])


def _active_admin_count(db: Session, hotel_id: int, *, exclude_id: int | None = None) -> int:
    q = (
        db.query(User)
        .join(HotelMembership, HotelMembership.user_id == User.id)
        .filter(
            HotelMembership.hotel_id == hotel_id,
            HotelMembership.role == ROLE_ADMIN,
            User.disabled.is_(False),
        )
    )
    if exclude_id is not None:
        q = q.filter(User.id != exclude_id)
    return q.count()


def _hotel_user(db: Session, hotel: Hotel, user_id: int) -> tuple[User, HotelMembership]:
    row = db.get(User, user_id)
    membership = membership_for(db, user_id, hotel.id) if row is not None else None
    if row is None or membership is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return row, membership


def _guard_last_admin(
    db: Session,
    hotel: Hotel,
    target: User,
    membership: HotelMembership,
    *,
    new_role: str | None = None,
    disable: bool = False,
) -> None:
    if membership.role != ROLE_ADMIN or target.disabled:
        return
    demoting = new_role is not None and new_role != ROLE_ADMIN
    if not disable and not demoting:
        return
    if _active_admin_count(db, hotel.id, exclude_id=target.id) == 0:
        raise HTTPException(status_code=400, detail="不能停用或降级唯一的酒店管理员")


def _clean_username(value: str) -> str:
    name = (value or "").strip()
    if len(name) < 3 or len(name) > 32 or not all(ch.isalnum() or ch in "._-" for ch in name):
        raise HTTPException(status_code=400, detail="用户名需为 3–32 位字母、数字或 ._-")
    return name


def _clean_password(value: str) -> str:
    if not value or len(value) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    return value


def _clean_role(value: str) -> str:
    role = (value or "").strip()
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="角色必须是酒店管理员、收益经理、调价审批人或运营观察")
    return role


@router.get("")
def list_users(
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("manage_users")),
):
    rows = (
        db.query(User, HotelMembership)
        .join(HotelMembership, HotelMembership.user_id == User.id)
        .filter(HotelMembership.hotel_id == hotel.id)
        .order_by(User.id.asc())
        .all()
    )
    return {"users": [public_user(row, role=membership.role) for row, membership in rows]}


@router.get("/matrix")
def role_matrix(
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(get_current_user),
):
    return {
        "matrix": public_matrix_table(),
        "roles": public_role_matrix(users_for_hotel(db, hotel.id)),
    }


@router.post("")
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("manage_users")),
):
    username = _clean_username(body.username)
    if db.query(User).filter(User.username == username).one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")
    role = _clean_role(body.role)
    display = (body.display_name or "").strip() or role_label(role)
    row = User(
        username=username,
        password_hash=hash_password(_clean_password(body.password)),
        display_name=display,
        role=role,
    )
    db.add(row)
    db.flush()
    membership = ensure_membership(db, row, hotel, role=role)
    db.commit()
    db.refresh(row)
    write_audit(
        db,
        actor=user.username,
        action="user_create",
        entity_type="user",
        entity_id=str(row.id),
        after={"username": row.username, "display_name": row.display_name, "role": membership.role, "hotel_id": hotel.id},
        reason="创建酒店账号（密码不写入审计）",
        hotel_id=hotel.id,
    )
    return public_user(row, role=membership.role)


@router.put("/{user_id}")
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("manage_users")),
):
    row, membership = _hotel_user(db, hotel, user_id)
    before = {"display_name": row.display_name, "role": membership.role, "disabled": bool(row.disabled)}
    payload = body.model_dump(exclude_unset=True)
    new_role = _clean_role(payload["role"]) if "role" in payload and payload["role"] is not None else None
    disable = bool(payload["disabled"]) if "disabled" in payload and payload["disabled"] is not None else None
    if new_role or disable is True:
        _guard_last_admin(db, hotel, row, membership, new_role=new_role, disable=bool(disable))
    if "display_name" in payload and payload["display_name"] is not None:
        name = str(payload["display_name"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="显示名不能为空")
        row.display_name = name
    if new_role:
        row.role = new_role
        membership.role = new_role
    if "password" in payload and payload["password"]:
        row.password_hash = hash_password(_clean_password(payload["password"]))
    if disable is True:
        row.disabled = True
        row.disabled_at = datetime.utcnow()
    elif disable is False:
        row.disabled = False
        row.disabled_at = None
    db.commit()
    db.refresh(row)
    write_audit(
        db,
        actor=user.username,
        action="user_update",
        entity_type="user",
        entity_id=str(row.id),
        before=before,
        after={"display_name": row.display_name, "role": membership.role, "disabled": bool(row.disabled), "password_reset": bool(payload.get("password"))},
        reason="更新酒店账号（密码不写入审计）",
        hotel_id=hotel.id,
    )
    return public_user(row, role=membership.role)


@router.post("/{user_id}/disable")
def disable_user(
    user_id: int,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("manage_users")),
):
    row, membership = _hotel_user(db, hotel, user_id)
    _guard_last_admin(db, hotel, row, membership, disable=True)
    row.disabled = True
    row.disabled_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    write_audit(
        db,
        actor=user.username,
        action="user_disable",
        entity_type="user",
        entity_id=str(row.id),
        after={"username": row.username, "disabled": True},
        reason="停用酒店账号",
        hotel_id=hotel.id,
    )
    return public_user(row, role=membership.role)


@router.post("/{user_id}/enable")
def enable_user(
    user_id: int,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("manage_users")),
):
    row, membership = _hotel_user(db, hotel, user_id)
    row.disabled = False
    row.disabled_at = None
    db.commit()
    db.refresh(row)
    write_audit(
        db,
        actor=user.username,
        action="user_enable",
        entity_type="user",
        entity_id=str(row.id),
        after={"username": row.username, "disabled": False},
        reason="重新启用酒店账号",
        hotel_id=hotel.id,
    )
    return public_user(row, role=membership.role)
