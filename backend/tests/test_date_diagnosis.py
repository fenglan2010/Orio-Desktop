from datetime import date, timedelta

from app.services.diagnosis import DiagnosisError, diagnose_for_date


def test_date_diagnosis_rejects_future_and_includes_selected_day(client, auth_header):
    today = date.today().isoformat()
    future = (date.today() + timedelta(days=3)).isoformat()
    bad = client.get(f"/api/diagnosis?date={future}", headers=auth_header)
    assert bad.status_code == 400
    assert "在手订单" in bad.json()["detail"]

    ok = client.get(f"/api/diagnosis?date={today}", headers=auth_header)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["as_of"] == today
    assert body["scope"] == "date"
    assert body["classification"] in {"structural", "short_term"}
    assert body["classification_label"]
    assert body["snapshot"]["date"] == today
    assert body["snapshot"]["in_progress"] is True
    assert body["snapshot"]["has_realized_metrics"] is True
    assert body["snapshot"]["metrics"]["available_rooms"] >= body["snapshot"]["metrics"]["sold_rooms"]
    assert "同比预售" in body["window_note"]
    assert "narrative" in body
    # Selected day is included in the signal window.
    assert body["signals"]["as_of"] == today

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    past = client.get(f"/api/diagnosis?date={yesterday}", headers=auth_header)
    assert past.status_code == 200, past.text
    pb = past.json()
    assert pb["as_of"] == yesterday
    assert pb["snapshot"]["in_progress"] is False
    assert pb["snapshot"]["reference_label"] == "上周同日"
    assert pb["snapshot"]["reference_date"] == (date.today() - timedelta(days=8)).isoformat()
    assert "primary_causes" in pb


def test_diagnose_for_date_unit_guard():
    from types import SimpleNamespace

    today = date(2026, 11, 14)
    future = today + timedelta(days=2)
    rooms = [SimpleNamespace(id=1, name="豪华大床房")]
    metrics = [
        SimpleNamespace(
            date=future,
            room_type_id=1,
            sold_rooms=2,
            available_rooms=10,
            revenue=1000,
            bookings=1,
            clicks=20,
            impressions=40,
            occupancy=0.2,
            adr=500,
            list_price=600,
            search_rank=12,
            is_future=True,
            conversion_rate=0.05,
        )
    ]
    try:
        diagnose_for_date(future, rooms, metrics, [], [], [], today)
        assert False, "expected DiagnosisError"
    except DiagnosisError as exc:
        assert "在手订单" in str(exc)
