"""Heuristic diagnosis: classify short-term fluctuation vs structural issues."""

from __future__ import annotations

from app.wording import from_to_phrase, rank_from_to

from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

from app.models import Alert, CompetitorSnapshot, DailyMetric, Review, RoomType
from app.services.kpis import pack_metrics


def _median(xs: list[float], default: float = 0.0) -> float:
    return float(median(xs)) if xs else default


def collect_signals(
    today: date,
    rooms: list[RoomType],
    metrics: list[DailyMetric],
    competitors: list[CompetitorSnapshot],
    reviews: list[Review],
    alerts: list[Alert] | None = None,
    include_as_of: bool = False,
) -> dict[str, Any]:
    # Default window (alerts / workflow): the 14 days strictly before as_of.
    # Date diagnosis includes the selected day itself in the recent week.
    if include_as_of:
        history_start = today - timedelta(days=13)
        decline_start = today - timedelta(days=6)
    else:
        history_start = today - timedelta(days=14)
        decline_start = today - timedelta(days=7)
    by_room: dict[int, list[DailyMetric]] = defaultdict(list)
    for m in metrics:
        if m.is_future:
            continue
        if include_as_of:
            in_window = history_start <= m.date <= today
        else:
            in_window = history_start <= m.date < today
        if in_window:
            by_room[m.room_type_id].append(m)

    rank_stories = []
    conv_stories = []
    occ_stories = []
    for room in rooms:
        series = sorted(by_room.get(room.id, []), key=lambda x: x.date)
        if len(series) < 6:
            continue
        healthy = [m for m in series if m.date < decline_start]
        recent = [m for m in series if m.date >= decline_start]
        if not healthy or not recent:
            continue
        r0 = int(_median([m.search_rank for m in healthy], 10))
        r1 = int(_median([m.search_rank for m in recent[-3:]], r0))
        prev_c = pack_metrics(healthy)
        last_c = pack_metrics(recent)
        c0 = prev_c["conversion_rate"]
        c1 = last_c["conversion_rate"]
        o0 = prev_c["occupancy"]
        o1 = last_c["occupancy"]
        rank_stories.append({"room": room.name, "from": r0, "to": r1, "delta": r1 - r0})
        conv_stories.append({"room": room.name, "from": round(c0 * 100, 2), "to": round(c1 * 100, 2)})
        occ_stories.append({"room": room.name, "from": round(o0 * 100, 1), "to": round(o1 * 100, 1)})

    # Competitor cuts: compare last 3 days vs days 11-14 ago.
    cuts = []
    names = sorted({c.competitor_name for c in competitors})
    for name in names:
        old = [c.price for c in competitors if c.competitor_name == name and c.date < decline_start]
        new = [c.price for c in competitors if c.competitor_name == name and c.date >= decline_start]
        if old and new:
            o, n = _median(old), _median(new)
            pct = (o - n) / o * 100 if o else 0
            cuts.append({"name": name, "from": round(o, 0), "to": round(n, 0), "cut_pct": round(pct, 1)})

    # Price inversion: our list price sits clearly above the competitor median.
    inversions = []
    for room in rooms:
        series = sorted(by_room.get(room.id, []), key=lambda x: x.date)
        recent = [m for m in series if m.date >= decline_start]
        if not recent:
            continue
        our = _median([m.list_price for m in recent if m.list_price], 0)
        comp = [
            c.price
            for c in competitors
            if c.room_type_id == room.id and c.date >= decline_start
        ]
        if not our or not comp:
            continue
        mid = _median(comp, our)
        gap_pct = (our - mid) / mid * 100 if mid else 0
        if gap_pct >= 8:
            inversions.append(
                {
                    "room": room.name,
                    "our_price": round(our, 0),
                    "competitor_median": round(mid, 0),
                    "gap_pct": round(gap_pct, 1),
                }
            )

    negatives = [
        {
            "id": r.id,
            "date": r.review_date.isoformat(),
            "rating": r.rating,
            "guest_name": r.guest_name,
            "content": r.content,
        }
        for r in reviews
        if r.rating <= 2 and r.review_date >= today - timedelta(days=14)
    ]

    flags = []
    if any(s["delta"] >= 5 for s in rank_stories):
        flags.append("rank_drop")
    if any(c["cut_pct"] >= 8 for c in cuts):
        flags.append("competitor_cut")
    if negatives:
        flags.append("negative_review")
    hotel_prev = pack_metrics([m for rows in by_room.values() for m in rows if m.date < decline_start])
    hotel_last = pack_metrics([m for rows in by_room.values() for m in rows if m.date >= decline_start])
    hotel_conv_from = round(hotel_prev["conversion_rate"] * 100, 2)
    hotel_conv_to = round(hotel_last["conversion_rate"] * 100, 2)
    if (hotel_prev["conversion_rate"] - hotel_last["conversion_rate"]) * 100 >= 0.6:
        flags.append("conversion_drop")
    if inversions:
        flags.append("price_inversion")

    # Structural if the drop persists across most of the last 7 days and has a competitor cause.
    structural = "rank_drop" in flags and "competitor_cut" in flags
    classification = "structural" if structural else "short_term"

    focus = max(rank_stories, key=lambda s: s["delta"], default={"room": "豪华大床房", "from": 9, "to": 23})
    avg_cut = round(_median([c["cut_pct"] for c in cuts], 12.0), 1)
    conv_focus = {"from": hotel_conv_from, "to": hotel_conv_to}

    return {
        "as_of": today.isoformat(),
        "classification": classification,
        "flags": flags,
        "rank_trend": {"room": focus["room"], "from": focus.get("from", 9), "to": focus.get("to", 23)},
        "rank_stories": rank_stories,
        "conversion": conv_stories,
        "occupancy": occ_stories,
        "competitor_cuts": cuts,
        "competitor_cut_pct": avg_cut,
        "negative_reviews": negatives,
        "focus_room": focus["room"],
        "conversion_from": conv_focus.get("from"),
        "conversion_to": conv_focus.get("to"),
        "negative_review": (
            negatives[0]["content"] if negatives else "近14日暂无2分及以下评价"
        ),
        "price_inversions": inversions,
    }


