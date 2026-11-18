"""Operations assistant: natural-language briefing over live hotel data."""

from __future__ import annotations

from app.wording import from_to_phrase, rank_from_to
import json
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.gateway.protocol import LLMRequest
from app.gateway.router import get_gateway
from app.models import Alert, ChatMessage, Hotel, Review, RoomType, Strategy
from app.adapters.ota.ctrip_mock import CtripMockAdapter
from app.services.diagnosis import collect_signals
from app.services.kpis import pack_metrics, period_delta


def strategy_title(s: Strategy, rooms: dict[int, str] | None = None) -> str:
    if s.kind == "campaign":
        name = (s.campaign_advice or {}).get("campaign_name")
        return f"活动建议：{name}" if name else "活动参与建议"
    room = (rooms or {}).get(s.room_type_id) if s.room_type_id else ""
    if s.recommended_price:
        label = f"{room} " if room else ""
        return f"调价建议：{label}→ ¥{int(round(s.recommended_price))}".replace("  ", " ")
    return f"调价建议：{room}" if room else "调价建议"


def build_chat_context(db: Session, hotel: Hotel) -> dict:
    today = date.today()
    rooms = db.query(RoomType).filter(RoomType.hotel_id == hotel.id).order_by(RoomType.id).all()
    ota = CtripMockAdapter()
    metrics = ota.fetch_metrics(db, hotel.id, today - timedelta(days=14), today + timedelta(days=13))
    comps = ota.fetch_competitors(db, hotel.id, today - timedelta(days=14), today + timedelta(days=13))
    reviews = ota.fetch_reviews(db, hotel.id)
    signals = collect_signals(today, rooms, metrics, comps, reviews)
    realized_today = [m for m in metrics if m.date == today and not m.is_future]
    prev_day = [m for m in metrics if m.date == today - timedelta(days=7) and not m.is_future]
    last7 = [m for m in metrics if (not m.is_future) and today - timedelta(days=6) <= m.date <= today]
    prev7 = [m for m in metrics if (not m.is_future) and today - timedelta(days=13) <= m.date <= today - timedelta(days=7)]
    today_p = pack_metrics(realized_today)
    week_p = pack_metrics(last7)
    prev_p = pack_metrics(prev7)
    week_delta = period_delta(week_p, prev_p) if prev7 else None
    today_delta = period_delta(today_p, pack_metrics(prev_day)) if prev_day else None
    alerts = (
        db.query(Alert)
        .filter(Alert.hotel_id == hotel.id, Alert.status == "open")
        .order_by(Alert.id)
        .all()
    )
    strategies = (
        db.query(Strategy)
        .filter(Strategy.hotel_id == hotel.id, Strategy.status == "pending_review")
        .order_by(Strategy.id.desc())
        .limit(5)
        .all()
    )
    room_names = {r.id: r.name for r in rooms}
    pending_rows = [
        {
            "id": s.id,
            "kind": s.kind,
            "title": strategy_title(s, room_names),
            "risk_level": s.risk_level,
            "status": s.status,
        }
        for s in strategies
    ]
    alert_rows = [{"id": a.id, "type": a.alert_type, "title": a.title, "severity": a.severity} for a in alerts]
    flags = "、".join(signals.get("flags") or []) or "暂无"
    lines = [
        f"{hotel.name}（{hotel.platform} 模拟）截至 {today.isoformat()} 的简报：",
        (
            f"今日营收 ¥{today_p['revenue']:,}，出租率 {today_p['occupancy'] * 100:.1f}%，"
            f"转化率 {today_p['conversion_rate'] * 100:.2f}%，搜索排名 {today_p['search_rank']:.0f}。"
        ),
        (
            f"近 7 日营收 ¥{week_p['revenue']:,}，出租率 {week_p['occupancy'] * 100:.1f}%，"
            f"转化率 {week_p['conversion_rate'] * 100:.2f}%（预订 {week_p['orders']} 单 / 浏览 {week_p['pageviews']} 次）。"
        ),
        f"当前信号：{flags}。",
    ]
    if today_delta:
        lines.append(
            f"今日营收较上周同日 {today_delta['revenue']:+.1f}%，出租率 {today_delta['occupancy_pp']:+.1f} 个百分点。"
        )
    rt = signals.get("rank_trend") or {}
    if rt:
        lines.append(f"搜索排名：{rt.get('room')} {rank_from_to(rt.get('from'), rt.get('to'))}。")
    inv = (signals.get("price_inversions") or [None])[0]
    if inv:
        lines.append(
            f"价格倒挂：{inv['room']} 挂牌价约¥{inv['our_price']}，高于竞对中位¥{inv['competitor_median']}约 {inv['gap_pct']}%。"
        )
    if signals.get("conversion_from") is not None:
        lines.append(f"转化率{from_to_phrase(signals.get('conversion_from'), signals.get('conversion_to'), '%')}。")
    if signals.get("negative_reviews"):
        lines.append("近14日存在差评，建议先做人审回复。")
    if alert_rows:
        lines.append("未关闭预警：" + "；".join(a["title"] for a in alert_rows) + "。")
    else:
        lines.append("当前没有未关闭预警。")
    if pending_rows:
        lines.append(
            "待人审策略："
            + "；".join(f"#{s['id']} {s['title']}" for s in pending_rows)
            + "。"
        )
    else:
        lines.append("当前没有待人审策略。")
    return {
        "hotel_name": hotel.name,
        "as_of": today.isoformat(),
        "briefing": "\n".join(lines),
        "kpis": {
            "today": today_p,
            "last7": week_p,
            "prev7": prev_p,
            "today_delta": today_delta,
            "last7_delta": week_delta,
        },
        "flags": signals.get("flags") or [],
        "rank_trend": rt,
        "price_inversions": signals.get("price_inversions") or [],
        "alerts": alert_rows,
        "pending_strategies": pending_rows,
    }


def chat(db: Session, hotel: Hotel, user_text: str) -> ChatMessage:
    db.add(ChatMessage(hotel_id=hotel.id, role="user", content=user_text))
    db.commit()
    facts = build_chat_context(db, hotel)
    resp = get_gateway().complete(
        LLMRequest(
            task_type="assistant_chat",
            hotel_id=hotel.id,
            messages=[
                {
                    "role": "system",
                    "content": "你是 OIRO 收益运营助手。只根据提供的经营事实回答，不编造数据，不直接执行调价或回复评价。",
                },
                {
                    "role": "user",
                    "content": user_text + "\nFACTS_JSON: " + json.dumps(facts, ensure_ascii=False),
                },
            ],
        ),
        db=db,
    )
    notice = f"\n\n（{resp.degrade_notice}）" if resp.degraded and resp.degrade_notice else ""
    assistant = ChatMessage(
        hotel_id=hotel.id,
        role="assistant",
        content=resp.content + notice,
        meta={
            "vendor": resp.vendor,
            "model": resp.model,
            "degraded": resp.degraded,
            "outbound": resp.outbound,
        },
    )
    db.add(assistant)
    db.commit()
    db.refresh(assistant)
    return assistant
