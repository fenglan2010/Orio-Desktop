from __future__ import annotations

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.auth import decode_token, get_current_user
from app.database import get_db
from app.models import Hotel, User
from app.services.tenancy import bind_session_hotel, resolve_current_hotel


def _jwt_hotel_id(request: Request) -> int | None:
    header = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if not header.lower().startswith("bearer "):
        return None
    token = header.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        payload = decode_token(token)
    except Exception:
        return None
    raw = payload.get("hotel_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def current_hotel(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    x_hotel_id: str | None = Header(default=None, alias="X-Hotel-Id"),
) -> Hotel:
    hotel = resolve_current_hotel(
        db,
        user,
        jwt_hotel_id=_jwt_hotel_id(request),
        header_hotel_id=x_hotel_id,
    )
    bind_session_hotel(db, hotel.id)
    request.state.hotel_id = hotel.id
    path = (request.url.path or "").rstrip("/") or "/"
    if not (request.method.upper() == "GET" and path in {"/api/auth/me", "/api/settings"}):
        from app.services.onboarding import ensure_product_enabled

        ensure_product_enabled(db, hotel)
    return hotel
