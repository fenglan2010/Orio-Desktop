"""Manual room-type catalog for hotels without PMS (or before sync)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Hotel, RoomType
from app.services.workflow import write_audit

_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


def _clean_code(value: str) -> str:
    code = (value or "").strip().upper()
    if not _CODE_PATTERN.match(code):
        raise HTTPException(status_code=400, detail="房型代码需为 1–16 位字母、数字、_ 或 -")
    return code


def _clean_name(value: str) -> str:
    name = (value or "").strip()
    if len(name) < 1 or len(name) > 64:
        raise HTTPException(status_code=400, detail="房型名称需为 1–64 个字符")
    return name


def _clean_location(value: str | None) -> str:
    loc = (value or "").strip()
    if len(loc) > 128:
        raise HTTPException(status_code=400, detail="位置说明不能超过 128 个字符")
    return loc


def _price_bounds(base: float) -> tuple[float, float]:
    base_f = round(float(base), 0)
    if base_f <= 0:
        raise HTTPException(status_code=400, detail="基础价必须大于 0")
    minimum = max(1.0, round(base_f * 0.65, 0))
    maximum = max(minimum, round(base_f * 1.6, 0))
    return minimum, maximum


def public_room_type(room: RoomType) -> dict[str, Any]:
    return {
        "id": room.id,
        "name": room.name,
        "code": room.code,
        "inventory": room.inventory,
        "capacity": room.capacity,
        "base_price": room.base_price,
        "min_price": room.min_price,
        "max_price": room.max_price,
        "location": getattr(room, "location", "") or "",
    }


def list_room_types(db: Session, hotel_id: int) -> list[dict[str, Any]]:
    rows = db.query(RoomType).filter(RoomType.hotel_id == hotel_id).order_by(RoomType.id).all()
    return [public_room_type(r) for r in rows]


def _assert_unique_code(db: Session, hotel_id: int, code: str, *, exclude_id: int | None = None) -> None:
    q = db.query(RoomType).filter(RoomType.hotel_id == hotel_id, RoomType.code == code)
    if exclude_id is not None:
        q = q.filter(RoomType.id != exclude_id)
    if q.one_or_none() is not None:
        raise HTTPException(status_code=400, detail=f"房型代码 {code} 已存在")


def create_room_type(db: Session, hotel: Hotel, payload: dict[str, Any], *, actor: str = "system") -> dict[str, Any]:
    code = _clean_code(payload.get("code") or payload.get("room_type") or "")
    name = _clean_name(payload.get("name") or "")
    location = _clean_location(payload.get("location"))
    try:
        inventory = int(payload.get("inventory") or payload.get("quantity") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="房间数量必须是正整数") from exc
    if inventory < 1 or inventory > 9999:
        raise HTTPException(status_code=400, detail="房间数量须为 1–9999")
    try:
        base_price = float(payload.get("base_price") or payload.get("price") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="价格必须是数字") from exc
    capacity = 2
    raw_cap = payload.get("capacity")
    if raw_cap is not None:
        try:
            capacity = max(1, min(12, int(raw_cap)))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="可住人数必须是 1–12 的整数") from exc
    min_price, max_price = _price_bounds(base_price)
    if "min_price" in payload and payload["min_price"] is not None:
        min_price = round(float(payload["min_price"]), 0)
    if "max_price" in payload and payload["max_price"] is not None:
        max_price = round(float(payload["max_price"]), 0)
    if min_price <= 0 or max_price <= 0 or min_price > max_price:
        raise HTTPException(status_code=400, detail="价格上下限无效")

    _assert_unique_code(db, hotel.id, code)
    room = RoomType(
        hotel_id=hotel.id,
        name=name,
        code=code,
        inventory=inventory,
        capacity=capacity,
        base_price=round(base_price, 0),
        min_price=min_price,
        max_price=max_price,
        location=location,
    )
    db.add(room)
    db.commit()
    db.refresh(room)
    write_audit(
        db,
        actor=actor,
        action="create_room_type",
        entity_type="room_type",
        entity_id=str(room.id),
        after=public_room_type(room),
        reason=f"创建房型 {room.name}",
        hotel_id=hotel.id,
    )
    return public_room_type(room)


def update_room_type(
    db: Session,
    hotel: Hotel,
    room_id: int,
    payload: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    room = db.get(RoomType, room_id)
    if room is None or room.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="房型不存在")
    before = public_room_type(room)
    if "code" in payload or "room_type" in payload:
        code = _clean_code(payload.get("code") or payload.get("room_type") or room.code)
        _assert_unique_code(db, hotel.id, code, exclude_id=room.id)
        room.code = code
    if payload.get("name") is not None:
        room.name = _clean_name(payload["name"])
    if payload.get("location") is not None:
        room.location = _clean_location(payload["location"])
    if payload.get("inventory") is not None or payload.get("quantity") is not None:
        try:
            inventory = int(payload.get("inventory") if payload.get("inventory") is not None else payload.get("quantity"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="房间数量必须是正整数") from exc
        if inventory < 1 or inventory > 9999:
            raise HTTPException(status_code=400, detail="房间数量须为 1–9999")
        room.inventory = inventory
    if payload.get("capacity") is not None:
        try:
            room.capacity = max(1, min(12, int(payload["capacity"])))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="可住人数必须是 1–12 的整数") from exc
    if payload.get("base_price") is not None or payload.get("price") is not None:
        try:
            base_price = float(payload.get("base_price") if payload.get("base_price") is not None else payload.get("price"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="价格必须是数字") from exc
        room.base_price = round(base_price, 0)
        if "min_price" not in payload and "max_price" not in payload:
            room.min_price, room.max_price = _price_bounds(room.base_price)
    if payload.get("min_price") is not None:
        room.min_price = round(float(payload["min_price"]), 0)
    if payload.get("max_price") is not None:
        room.max_price = round(float(payload["max_price"]), 0)
    if room.min_price <= 0 or room.max_price <= 0 or room.min_price > room.max_price:
        raise HTTPException(status_code=400, detail="价格上下限无效")
    db.commit()
    db.refresh(room)
    after = public_room_type(room)
    write_audit(
        db,
        actor=actor,
        action="update_room_type",
        entity_type="room_type",
        entity_id=str(room.id),
        before=before,
        after=after,
        reason=f"更新房型 {room.name}",
        hotel_id=hotel.id,
    )
    return after


def delete_room_type(db: Session, hotel: Hotel, room_id: int, *, actor: str) -> dict[str, Any]:
    room = db.get(RoomType, room_id)
    if room is None or room.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="房型不存在")
    count = db.query(RoomType).filter(RoomType.hotel_id == hotel.id).count()
    if count <= 1:
        raise HTTPException(status_code=400, detail="至少保留一个房型")
    before = public_room_type(room)
    db.delete(room)
    db.commit()
    write_audit(
        db,
        actor=actor,
        action="delete_room_type",
        entity_type="room_type",
        entity_id=str(room_id),
        before=before,
        reason=f"删除房型 {before['name']}",
        hotel_id=hotel.id,
    )
    return {"ok": True, "id": room_id}


def create_room_types_batch(db: Session, hotel: Hotel, items: list[dict[str, Any]], *, actor: str) -> list[dict[str, Any]]:
    if not items:
        raise HTTPException(status_code=400, detail="请至少添加一个房型")
    if len(items) > 50:
        raise HTTPException(status_code=400, detail="单次最多 50 个房型")
    created: list[dict[str, Any]] = []
    codes: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        code = _clean_code(raw.get("code") or raw.get("room_type") or "")
        if code in codes:
            raise HTTPException(status_code=400, detail=f"房型代码 {code} 重复")
        codes.add(code)
        created.append(create_room_type(db, hotel, raw, actor=actor))
    return created


def _vr_code(value: str) -> str:
    code = (value or "").strip().upper()
    if not _CODE_PATTERN.match(code):
        raise ValueError("房型代码需为 1–16 位字母、数字、_ 或 -")
    return code


def _vr_name(value: str) -> str:
    name = (value or "").strip()
    if len(name) < 1 or len(name) > 64:
        raise ValueError("房型名称需为 1–64 个字符")
    return name


def validate_first_run_room_types(items: list[Any]) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise ValueError("请至少添加一个房型（代码、名称、数量、价格与位置）。")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        try:
            code = _vr_code(raw.get("code") or raw.get("room_type") or "")
            name = _vr_name(raw.get("name") or "")
            inventory = int(raw.get("inventory") or raw.get("quantity") or 0)
            base_price = float(raw.get("base_price") or raw.get("price") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        location = (raw.get("location") or "").strip()
        if code in seen:
            raise ValueError(f"房型代码 {code} 重复")
        seen.add(code)
        normalized.append(
            {
                "code": code,
                "name": name,
                "inventory": inventory,
                "base_price": base_price,
                "location": location[:128],
                "capacity": raw.get("capacity"),
            }
        )
    if not normalized:
        raise ValueError("请至少添加一个有效房型。")
    for row in normalized:
        if row["inventory"] < 1:
            raise ValueError("每个房型的房间数量须至少为 1")
        if row["base_price"] <= 0:
            raise ValueError("每个房型的价格须大于 0")
        if not row["location"]:
            raise ValueError("请为每个房型填写位置（楼栋/楼层/区域等）")
    return normalized