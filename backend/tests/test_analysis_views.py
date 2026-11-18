from datetime import date, timedelta
from types import SimpleNamespace

from app.services.analysis_views import build_stock_and_pace, date_pressure


TODAY = date(2026, 11, 14)


def _m(day, room_id, sold, available, bookings=0, is_future=None):
    return SimpleNamespace(
        date=day,
        room_type_id=room_id,
        sold_rooms=sold,
        available_rooms=available,
        bookings=bookings,
        is_future=TODAY < day if is_future is None else is_future,
    )


def test_date_pressure_rules():
    assert date_pressure(1, 0.30) == "high"
    assert date_pressure(2, 0.92) == "tight"
    assert date_pressure(5, 0.48) == "medium"
    assert date_pressure(10, 0.70) == "low"


def test_stock_remaining_and_room_gap():
    rooms = [
        SimpleNamespace(id=1, name="豪华大床房"),
        SimpleNamespace(id=3, name="家庭房"),
    ]
    metrics = []
    for i in range(0, 10):
        d = TODAY + timedelta(days=i)
        # Deluxe holds fairly well; family stays soft on the books.
        metrics.append(_m(d, 1, sold=16, available=20, bookings=2 if i == 0 else 0))
        metrics.append(_m(d, 3, sold=2, available=8, bookings=1 if i == 0 else 0))
    # History for pace
    for i in range(1, 15):
        d = TODAY - timedelta(days=i)
        metrics.append(_m(d, 1, sold=18, available=20, bookings=4 if i <= 7 else 6, is_future=False))
        metrics.append(_m(d, 3, sold=5, available=8, bookings=1 if i <= 7 else 2, is_future=False))

    views = build_stock_and_pace(metrics, rooms, TODAY)
    stock = views["stock"]
    assert stock["remaining_nights"] == (4 + 6) * 7  # 7 days incl today
    assert stock["capacity_nights"] == (20 + 8) * 7
    assert stock["otb_occupancy"] == round((18 * 7) / (28 * 7), 4)
    for row in stock["by_date"]:
        assert row["remaining"] == row["available"] - row["sold"]
    by_name = {r["name"]: r for r in stock["by_room"]}
    assert by_name["家庭房"]["occupancy"] < by_name["豪华大床房"]["occupancy"]
    assert any("家庭房" in f and "豪华大床房" in f for f in stock["findings"])

    pace = views["pace"]
    assert pace["bookings_last7"] == (4 + 1) * 7
    assert pace["bookings_prev7"] == (6 + 2) * 7
    assert "同一提前天数" in pace["reference_note"]
    assert pace["today_bookings"] == 3
    ref = pace["otb_vs_reference"]
    assert ref and ref[0]["reference_occupancy"] is not None
    assert ref[0]["gap_pp"] == round((ref[0]["otb_occupancy"] - ref[0]["reference_occupancy"]) * 100, 1)


def test_timeseries_exposes_stock_and_pace(client, auth_header):
    res = client.get("/api/dashboard/timeseries", headers=auth_header)
    assert res.status_code == 200
    body = res.json()
    point = next(p for p in body["series"] if p["date"] >= "2026-11-14")
    assert point["remaining"] == point["available"] - point["sold"]
    assert "bookings" in point
    stock = body["stock"]
    assert stock["findings"]
    assert stock["by_room"] and stock["by_date"]
    assert stock["remaining_nights"] == sum(r["remaining"] for r in stock["by_date"] if r["days_out"] <= 6)
    pace = body["pace"]
    assert "同一提前天数" in pace["reference_note"]
    assert pace["findings"]
    assert "同比预售曲线" in pace["reference_note"]


from app.services.analysis_views import build_history_and_conversion


def test_history_and_conversion_volume_weighted():
    rooms = [
        SimpleNamespace(id=1, name="豪华大床房"),
        SimpleNamespace(id=3, name="家庭房"),
    ]
    metrics = []
    # Prev 7 (today-13 .. today-7): deluxe converts well, family poorly.
    # Last 7 (today-6 .. today): both softer in revenue, conversion improves on family.
    for i in range(0, 14):
        d = TODAY - timedelta(days=i)
        if i <= 6:
            metrics.append(_m(d, 1, sold=12, available=20, bookings=4, is_future=False))
            metrics.append(_m(d, 3, sold=3, available=8, bookings=2, is_future=False))
        else:
            metrics.append(_m(d, 1, sold=16, available=20, bookings=6, is_future=False))
            metrics.append(_m(d, 3, sold=5, available=8, bookings=1, is_future=False))

    # Attach clicks so conversion is bookings/clicks, not a mean of room rates.
    for m in metrics:
        if m.room_type_id == 1:
            m.clicks = 80
            m.revenue = m.sold_rooms * 500
        else:
            m.clicks = 40
            m.revenue = m.sold_rooms * 400
        m.impressions = m.clicks * 2
        m.adr = m.revenue / m.sold_rooms
        m.conversion_rate = 0.99  # sentinel: must not be used as the period rate

    views = build_history_and_conversion(metrics, rooms, TODAY)
    hist = views["history"]
    last_bookings = 7 * (4 + 2)
    last_clicks = 7 * (80 + 40)
    prev_bookings = 7 * (6 + 1)
    prev_clicks = 7 * (80 + 40)
    assert hist["last7"]["orders"] == last_bookings
    assert hist["last7"]["pageviews"] == last_clicks
    assert hist["last7"]["conversion_rate"] == round(last_bookings / last_clicks, 4)
    assert hist["last7"]["conversion_rate"] != 0.99
    assert hist["prev7"]["orders"] == prev_bookings
    assert "同比" in hist["reference_note"]
    assert hist["reference_date"] == (TODAY - timedelta(days=7)).isoformat()

    conv = views["conversion"]
    assert conv["channel"] == "携程"
    assert "多渠道" in conv["reference_note"]
    assert conv["last7"]["conversion_rate"] == round(last_bookings / last_clicks, 4)
    assert conv["prev7"]["conversion_rate"] == round(prev_bookings / prev_clicks, 4)
    assert len(conv["series"]) == 14
    assert conv["series"][-1]["date"] == TODAY.isoformat()
    by_name = {r["name"]: r for r in conv["by_room"]}
    # Family last7: 2*7 / 40*7 = 0.05; deluxe 4*7/80*7 = 0.05 — equal.
    # Make assertion on weighted hotel rate only; rooms both 0.05.
    assert by_name["豪华大床房"]["conversion_rate"] == round((4 * 7) / (80 * 7), 4)


def test_timeseries_exposes_history_and_conversion(client, auth_header):
    res = client.get("/api/dashboard/timeseries", headers=auth_header)
    assert res.status_code == 200
    body = res.json()
    point = next(p for p in body["series"] if p["date"] <= "2026-11-14" and not p["is_future"])
    assert "clicks" in point
    if point["clicks"]:
        assert point["conversion_rate"] == round(point["bookings"] / point["clicks"], 4)
    hist = body["history"]
    assert hist["findings"] and "同比" in hist["reference_note"]
    last, prev = hist["last7"], hist["prev7"]
    assert last["conversion_rate"] == (round(last["orders"] / last["pageviews"], 4) if last["pageviews"] else 0)
    conv = body["conversion"]
    assert conv["channel"] == "携程"
    assert "多渠道" in conv["reference_note"]
    assert conv["series"] and conv["by_room"]
