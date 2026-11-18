from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import create_token, get_current_user, is_operator_user, verify_password
from app.database import get_db
from app.models import Hotel, User
from app.schemas import LoginRequest, TokenResponse, UserMe
from app.services.permissions import me_payload, role_label
from app.services.onboarding import public_entitlement
from app.services.tenancy import membership_for, resolve_current_hotel

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if getattr(user, "disabled", False):
        raise HTTPException(status_code=401, detail="账号已停用，无法登录")
    if is_operator_user(user):
        raise HTTPException(status_code=403, detail="这是运维账号，请从运维端登录")
    hotel = resolve_current_hotel(db, user, login_hotel_id=body.hotel_id)
    membership = membership_for(db, user.id, hotel.id)
    role = membership.role if membership else user.role
    entitlement = public_entitlement(db, hotel)
    return TokenResponse(
        access_token=create_token(user, hotel.id),
        display_name=user.display_name,
        role=role,
        hotel_id=hotel.id,
        hotel_name=hotel.name,
        city=hotel.city or "",
        **entitlement,
    )


@router.get("/me", response_model=UserMe)
def me(user: User = Depends(get_current_user), hotel: Hotel = Depends(current_hotel), db: Session = Depends(get_db)):
    membership = membership_for(db, user.id, hotel.id)
    payload = me_payload(user, hotel, role=membership.role if membership else None)
    payload.setdefault("role_label", role_label(payload.get("role") or user.role))
    payload.update(public_entitlement(db, hotel))
    return payload
