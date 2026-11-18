"""L2 stock-pressure and booking-pace views built from stay-date metrics.

These views use on-the-books sold rooms for today and future stay dates, and
channel booking counts for historical days. They do not invent a same-lead-time
year-ago pickup curve, because the demo data has no historical OTB snapshots.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.models import DailyMetric, RoomType
from app.services.kpis import pack_metrics, period_delta

PRESSURE_LABELS = {
    "tight": "库存偏紧",
    "high": "去库存压力高",
    "medium": "去库存压力中等",
    "low": "压力较低",
}


def _iso(d: date) -> str:
    return d.isoformat()


def _occ(sold: int, available: int) -> float:
    return (sold / available) if available else 0.0


def date_pressure(days_out: int, occ: float) -> str:
    if days_out < 0:
        return "low"
    if occ >= 0.90 and days_out <= 3:
        return "tight"
    if occ < 0.35 and days_out <= 3:
        return "high"
    if occ < 0.50 and days_out <= 7:
        return "medium"
    if occ < 0.45 and days_out <= 14:
        return "medium"
    return "low"




def build_stock_and_pace(
    metrics: list[DailyMetric],
    rooms: list[RoomType],
    today: date,
) -> dict[str, Any]:
    room_name = {r.id: r.name for r in rooms}
    # Aggregate stay dates at hotel and room level.
    by_date: dict[date, dict[str, int]] = {}
    by_room_date: dict[tuple[int, date], dict[str, int]] = {}
    for m in metrics:
        row = by_date.setdefault(m.date, {"sold": 0, "available": 0, "bookings": 0, "is_future": 0})
        row["sold"] += int(m.sold_rooms or 0)
        row["available"] += int(m.available_rooms or 0)
        row["bookings"] += int(m.bookings or 0)
        row["is_future"] = 1 if m.is_future else row["is_future"]
        key = (m.room_type_id, m.date)
        cell = by_room_date.setdefault(key, {"sold": 0, "available": 0, "bookings": 0})
        cell["sold"] += int(m.sold_rooms or 0)
        cell["available"] += int(m.available_rooms or 0)
        cell["bookings"] += int(m.bookings or 0)

    horizon_end = today + timedelta(days=13)
    upcoming_dates = [today + timedelta(days=i) for i in range(0, 14) if today + timedelta(days=i) <= horizon_end]

    by_date_out = []
    remaining_7 = 0
    capacity_7 = 0
    sold_7 = 0
    peak = None
    for d in upcoming_dates:
        row = by_date.get(d) or {"sold": 0, "available": 0, "bookings": 0, "is_future": 1}
        sold, available = row["sold"], row["available"]
        remaining = max(0, available - sold)
        occ = _occ(sold, available)
        days_out = (d - today).days
        pressure = date_pressure(days_out, occ)
        item = {
            "date": _iso(d),
            "days_out": days_out,
            "sold": sold,
            "available": available,
            "remaining": remaining,
            "occupancy": round(occ, 4),
            "pressure": pressure,
            "pressure_label": PRESSURE_LABELS[pressure],
            "is_future": d > today,
        }
        by_date_out.append(item)
        if days_out <= 6:
            remaining_7 += remaining
            capacity_7 += available
            sold_7 += sold
        # Prefer unsold-pressure dates; tight is a different kind of risk.
        score = remaining if pressure != "tight" else -sold
        if peak is None or (pressure in {"high", "medium"} and (peak["pressure"] == "low" or score > peak.get("remain_score", -1))):
            peak = {**item, "remain_score": remaining}

    otb_7 = _occ(sold_7, capacity_7)
    if any(x["pressure"] == "high" for x in by_date_out if x["days_out"] <= 6):
        hotel_pressure = "high"
    elif otb_7 < 0.45:
        hotel_pressure = "high"
    elif otb_7 < 0.60 or any(x["pressure"] == "medium" for x in by_date_out if x["days_out"] <= 6):
        hotel_pressure = "medium"
    elif any(x["pressure"] == "tight" for x in by_date_out if x["days_out"] <= 3):
        hotel_pressure = "tight"
    else:
        hotel_pressure = "low"

    by_room_out = []
    for room in rooms:
        rem = cap = sold_n = 0
        soft = None
        for d in upcoming_dates[:7]:
            cell = by_room_date.get((room.id, d)) or {"sold": 0, "available": 0}
            rem += max(0, cell["available"] - cell["sold"])
            cap += cell["available"]
            sold_n += cell["sold"]
            occ = _occ(cell["sold"], cell["available"])
            if occ < 0.50 and (soft is None or occ < soft["occupancy"]):
                soft = {"date": _iso(d), "occupancy": round(occ, 4), "remaining": max(0, cell["available"] - cell["sold"])}
        occ = _occ(sold_n, cap)
        pressure = date_pressure(3, occ) if cap else "low"
        # Room-level uses window occ rather than a single day-out.
        if occ >= 0.90:
            pressure = "tight"
        elif occ < 0.40:
            pressure = "high"
        elif occ < 0.55:
            pressure = "medium"
        else:
            pressure = "low"
        by_room_out.append(
            {
                "room_type_id": room.id,
                "name": room.name,
                "sold": sold_n,
                "available": cap,
                "remaining": rem,
                "occupancy": round(occ, 4),
                "pressure": pressure,
                "pressure_label": PRESSURE_LABELS[pressure],
                "softest": soft,
            }
        )

    findings = [
        f"未来 7 日在手订单出租率 {otb_7 * 100:.1f}%，剩余可售 {remaining_7} 间夜（可售 {capacity_7} 间夜）。"
    ]
    if peak and peak["pressure"] in {"high", "medium"}:
        findings.append(
            f"{peak['date']} 剩余可售 {peak['remaining']} 间，在手出租率 {peak['occupancy'] * 100:.1f}%，{peak['pressure_label']}。"
        )
    tight_days = [x for x in by_date_out if x["pressure"] == "tight"]
    if tight_days:
        findings.append(f"{tight_days[0]['date']} 在手出租率已达 {tight_days[0]['occupancy'] * 100:.1f}%，库存偏紧，需注意满房风险。")
    if by_room_out:
        weakest = min(by_room_out, key=lambda r: r["occupancy"] if r["available"] else 1)
        strongest = max(by_room_out, key=lambda r: r["occupancy"] if r["available"] else 0)
        if weakest["name"] != strongest["name"]:
            findings.append(
                f"{weakest['name']}未来 7 日在手出租率 {weakest['occupancy'] * 100:.1f}%，"
                f"低于{strongest['name']}的 {strongest['occupancy'] * 100:.1f}%，去库存压力更大。"
            )

    # --- Pace: booking flow (historical) + OTB vs last-week same weekday final occ (reference only)
    last7_start, last7_end = today - timedelta(days=7), today - timedelta(days=1)
    prev7_start, prev7_end = today - timedelta(days=14), today - timedelta(days=8)
    def _sum_bookings(start: date, end: date) -> int:
        total = 0
        d = start
        while d <= end:
            total += (by_date.get(d) or {}).get("bookings") or 0
            d += timedelta(days=1)
        return total

    bookings_last7 = _sum_bookings(last7_start, last7_end)
    bookings_prev7 = _sum_bookings(prev7_start, prev7_end)
    today_bookings = (by_date.get(today) or {}).get("bookings") or 0
    same_wd = today - timedelta(days=7)
    last_week_day_bookings = (by_date.get(same_wd) or {}).get("bookings") or 0

    booking_series = []
    d = today - timedelta(days=14)
    while d <= today:
        booking_series.append(
            {
                "date": _iso(d),
                "bookings": (by_date.get(d) or {}).get("bookings") or 0,
                "is_today": d == today,
            }
        )
        d += timedelta(days=1)

    otb_vs_ref = []
    for item in by_date_out:
        stay = date.fromisoformat(item["date"])
        ref_day = stay - timedelta(days=7)
        ref = by_date.get(ref_day)
        # Only use a fully realized past day as the benchmark (not another future OTB point).
        ref_occ = None
        if ref and ref_day < today:
            ref_occ = round(_occ(ref["sold"], ref["available"]), 4)
        otb_vs_ref.append(
            {
                "date": item["date"],
                "otb_occupancy": item["occupancy"],
                "reference_date": _iso(ref_day) if ref_occ is not None else None,
                "reference_occupancy": ref_occ,
                "gap_pp": round((item["occupancy"] - ref_occ) * 100, 1) if ref_occ is not None else None,
            }
        )

    gaps = [x["gap_pp"] for x in otb_vs_ref if x["gap_pp"] is not None]
    avg_gap = sum(gaps) / len(gaps) if gaps else 0.0
    pace_findings = []
    if bookings_prev7:
        delta = bookings_last7 - bookings_prev7
        rel = delta / bookings_prev7
        direction = "快于" if rel > 0.05 else "慢于" if rel < -0.05 else "接近"
        pace_findings.append(
            f"近 7 日渠道新订 {bookings_last7} 单，较前 7 日 {bookings_prev7} 单{direction}"
            + (f"（{delta:+d} 单）。" if direction != "接近" else "。")
        )
    else:
        pace_findings.append(f"近 7 日渠道新订 {bookings_last7} 单。")
    pace_findings.append(
        f"今日新订 {today_bookings} 单，上周同日为 {last_week_day_bookings} 单。"
    )
    if gaps:
        if avg_gap <= -8:
            pace_findings.append(
                f"未来入住日的在手出租率平均比上周同星期最终出租率低 {abs(avg_gap):.1f} 个百分点；该对照并未对齐同一提前天数，只能作为节奏参考。"
            )
        elif avg_gap >= 8:
            pace_findings.append(
                f"未来入住日的在手出租率平均已高于上周同星期最终出租率 {avg_gap:.1f} 个百分点；该对照并未对齐同一提前天数，只能作为节奏参考。"
            )
        else:
            pace_findings.append(
                "未来入住日的在手出租率与上周同星期最终出租率差距不大；该对照并未对齐同一提前天数，只能作为节奏参考。"
            )

    return {
        "stock": {
            "window": {"from": _iso(today), "to": _iso(today + timedelta(days=6))},
            "remaining_nights": remaining_7,
            "capacity_nights": capacity_7,
            "sold_nights": sold_7,
            "otb_occupancy": round(otb_7, 4),
            "pressure": hotel_pressure,
            "pressure_label": PRESSURE_LABELS[hotel_pressure],
            "findings": findings,
            "by_date": by_date_out,
            "by_room": by_room_out,
        },
        "pace": {
            "bookings_last7": bookings_last7,
            "bookings_prev7": bookings_prev7,
            "today_bookings": today_bookings,
            "last_week_same_day_bookings": last_week_day_bookings,
            "booking_series": booking_series,
            "otb_vs_reference": otb_vs_ref,
            "reference_note": "对照为上周同星期的最终出租率，不是同一提前天数的在手订单。演示数据没有历史预售快照，因此不做同比预售曲线。",
            "findings": pace_findings,
        },
    }




def _realized_between(metrics: list[DailyMetric], start: date, end: date, today: date) -> list[DailyMetric]:
    return [
        m
        for m in metrics
        if (not m.is_future) and start <= m.date <= end and m.date <= today
    ]


def build_history_and_conversion(
    metrics: list[DailyMetric],
    rooms: list[RoomType],
    today: date,
) -> dict[str, Any]:
    last7_start = today - timedelta(days=6)
    prev7_start = today - timedelta(days=13)
    prev7_end = today - timedelta(days=7)
    last7 = _realized_between(metrics, last7_start, today, today)
    prev7 = _realized_between(metrics, prev7_start, prev7_end, today)
    last = pack_metrics(last7)
    prev = pack_metrics(prev7)
    delta = period_delta(last, prev) if prev7 else None
    today_rows = _realized_between(metrics, today, today, today)
    ref_day = today - timedelta(days=7)
    ref_rows = _realized_between(metrics, ref_day, ref_day, today)
    today_pack = pack_metrics(today_rows)
    ref_pack = pack_metrics(ref_rows) if ref_rows else None
    today_delta = period_delta(today_pack, ref_pack) if ref_pack else None

    room_name = {r.id: r.name for r in rooms}
    by_room = []
    for room in rooms:
        now_rows = [m for m in last7 if m.room_type_id == room.id]
        old_rows = [m for m in prev7 if m.room_type_id == room.id]
        now_p = pack_metrics(now_rows)
        old_p = pack_metrics(old_rows) if old_rows else None
        by_room.append(
            {
                "room_type_id": room.id,
                "name": room.name,
                "last7": now_p,
                "prev7": old_p,
                "delta": period_delta(now_p, old_p) if old_p else None,
            }
        )

    hist_findings = []
    if prev7 and delta is not None:
        hist_findings.append(
            f"近 7 日（含今日在途）营收 ¥{last['revenue']:,}，较前 7 日 ¥{prev['revenue']:,} "
            f"{'增加' if delta['revenue'] > 0 else '减少' if delta['revenue'] < 0 else '持平'} {abs(delta['revenue'])}%。"
        )
        hist_findings.append(
            f"近 7 日出租率 {last['occupancy'] * 100:.1f}%，较前 7 日 {delta['occupancy_pp']:+.1f} 个百分点；"
            f"转化率 {last['conversion_rate'] * 100:.2f}%，较前 7 日 {delta['conversion_pp']:+.2f} 个百分点。"
        )
    else:
        hist_findings.append(f"近 7 日（含今日在途）营收 ¥{last['revenue']:,}，出租率 {last['occupancy'] * 100:.1f}%。")
    if today_delta and ref_pack:
        hist_findings.append(
            f"今日营收 ¥{today_pack['revenue']:,}，较上周同日（{ref_day.isoformat()}）{today_delta['revenue']:+.1f}%；"
            f"出租率 {today_pack['occupancy'] * 100:.1f}%，{today_delta['occupancy_pp']:+.1f} 个百分点。"
        )
    drops = [r for r in by_room if r.get("delta") and r["delta"]["revenue"] < 0]
    if drops:
        weak = min(drops, key=lambda r: r["delta"]["revenue"])
        hist_findings.append(
            f"{weak['name']}近 7 日营收较前 7 日下降 {abs(weak['delta']['revenue'])}%，在各房型中回落最明显。"
        )

    # Conversion series: last 14 realized days through today, volume-weighted by day.
    series_start = today - timedelta(days=13)
    conv_series = []
    day = series_start
    while day <= today:
        rows = _realized_between(metrics, day, day, today)
        packed = pack_metrics(rows)
        conv_series.append(
            {
                "date": _iso(day),
                "bookings": packed["orders"],
                "clicks": packed["pageviews"],
                "conversion_rate": packed["conversion_rate"],
            }
        )
        day += timedelta(days=1)

    conv_by_room = []
    for room in rooms:
        now_rows = [m for m in last7 if m.room_type_id == room.id]
        packed = pack_metrics(now_rows)
        conv_by_room.append(
            {
                "room_type_id": room.id,
                "name": room.name,
                "bookings": packed["orders"],
                "clicks": packed["pageviews"],
                "conversion_rate": packed["conversion_rate"],
            }
        )

    conv_findings = []
    if prev7 and delta is not None:
        conv_findings.append(
            f"近 7 日携程转化率 {last['conversion_rate'] * 100:.2f}%（预订 {last['orders']} 单 / 浏览 {last['pageviews']} 次），"
            f"较前 7 日 {delta['conversion_pp']:+.2f} 个百分点。"
        )
        conv_findings.append(
            f"近 7 日浏览量 {last['pageviews']} 次，较前 7 日 {delta['pageviews']:+.1f}%；"
            f"预订 {last['orders']} 单，较前 7 日 {delta['orders']:+.1f}%。"
        )
    else:
        conv_findings.append(
            f"近 7 日携程转化率 {last['conversion_rate'] * 100:.2f}%（预订 {last['orders']} 单 / 浏览 {last['pageviews']} 次）。"
        )
    if conv_by_room and any(r["clicks"] for r in conv_by_room):
        weakest = min(conv_by_room, key=lambda r: r["conversion_rate"] if r["clicks"] else 99)
        if weakest["clicks"]:
            conv_findings.append(
                f"{weakest['name']}近 7 日转化率 {weakest['conversion_rate'] * 100:.2f}%，在各房型中最低。"
            )

    return {
        "history": {
            "last7": last,
            "prev7": prev,
            "delta": delta,
            "window": {"last7_from": _iso(last7_start), "last7_to": _iso(today), "prev7_from": _iso(prev7_start), "prev7_to": _iso(prev7_end)},
            "today": today_pack,
            "reference_date": _iso(ref_day) if ref_pack else None,
            "reference": ref_pack,
            "today_delta": today_delta,
            "by_room": by_room,
            "findings": hist_findings,
            "reference_note": "近 7 日含今日在途经营日，对照为前 7 日及上周同日。演示数据没有完整年度历史，因此不做同比。",
        },
        "conversion": {
            "channel": "携程",
            "last7": {"conversion_rate": last["conversion_rate"], "bookings": last["orders"], "clicks": last["pageviews"]},
            "prev7": {"conversion_rate": prev["conversion_rate"], "bookings": prev["orders"], "clicks": prev["pageviews"]},
            "delta_pp": delta["conversion_pp"] if delta else None,
            "series": conv_series,
            "by_room": conv_by_room,
            "findings": conv_findings,
            "reference_note": "本轮只有携程模拟渠道，这里展示该渠道浏览量到预订的转化，不是多渠道对比。浏览量是列表页点击，不是曝光次数。转化率按预订合计除以浏览合计计算，不用每日转化率再平均。",
        },
    }


def enrich_timeseries_points(points: list[dict], date_aggs: dict[str, dict]) -> None:
    """Add sold / available / remaining / bookings onto hotel-level series points."""
    for p in points:
        row = date_aggs.get(p["date"]) or {}
        sold = int(row.get("sold") or 0)
        available = int(row.get("available") or 0)
        p["sold"] = sold
        p["available"] = available
        p["remaining"] = max(0, available - sold)
        p["bookings"] = int(row.get("bookings") or 0)
        p["clicks"] = int(row.get("clicks") or 0)
        if p["clicks"]:
            p["conversion_rate"] = round(p["bookings"] / p["clicks"], 4)
