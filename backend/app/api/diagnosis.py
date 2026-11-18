from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.adapters.ota.ctrip_mock import CtripMockAdapter
from app.api.deps import current_hotel
from app.auth import get_current_user
from app.database import get_db
from app.gateway.protocol import LLMRequest
from app.gateway.router import get_gateway
from app.models import Alert, Hotel, RoomType, User
from app.services.diagnosis import DiagnosisError, diagnose_for_date
from app.services.workflow import write_audit

router = APIRouter(prefix="/api/diagnosis", tags=["diagnosis"], dependencies=[Depends(get_current_user)])


@router.get("")
def diagnose_date(
    date_str: date | None = Query(None, alias="date"),
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(get_current_user),
):
    operating_today = date.today()
    as_of = date_str or operating_today
    ota = CtripMockAdapter()
    rooms = db.query(RoomType).filter(RoomType.hotel_id == hotel.id).order_by(RoomType.id).all()
    metrics = ota.fetch_metrics(db, hotel.id, as_of - timedelta(days=21), as_of + timedelta(days=1))
    comps = ota.fetch_competitors(db, hotel.id, as_of - timedelta(days=21), as_of + timedelta(days=1))
    reviews = ota.fetch_reviews(db, hotel.id)
    alerts = db.query(Alert).filter(Alert.hotel_id == hotel.id).order_by(Alert.id).all()
    try:
        diagnosis = diagnose_for_date(as_of, rooms, metrics, comps, reviews, alerts, operating_today)
    except DiagnosisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    facts = {
        "hotel_name": hotel.name,
        "as_of": diagnosis.get("as_of"),
        "scope": "date",
        "snapshot": diagnosis.get("snapshot"),
        "rank_trend": (diagnosis.get("signals") or {}).get("rank_trend"),
        "focus_room": (diagnosis.get("signals") or {}).get("focus_room"),
        "competitor_cut_pct": (diagnosis.get("signals") or {}).get("competitor_cut_pct"),
        "conversion_from": (diagnosis.get("signals") or {}).get("conversion_from"),
        "conversion_to": (diagnosis.get("signals") or {}).get("conversion_to"),
        "negative_review": (diagnosis.get("signals") or {}).get("negative_review"),
        "classification": diagnosis.get("classification"),
        "flags": (diagnosis.get("signals") or {}).get("flags") or [],
        "primary_causes": diagnosis.get("primary_causes"),
        "not_causes": diagnosis.get("not_causes"),
        "price_inversions": (diagnosis.get("signals") or {}).get("price_inversions"),
    }
    resp = get_gateway().complete(
        LLMRequest(
            task_type="analysis",
            hotel_id=hotel.id,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是酒店 OTA 收益分析官。根据事实解释选定经营日的表现，"
                        "区分短期波动与结构性问题。不要改写分类或编造没有出现在事实中的数字。"
                        "只有 flags 含 conversion_drop 时才能写转化走弱或同步下滑；否则必须写明合计转化并未同步走弱。"
                    ),
                },
                {"role": "user", "content": "请诊断该经营日。\nFACTS_JSON: " + json.dumps(facts, ensure_ascii=False)},
            ],
        ),
        db=db,
    )
    diagnosis["narrative"] = resp.content
    diagnosis["degraded"] = resp.degraded
    diagnosis["degrade_notice"] = resp.degrade_notice
    write_audit(
        db,
        actor=user.username,
        action="diagnose_date",
        entity_type="diagnosis",
        entity_id=as_of.isoformat(),
        after={"classification": diagnosis.get("classification")},
        reason=f"{hotel.name} {as_of.isoformat()} 日期诊断",
    )
    return diagnosis
