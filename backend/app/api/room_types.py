"""Hotel room-type catalog API (manual maintenance)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_hotel
from app.auth import get_current_user
from app.database import get_db
from app.models import Hotel, User
from app.services.permissions import require
from app.services.room_catalog import create_room_type, delete_room_type, list_room_types, update_room_type

router = APIRouter(prefix="/api/room-types", tags=["room-types"], dependencies=[Depends(get_current_user)])


class RoomTypeBody(BaseModel):
    code: str | None = None
    room_type: str | None = None
    name: str | None = None
    inventory: int | None = None
    quantity: int | None = None
    base_price: float | None = None
    price: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    location: str | None = None
    capacity: int | None = None


@router.get("")
def get_room_types(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel), user: User = Depends(require("view_l1"))):
    return {"room_types": list_room_types(db, hotel.id)}


@router.post("")
def post_room_type(
    body: RoomTypeBody,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("update_constraints")),
):
    if not body.name or (body.base_price is None and body.price is None):
        raise HTTPException(status_code=400, detail="新建房型须提供名称与基础价")
    return create_room_type(db, hotel, body.model_dump(exclude_unset=True), actor=user.username)


@router.put("/{room_id}")
def put_room_type(
    room_id: int,
    body: RoomTypeBody,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("update_constraints")),
):
    return update_room_type(db, hotel, room_id, body.model_dump(exclude_unset=True), actor=user.username)


@router.delete("/{room_id}")
def remove_room_type(
    room_id: int,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("update_constraints")),
):
    return delete_room_type(db, hotel, room_id, actor=user.username)