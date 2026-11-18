from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.adapters.ota.ctrip_mock import CtripMockAdapter
from app.api.deps import current_hotel
from app.auth import get_current_user
from app.services.permissions import require
from app.database import get_db
from app.models import Alert, Hotel, RoomType, User
from app.services.analysis_views import build_history_and_conversion, build_stock_and_pace
from app.services.kpis import flash_windows, in_range, pack_metrics, period_delta
from app.services.workflow import write_audit
from app.schemas import RoomConstraintsUpdate

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(get_current_user)])


def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


@router.get("/summary")
def summary(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    today = date.today()
    start = today - timedelta(days=14)
    ota = CtripMockAdapter()
    metrics = ota.fetch_metrics(db, hotel.id, start, today + timedelta(days=13))
    comps = ota.fetch_competitors(db, hotel.id, start, today + timedelta(days=13))
    rooms = {r.id: r for r in db.query(RoomType).filter(RoomType.hotel_id == hotel.id).all()}
    w = flash_windows(today)

    week = pack_metrics(in_range(metrics, w["week_start"], w["week_end"]))
    prev_week = pack_metrics(in_range(metrics, w["prev_week_start"], w["prev_week_end"]))
    today_pack = pack_metrics(in_range(metrics, w["today"], w["today"]))
    last_week_day = pack_metrics(in_range(metrics, w["same_weekday_last_week"], w["same_weekday_last_week"]))
    last7 = pack_metrics(in_range(metrics, w["last7_start"], w["last7_end"]))
    prev7 = pack_metrics(in_range(metrics, w["prev7_start"], w["prev7_end"]))
    week_delta = period_delta(week, prev_week)
    today_delta = period_delta(today_pack, last_week_day)
    last7_delta = period_delta(last7, prev7)

    last7_rows = in_range(metrics, w["last7_start"], w["last7_end"])
    comp_prices = [c.price for c in comps if c.date >= today - timedelta(days=7)]
    our_prices = [m.list_price for m in last7_rows if m.list_price]
    band = {
        "competitor_low": round(min(comp_prices), 0) if comp_prices else 0,
        "competitor_high": round(max(comp_prices), 0) if comp_prices else 0,
        "competitor_median": round(sorted(comp_prices)[len(comp_prices)//2], 0) if comp_prices else 0,
        "our_avg_list": round(_avg(our_prices), 0),
    }
    open_alerts = db.query(Alert).filter(Alert.hotel_id == hotel.id, Alert.status == "open").count()
    return {
        "hotel": {"id": hotel.id, "name": hotel.name, "city": hotel.city, "platform": hotel.platform, "address": hotel.address},
        "as_of": today.isoformat(),
        "window": "近7日 vs 前7日",
        "flash_window": "本周 vs 上周同期 · 今日 vs 上周同日",
        "kpis": {
            **last7,
            "week_revenue": week["revenue"],
            "revpar": today_pack["revpar"],
            "today_adr": today_pack["adr"],
            "today_occupancy": today_pack["occupancy"],
            "today_orders": today_pack["orders"],
            "today_revenue": today_pack["revenue"],
            "today_pageviews": today_pack["pageviews"],
            "today_conversion_rate": today_pack["conversion_rate"],
            "delta": {
                "revenue": last7_delta["revenue"],
                "occupancy": last7_delta["occupancy"],
                "occupancy_pp": last7_delta["occupancy_pp"],
                "adr": last7_delta["adr"],
                "conversion_rate": last7_delta["conversion_rate"],
                "search_rank": last7_delta["search_rank"],
                "week_revenue": week_delta["revenue"],
                "revpar": today_delta["revpar"],
                "today_adr": today_delta["adr"],
                "today_occupancy": today_delta["occupancy_pp"],
                "today_orders": today_delta["orders"],
                "today_revenue": today_delta["revenue"],
                "today_pageviews": today_delta["pageviews"],
                "today_conversion_rate": today_delta["conversion_pp"],
            },
        },
        "competitor_band": band,
        "open_alerts": open_alerts,
        "room_count": len(rooms),
        "data_source": "ctrip_simulated",
    }


@router.get("/timeseries")
def timeseries(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    today = date.today()
    ota = CtripMockAdapter()
    metrics = ota.fetch_metrics(db, hotel.id, today - timedelta(days=14), today + timedelta(days=13))
    rooms = {r.id: r.name for r in db.query(RoomType).filter(RoomType.hotel_id == hotel.id).all()}
    by_date: dict[str, dict] = {}
    by_room: dict[str, list] = defaultdict(list)
    for m in metrics:
        key = m.date.isoformat()
        row = by_date.setdefault(
            key,
            {
                "date": key,
                "is_future": m.is_future,
                "revenue": 0,
                "sold": 0,
                "available": 0,
                "bookings": 0,
                "clicks": 0,
                "rank": [],
                "conv": [],
                "adr": [],
            },
        )
        row["revenue"] += m.revenue
        row["sold"] += m.sold_rooms
        row["available"] += m.available_rooms
        row["bookings"] += m.bookings or 0
        row["clicks"] += m.clicks or 0
        if m.search_rank:
            row["rank"].append(m.search_rank)
        row["conv"].append(m.conversion_rate)
        row["adr"].append(m.adr)
        by_room[rooms.get(m.room_type_id, str(m.room_type_id))].append(
            {
                "date": key,
                "is_future": m.is_future,
                "revenue": m.revenue,
                "occupancy": m.occupancy,
                "list_price": m.list_price,
                "adr": m.adr,
                "search_rank": m.search_rank,
                "conversion_rate": m.conversion_rate,
                "sold": m.sold_rooms,
                "available": m.available_rooms,
                "remaining": max(0, (m.available_rooms or 0) - (m.sold_rooms or 0)),
                "bookings": m.bookings or 0,
                "clicks": m.clicks or 0,
            }
        )
    series = []
    for key in sorted(by_date):
        r = by_date[key]
        series.append(
            {
                "date": key,
                "is_future": r["is_future"],
                "revenue": r["revenue"],
                "occupancy": (r["sold"] / r["available"]) if r["available"] else 0,
                "search_rank": round(_avg(r["rank"]), 1),
                "conversion_rate": round((r["bookings"] / r["clicks"]), 4) if r.get("clicks") else 0,
                "adr": round((r["revenue"] / r["sold"]), 1) if r["sold"] else 0,
                "revpar": round((r["revenue"] / r["available"]), 1) if r["available"] else 0,
                "sold": r["sold"],
                "available": r["available"],
                "remaining": max(0, r["available"] - r["sold"]),
                "bookings": r["bookings"],
                "clicks": r["clicks"],
            }
        )
    room_objs = db.query(RoomType).filter(RoomType.hotel_id == hotel.id).order_by(RoomType.id).all()
    views = build_stock_and_pace(metrics, room_objs, today)
    hist = build_history_and_conversion(metrics, room_objs, today)
    return {
        "series": series,
        "by_room": by_room,
        "stock": views["stock"],
        "pace": views["pace"],
        "history": hist["history"],
        "conversion": hist["conversion"],
    }


@router.get("/competitors")
def competitors(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    today = date.today()
    ota = CtripMockAdapter()
    comps = ota.fetch_competitors(db, hotel.id, today - timedelta(days=14), today + timedelta(days=13))
    rooms = {r.id: r.name for r in db.query(RoomType).filter(RoomType.hotel_id == hotel.id).all()}
    metrics = ota.fetch_metrics(db, hotel.id, today - timedelta(days=14), today + timedelta(days=13))
    our = {(m.room_type_id, m.date.isoformat()): m.list_price for m in metrics}
    out = []
    for c in comps:
        key = (c.room_type_id, c.date.isoformat())
        out.append(
            {
                "date": c.date.isoformat(),
                "room": rooms.get(c.room_type_id),
                "room_type_id": c.room_type_id,
                "competitor": c.competitor_name,
                "price": c.price,
                "rank": c.rank,
                "our_list_price": our.get(key),
            }
        )
    return {"rows": out}


@router.get("/rooms")
def rooms(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    today = date.today()
    ota = CtripMockAdapter()
    metrics = ota.fetch_metrics(db, hotel.id, today - timedelta(days=7), today - timedelta(days=1))
    rooms = db.query(RoomType).filter(RoomType.hotel_id == hotel.id).order_by(RoomType.id).all()
    rows = []
    for room in rooms:
        subset = [m for m in metrics if m.room_type_id == room.id and not m.is_future]
        packed = pack_metrics(subset)
        rows.append(
            {
                "id": room.id,
                "name": room.name,
                "code": room.code,
                "inventory": room.inventory,
                "base_price": room.base_price,
                "min_price": room.min_price,
                "max_price": room.max_price,
                "revenue": packed["revenue"],
                "occupancy": packed["occupancy"],
                "adr": packed["adr"],
                "revpar": packed["revpar"],
                "search_rank": packed["search_rank"],
                "conversion_rate": packed["conversion_rate"],
            }
        )
    return {"rooms": rows}


@router.put("/rooms/{room_id}/constraints")
def update_room_constraints(
    room_id: int,
    body: RoomConstraintsUpdate,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(require("update_constraints")),
):
    room = db.get(RoomType, room_id)
    if room is None or room.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="房型不存在")
    if body.min_price <= 0 or body.max_price <= 0:
        raise HTTPException(status_code=400, detail="价格约束必须大于 0")
    if body.min_price > body.max_price:
        raise HTTPException(status_code=400, detail="最低价不能高于最高价")
    before = {"min_price": room.min_price, "max_price": room.max_price}
    room.min_price = round(float(body.min_price), 0)
    room.max_price = round(float(body.max_price), 0)
    db.commit()
    write_audit(
        db,
        actor=user.username,
        action="update_price_constraints",
        entity_type="room_type",
        entity_id=str(room.id),
        before=before,
        after={"min_price": room.min_price, "max_price": room.max_price},
        reason=f"{room.name} 价格约束",
    )
    return {"id": room.id, "name": room.name, "min_price": room.min_price, "max_price": room.max_price}

