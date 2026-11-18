from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.adapters.ota.ctrip_mock import CtripMockAdapter
from app.api.deps import current_hotel
from app.auth import get_current_user
from app.database import get_db
from app.gateway.protocol import LLMRequest
from app.gateway.router import get_gateway
from app.models import Alert, Hotel, RoomType, User
from app.services.diagnosis import collect_signals, heuristic_diagnosis
from app.services.workflow import write_audit

router = APIRouter(prefix="/api/alerts", tags=["alerts"], dependencies=[Depends(get_current_user)])


def _dump(alert: Alert) -> dict:
    return {
        "id": alert.id,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "title": alert.title,
        "body": alert.body,
        "metric_date": alert.metric_date.isoformat() if alert.metric_date else None,
        "payload": alert.payload,
        "status": alert.status,
        "diagnosis": alert.diagnosis,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
    }


@router.get("")
def list_alerts(db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    rows = db.query(Alert).filter(Alert.hotel_id == hotel.id).order_by(Alert.id).all()
    return {"alerts": [_dump(a) for a in rows]}


@router.get("/{alert_id}")
def get_alert(alert_id: int, db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel)):
    alert = db.get(Alert, alert_id)
    if alert is None or alert.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="预警不存在")
    return _dump(alert)


@router.post("/{alert_id}/diagnose")
def diagnose(
    alert_id: int,
    db: Session = Depends(get_db),
    hotel: Hotel = Depends(current_hotel),
    user: User = Depends(get_current_user),
):
    alert = db.get(Alert, alert_id)
    if alert is None or alert.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="预警不存在")
    if alert.alert_type in {"write_failed", "reconcile_mismatch"}:
        from app.services.writes import honest_write_alert_diagnosis

        diagnosis = honest_write_alert_diagnosis(alert)
        alert.diagnosis = diagnosis
        db.commit()
        write_audit(
            db,
            actor=user.username,
            action="diagnose_alert",
            entity_type="alert",
            entity_id=str(alert.id),
            after={"classification": diagnosis.get("classification")},
            reason=alert.title,
        )
        return _dump(alert)
    today = date.today()
    ota = CtripMockAdapter()
    rooms = db.query(RoomType).filter(RoomType.hotel_id == hotel.id).all()
    metrics = ota.fetch_metrics(db, hotel.id, today - timedelta(days=14), today + timedelta(days=13))
    comps = ota.fetch_competitors(db, hotel.id, today - timedelta(days=14), today + timedelta(days=13))
    reviews = ota.fetch_reviews(db, hotel.id)
    signals = collect_signals(today, rooms, metrics, comps, reviews)
    diagnosis = heuristic_diagnosis(signals)
    facts = {
        "hotel_name": hotel.name,
        "alert": {"type": alert.alert_type, "title": alert.title, "body": alert.body},
        "rank_trend": signals.get("rank_trend"),
        "focus_room": signals.get("focus_room"),
        "competitor_cut_pct": signals.get("competitor_cut_pct"),
        "conversion_from": signals.get("conversion_from"),
        "conversion_to": signals.get("conversion_to"),
        "negative_review": signals.get("negative_review"),
        "classification": diagnosis.get("classification"),
        "flags": signals.get("flags") or [],
        "primary_causes": diagnosis.get("primary_causes"),
        "not_causes": diagnosis.get("not_causes"),
        "price_inversions": signals.get("price_inversions"),
    }
    resp = get_gateway().complete(
        LLMRequest(
            task_type="analysis",
            hotel_id=hotel.id,
            messages=[
                {"role": "system", "content": "你是酒店 OTA 收益分析官。根据事实解释这条预警，区分短期波动与结构性问题。不要改写分类或编造数字。只有 flags 含 conversion_drop 时才能写转化走弱或同步下滑。"},
                {"role": "user", "content": "请诊断该预警。\nFACTS_JSON: " + json.dumps(facts, ensure_ascii=False)},
            ],
        ),
        db=db,
    )
    diagnosis["narrative"] = resp.content
    diagnosis["degraded"] = resp.degraded
    diagnosis["degrade_notice"] = resp.degrade_notice
    alert.diagnosis = diagnosis
    db.commit()
    write_audit(
        db,
        actor=user.username,
        action="diagnose_alert",
        entity_type="alert",
        entity_id=str(alert.id),
        after={"classification": diagnosis.get("classification")},
        reason=alert.title,
    )
    return _dump(alert)


@router.post("/{alert_id}/ack")
def ack(alert_id: int, db: Session = Depends(get_db), hotel: Hotel = Depends(current_hotel), user: User = Depends(get_current_user)):
    alert = db.get(Alert, alert_id)
    if alert is None or alert.hotel_id != hotel.id:
        raise HTTPException(status_code=404, detail="预警不存在")
    alert.status = "acknowledged"
    db.commit()
    write_audit(db, actor=user.username, action="ack_alert", entity_type="alert", entity_id=str(alert.id))
    return _dump(alert)