def heuristic_diagnosis(signals: dict[str, Any]) -> dict[str, Any]:
    classification = signals.get("classification") or "structural"
    causes = []
    if "competitor_cut" in signals.get("flags", []):
        causes.append(
            {
                "factor": "竞对降价",
                "contribution": "high",
                "detail": "、".join(
                    f"{c['name']}下调约{c['cut_pct']}%" for c in signals.get("competitor_cuts", [])[:3]
                )
                or "主要竞对近一周下调售价",
            }
        )
    if "rank_drop" in signals.get("flags", []):
        rt = signals.get("rank_trend") or {}
        causes.append(
            {
                "factor": "我方价格未跟进，排名下滑",
                "contribution": "high",
                "detail": f"{rt.get('room', '')}搜索排名{rank_from_to(rt.get('from'), rt.get('to'))}",
            }
        )
    if "negative_review" in signals.get("flags", []):
        causes.append(
            {
                "factor": "差评影响转化",
                "contribution": "medium",
                "detail": (signals.get("negative_reviews") or [{}])[0].get("content", "存在差评"),
            }
        )
    if "price_inversion" in signals.get("flags", []):
        inv = (signals.get("price_inversions") or [{}])[0]
        causes.append(
            {
                "factor": "价格倒挂",
                "contribution": "high",
                "detail": (
                    f"{inv.get('room', '')}挂牌价约¥{inv.get('our_price')}，"
                    f"高于竞对中位¥{inv.get('competitor_median')}约 {inv.get('gap_pct')}%"
                ),
            }
        )
    if "conversion_drop" in signals.get("flags", []):
        causes.append(
            {
                "factor": "转化率走弱",
                "contribution": "medium",
                "detail": f"转化率{from_to_phrase(signals.get('conversion_from'), signals.get('conversion_to'), '%')}",
            }
        )
    flags = signals.get("flags") or []
    if classification == "structural":
        if "conversion_drop" in flags:
            not_causes = ["单纯周末需求波动不足以解释连续多日的排名与转化同步下滑"]
        else:
            not_causes = ["单纯周末需求波动不足以解释连续多日的排名下滑与价格倒挂"]
    else:
        not_causes = ["需继续观察是否为节假日或天气造成的短暂波动"]
    return {
        "classification": classification,
        "classification_label": "结构性问题" if classification == "structural" else "短期波动",
        "primary_causes": causes,
        "not_causes": not_causes,
        "signals": signals,
    }


