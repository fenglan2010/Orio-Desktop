from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from app.services.kpis import flash_windows, in_range, pack_metrics, period_delta, week_start


def _row(**kw):
    defaults = dict(
        date=date(2026, 11, 14),
        is_future=False,
        revenue=0,
        sold_rooms=0,
        available_rooms=0,
        bookings=0,
        clicks=0,
        impressions=0,
        search_rank=0,
        occupancy=0,
        adr=0,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_week_starts_monday():
    assert week_start(date(2026, 11, 14)) == date(2026, 11, 9)  # Saturday
    assert week_start(date(2026, 11, 9)) == date(2026, 11, 9)


def test_pack_is_inventory_weighted_not_simple_average():
    # Two room types on the same day: 10 sold of 10 at ¥1000, and 1 sold of 10 at ¥200.
    # Unweighted ADR would be 600; weighted ADR is 10200/11.
    rows = [
        _row(revenue=10000, sold_rooms=10, available_rooms=10, occupancy=1.0, adr=1000, bookings=10, clicks=100),
        _row(revenue=200, sold_rooms=1, available_rooms=10, occupancy=0.1, adr=200, bookings=1, clicks=50),
    ]
    p = pack_metrics(rows)
    assert p["revenue"] == 10200
    assert p["sold_rooms"] == 11
    assert p["available_rooms"] == 20
    assert p["occupancy"] == round(11 / 20, 4)
    assert p["adr"] == round(10200 / 11, 1)
    assert p["revpar"] == round(10200 / 20, 1)
    assert abs(p["revpar"] - p["adr"] * p["occupancy"]) < 0.15
    assert p["orders"] == 11
    assert p["pageviews"] == 150
    assert p["conversion_rate"] == round(11 / 150, 4)


def test_period_delta_uses_percent_and_points():
    now = pack_metrics([_row(revenue=120, sold_rooms=6, available_rooms=10, bookings=6, clicks=100)])
    prev = pack_metrics([_row(revenue=100, sold_rooms=5, available_rooms=10, bookings=5, clicks=100)])
    d = period_delta(now, prev)
    assert d["revenue"] == 20.0
    assert d["occupancy_pp"] == 10.0  # 60% vs 50%
    assert d["conversion_pp"] == 1.0  # 6% vs 5%


def test_in_range_skips_future_by_default():
    rows = [
        _row(date=date(2026, 11, 14), is_future=False, revenue=10, sold_rooms=1, available_rooms=2),
        _row(date=date(2026, 11, 15), is_future=True, revenue=0, sold_rooms=1, available_rooms=2),
    ]
    assert len(in_range(rows, date(2026, 11, 14), date(2026, 11, 15))) == 1
    assert len(in_range(rows, date(2026, 11, 14), date(2026, 11, 15), include_future=True)) == 2


def test_flash_windows_align_to_last_week():
    today = date(2026, 11, 14)
    w = flash_windows(today)
    assert w["week_start"] == date(2026, 11, 9)
    assert w["prev_week_start"] == date(2026, 11, 2)
    assert w["prev_week_end"] == date(2026, 11, 7)
    assert w["same_weekday_last_week"] == today - timedelta(days=7)


def test_summary_flash_kpis(client, auth_header):
    data = client.get("/api/dashboard/summary", headers=auth_header).json()
    k = data["kpis"]
    assert data["flash_window"]
    assert k["week_revenue"] > 0
    assert k["today_revenue"] > 0
    assert k["today_orders"] >= 1
    assert k["today_pageviews"] >= 1
    assert k["revpar"] > 0
    assert 0 < k["today_occupancy"] <= 1
    assert k["today_adr"] > 0
    assert 0 <= k["today_conversion_rate"] <= 1
    # RevPAR = 今日营收 / 今日可售房 (recompute from packed sold/available of last7 is not today)
    # Just check keys and that WTD revenue is at least today's revenue.
    assert k["week_revenue"] >= k["today_revenue"]
    for key in (
        "week_revenue",
        "revpar",
        "today_adr",
        "today_occupancy",
        "today_orders",
        "today_revenue",
        "today_pageviews",
        "today_conversion_rate",
    ):
        assert key in k["delta"]
