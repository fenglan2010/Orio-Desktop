"""Interpretable heuristic pricing and campaign advice. The LLM does not invent the price."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

from app.models import CompetitorSnapshot, DailyMetric, RoomType


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _round_price(value: float) -> int:
    # Hotel list prices are usually presented as whole yuan, often ending in 8.
    raw = int(round(value / 10.0) * 10)
    if raw % 10 == 0:
        raw = raw - 2 if raw > 20 else raw
    return max(1, raw)


def weekday_factor(d: date) -> float:
    if d.weekday() >= 5:
        return 1.12
    if d.weekday() == 4:
        return 1.05
    return 1.0


def compute_room_pricing(
    room: RoomType,
    date_from: date,
    date_to: date,
    metrics: list[DailyMetric],
    competitors: list[CompetitorSnapshot],
) -> dict[str, Any]:
    history = [m for m in metrics if m.room_type_id == room.id and not m.is_future]
    future = [m for m in metrics if m.room_type_id == room.id and m.is_future]
    if not future:
        # Fall back to any metrics in the target window.
        future = [m for m in metrics if m.room_type_id == room.id and date_from <= m.date <= date_to]

    healthy = [m for m in history if m.date <= date_from - timedelta(days=8)] or history[:7]
    recent = history[-7:] if history else []

    baseline_adr = median([m.adr or m.list_price for m in healthy if (m.adr or m.list_price) > 0] or [room.base_price])
    recent_adr = median([m.adr or m.list_price for m in recent if (m.adr or m.list_price) > 0] or [baseline_adr])
    current_list = median([m.list_price for m in (future or recent) if m.list_price > 0] or [room.base_price])

    recent_rank = [m.search_rank for m in recent if m.search_rank]
    healthy_rank = [m.search_rank for m in healthy if m.search_rank]
    rank_from = int(median(healthy_rank)) if healthy_rank else 10
    rank_to = int(median(recent_rank[-3:])) if recent_rank else rank_from
    rank_dropped = rank_to - rank_from >= 5

    recent_occ = median([m.occupancy for m in recent]) if recent else 0.7
    healthy_occ = median([m.occupancy for m in healthy]) if healthy else recent_occ

    comp_recent = [
        c
        for c in competitors
        if c.room_type_id == room.id and c.date >= (recent[-1].date - timedelta(days=6) if recent else date_from)
    ]
    comp_healthy = [
        c
        for c in competitors
        if c.room_type_id == room.id and healthy and c.date <= healthy[-1].date
    ]
    comp_prices_recent = [c.price for c in comp_recent] or [current_list]
    comp_prices_healthy = [c.price for c in comp_healthy] or comp_prices_recent
    comp_median = median(comp_prices_recent)
    comp_low = min(comp_prices_recent)
    comp_high = max(comp_prices_recent)
    healthy_comp_median = median(comp_prices_healthy)
    cut_pct = 0.0
    if healthy_comp_median:
        cut_pct = max(0.0, (healthy_comp_median - comp_median) / healthy_comp_median * 100)

    # Occupancy high → stay closer to our baseline; rank poor + occ soft → lean to competitor mid.
    if recent_occ >= 0.85:
        blend_comp = 0.25
    elif rank_dropped and recent_occ < healthy_occ - 0.04:
        blend_comp = 0.7
    elif rank_dropped:
        blend_comp = 0.55
    else:
        blend_comp = 0.4

    # Stay slightly above the cheapest competitor — do not race to the bottom.
    target_unclamped = (1 - blend_comp) * baseline_adr + blend_comp * (comp_median * 1.02)
    recommended = _clamp(target_unclamped, room.min_price, room.max_price)
    # Keep a modest corridor around the recommendation, still inside constraints and competitor band.
    band_lo = _clamp(min(recommended * 0.93, comp_low * 0.99), room.min_price, recommended)
    band_hi = _clamp(max(recommended * 1.07, min(current_list, comp_high * 1.02)), recommended, room.max_price)
    recommended = _round_price(recommended)
    band_lo = _round_price(band_lo)
    band_hi = _round_price(band_hi)
    if band_lo > recommended:
        band_lo = recommended
    if band_hi < recommended:
        band_hi = recommended

    price_change_pct = (recommended - current_list) / current_list if current_list else 0.0
    # Simple constant-elasticity demand: weaker rank → slightly higher elasticity.
    elasticity = 1.25 if rank_dropped else 0.85
    occ_delta = -elasticity * price_change_pct  # price down => occupancy up
    # Express as percentage points of occupancy (0-100).
    occ_delta_pp = occ_delta * 100 * max(recent_occ, 0.3)

    days = []
    cursor = date_from
    while cursor <= date_to:
        factor = weekday_factor(cursor)
        day_price = _round_price(_clamp(recommended * factor / 1.04 if cursor.weekday() >= 5 else recommended, band_lo, band_hi if cursor.weekday() < 5 else room.max_price))
        days.append(
            {
                "date": cursor.isoformat(),
                "recommended": day_price,
                "current_list": _round_price(current_list * factor / (1.0 if cursor.weekday() < 5 else 1.0) * (1.12 if cursor.weekday() >= 5 else 1.0)),
            }
        )
        cursor += timedelta(days=1)

    window_days = max(1, (date_to - date_from).days + 1)
    avg_sold = median([m.sold_rooms for m in recent if not m.is_future] or [room.inventory * recent_occ])
    new_sold = max(0.0, avg_sold * (1 + occ_delta))
    revenue_now = current_list * avg_sold * window_days
    revenue_new = recommended * new_sold * window_days
    revenue_delta = revenue_new - revenue_now

    # Confidence: more consistent competitor cuts + complete history → higher.
    confidence = 0.7
    if len(history) >= 10:
        confidence += 0.06
    if cut_pct >= 8:
        confidence += 0.05
    if rank_dropped:
        confidence += 0.04
    if abs(price_change_pct) > 0.2:
        confidence -= 0.08
    confidence = round(_clamp(confidence, 0.55, 0.9), 2)

    return {
        "room_type_id": room.id,
        "room_name": room.name,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "current_price": round(current_list, 2),
        "recommended_price": recommended,
        "min_price": band_lo,
        "max_price": band_hi,
        "confidence": confidence,
        "constraints": {
            "floor": room.min_price,
            "ceiling": room.max_price,
            "competitor_median": round(comp_median, 2),
            "competitor_low": round(comp_low, 2),
            "competitor_high": round(comp_high, 2),
            "competitor_cut_pct": round(cut_pct, 1),
        },
        "signals": {
            "rank_from": rank_from,
            "rank_to": rank_to,
            "rank_dropped": rank_dropped,
            "recent_occupancy": round(recent_occ, 3),
            "healthy_occupancy": round(healthy_occ, 3),
            "baseline_adr": round(baseline_adr, 2),
        },
        "expected_impact": {
            "price_change_pct": round(price_change_pct * 100, 1),
            "occupancy_delta_pct": round(occ_delta_pp, 1),
            "revenue_delta": int(revenue_delta),
            "window_days": window_days,
            "note": "启发式弹性测算，仅供人审参考，不是承诺。",
        },
        "daily_breakdown": days,
    }

from app.services.campaigns import compute_campaign_advice  # noqa: E402,F401