class DiagnosisError(ValueError):
    """User-facing reason why a date cannot be diagnosed."""


def _rows_on(metrics: list[DailyMetric], day: date) -> list[DailyMetric]:
    return [m for m in metrics if m.date == day]


def day_snapshot(metrics: list[DailyMetric], rooms: list[RoomType], day: date, operating_today: date) -> dict[str, Any]:
    from app.services.kpis import pack_metrics, period_delta

    rows = _rows_on(metrics, day)
    packed = pack_metrics(rows)
    ref_day = day - timedelta(days=7)
    ref_rows = [m for m in _rows_on(metrics, ref_day) if not m.is_future]
    ref = pack_metrics(ref_rows) if ref_rows else None
    by_room = []
    room_name = {r.id: r.name for r in rooms}
    room_ids = sorted({m.room_type_id for m in rows})
    for rid in room_ids:
        mine = [m for m in rows if m.room_type_id == rid]
        item = pack_metrics(mine)
        item["room_type_id"] = rid
        item["name"] = room_name.get(rid, str(rid))
        by_room.append(item)
    return {
        "date": day.isoformat(),
        "in_progress": day == operating_today,
        "metrics": packed,
        "reference_date": ref_day.isoformat() if ref else None,
        "reference_metrics": ref,
        "delta": period_delta(packed, ref) if ref else None,
        "reference_label": "上周同日" if ref else None,
        "by_room": by_room,
        "has_realized_metrics": bool(rows) and all(not m.is_future for m in rows),
    }


def diagnose_for_date(
    as_of: date,
    rooms: list[RoomType],
    metrics: list[DailyMetric],
    competitors: list[CompetitorSnapshot],
    reviews: list[Review],
    alerts: list[Alert] | None,
    operating_today: date,
) -> dict[str, Any]:
    if as_of > operating_today:
        raise DiagnosisError("未来入住日只有在手订单，没有已实现的排名和转化，不能做因果诊断。请选择今天或更早的日期。")
    rows = _rows_on(metrics, as_of)
    if not rows:
        raise DiagnosisError("这一天没有经营数据，无法诊断。")
    if any(m.is_future for m in rows):
        raise DiagnosisError("这一天还是未来入住日，只有在手订单，不能做已实现指标的因果诊断。请选择今天或更早的日期。")

    signals = collect_signals(as_of, rooms, metrics, competitors, reviews, alerts, include_as_of=True)
    diagnosis = heuristic_diagnosis(signals)
    nearby = []
    for a in alerts or []:
        if a.metric_date is None:
            continue
        if abs((a.metric_date - as_of).days) <= 3:
            nearby.append(
                {
                    "id": a.id,
                    "alert_type": a.alert_type,
                    "severity": a.severity,
                    "title": a.title,
                    "status": a.status,
                    "metric_date": a.metric_date.isoformat(),
                }
            )
    nearby.sort(key=lambda x: x["metric_date"], reverse=True)
    diagnosis["as_of"] = as_of.isoformat()
    diagnosis["scope"] = "date"
    diagnosis["snapshot"] = day_snapshot(metrics, rooms, as_of, operating_today)
    diagnosis["related_alerts"] = nearby
    diagnosis["window_note"] = (
        "信号窗口为该日及此前 13 天的已实现数据；近 7 日对照前 7 日，当日指标另与上周同日对照。"
        "这不是同比预售曲线，也不使用未实现的未来入住日数据。"
    )
    return diagnosis

