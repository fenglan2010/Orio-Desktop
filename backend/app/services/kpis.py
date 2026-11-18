"""Hotel revenue-management KPI packing.

Formulas follow common room-revenue (客房收入) definitions used in
STR-style daily flash reports and domestic OTA operating dashboards:

- 客房收入 revenue = sum of room revenue, excluding F&B and other
  non-room charges.
- OCC  = rooms sold / rooms available
- ADR  = room revenue / rooms sold
- RevPAR = room revenue / rooms available  (= ADR × OCC)
- 浏览量 = listing page clicks (点击/浏览), not impressions
- 转化率 = bookings / page clicks

Period occupancy, ADR, RevPAR and conversion are always inventory- or
volume-weighted (sums first, then divide). Never an unweighted average
of daily or room-type ratios.

A week starts on Monday (ISO / 国内惯例). 本周营收 is week-to-date
(Monday through today). Daily flash metrics compare to the same weekday
last week (上周同日) to avoid weekday seasonality; WTD compares to the
same Monday–weekday span last week (上周同期).
"""

from __future__ import annotations

from datetime import date, timedelta


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def in_range(rows, start: date, end: date, *, include_future: bool = False):
    out = []
    for m in rows:
        if m.date < start or m.date > end:
            continue
        if getattr(m, "is_future", False) and not include_future:
            continue
        out.append(m)
    return out


def pack_metrics(rows) -> dict:
    revenue = float(sum(getattr(m, "revenue", 0) or 0 for m in rows))
    sold = int(sum(getattr(m, "sold_rooms", 0) or 0 for m in rows))
    available = int(sum(getattr(m, "available_rooms", 0) or 0 for m in rows))
    bookings = int(sum(getattr(m, "bookings", 0) or 0 for m in rows))
    clicks = int(sum(getattr(m, "clicks", 0) or 0 for m in rows))
    impressions = int(sum(getattr(m, "impressions", 0) or 0 for m in rows))
    occ = (sold / available) if available else 0.0
    adr = (revenue / sold) if sold else 0.0
    revpar = (revenue / available) if available else 0.0
    conv = (bookings / clicks) if clicks else 0.0
    ranks = [m.search_rank for m in rows if getattr(m, "search_rank", None)]
    return {
        "revenue": int(round(revenue)),
        "occupancy": round(occ, 4),
        "adr": round(adr, 1),
        "revpar": round(revpar, 1),
        "sold_rooms": sold,
        "available_rooms": available,
        "orders": bookings,
        "pageviews": clicks,
        "impressions": impressions,
        "conversion_rate": round(conv, 4),
        "search_rank": round(sum(ranks) / len(ranks), 1) if ranks else 0.0,
    }


def _pct_change(now: float, prev: float) -> float:
    if not prev:
        return 0.0
    return round((now - prev) / prev * 100, 1)


def _pp_change(now: float, prev: float) -> float:
    """Percentage-point change for ratios stored as 0–1."""
    return round((now - prev) * 100, 1)


def period_delta(now: dict, prev: dict) -> dict:
    return {
        "revenue": _pct_change(now["revenue"], prev["revenue"]),
        "adr": _pct_change(now["adr"], prev["adr"]),
        "revpar": _pct_change(now["revpar"], prev["revpar"]),
        "occupancy": _pct_change(now["occupancy"], prev["occupancy"]),
        "occupancy_pp": _pp_change(now["occupancy"], prev["occupancy"]),
        "orders": _pct_change(now["orders"], prev["orders"]),
        "pageviews": _pct_change(now["pageviews"], prev["pageviews"]),
        "conversion_rate": _pct_change(now["conversion_rate"], prev["conversion_rate"]),
        "conversion_pp": _pp_change(now["conversion_rate"], prev["conversion_rate"]),
        "search_rank": round(now["search_rank"] - prev["search_rank"], 1),
    }


def flash_windows(today: date) -> dict:
    mon = week_start(today)
    return {
        "week_start": mon,
        "week_end": today,
        "prev_week_start": mon - timedelta(days=7),
        "prev_week_end": today - timedelta(days=7),
        "today": today,
        "same_weekday_last_week": today - timedelta(days=7),
        "last7_start": today - timedelta(days=7),
        "last7_end": today - timedelta(days=1),
        "prev7_start": today - timedelta(days=14),
        "prev7_end": today - timedelta(days=8),
    }
