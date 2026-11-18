"""Phase IV increment 3: post-decision impact attribution."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import DailyMetric, Hotel, MemoryRecord, RoomType, WorkflowRun
from app.services.impact import (
    OUTCOME_MEASURED,
    OUTCOME_NOT_MEASURED,
    OUTCOME_PARTIAL,
    measure_hotel_episodes,
)
from app.services.memory import MEMORY_KIND, persist_decision_episode


def _login(client: TestClient, username: str, password: str = "admin123"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _headers(client: TestClient, username: str = "admin", password: str = "admin123") -> dict:
    resp = _login(client, username, password)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _hotels():
    db = SessionLocal()
    try:
        hangzhou = db.query(Hotel).filter(Hotel.tenant_key == "demo").one()
        chengdu = db.query(Hotel).filter(Hotel.tenant_key == "demo_chengdu").one()
        return hangzhou, chengdu
    finally:
        db.close()


def _room(hotel_id: int) -> RoomType:
    db = SessionLocal()
    try:
        room = (
            db.query(RoomType)
            .filter(RoomType.hotel_id == hotel_id)
            .order_by(RoomType.id.asc())
            .first()
        )
        assert room is not None
        db.expunge(room)
        return room
    finally:
        db.close()


def _today() -> date:
    return date.today()


FORBIDDEN_TOKENS = (
    "同比",
    "准确率",
    "这次调价带来了",
    "yoy",
    "year_over_year",
    "year_ago",
    "accuracy",
    "accuracy_pct",
    "model_accuracy",
    "actual_revenue",
    "revenue_lift",
    "measured_impact",
    "caused_revenue",
    "causal_revenue",
)


def _assert_no_invented_fields(blob) -> None:
    text = str(blob).lower()
    for token in FORBIDDEN_TOKENS:
        assert token.lower() not in text, f"invented or causal field {token!r} in {blob}"


def _persist_episode(hotel_id: int, **overrides) -> MemoryRecord:
    db = SessionLocal()
    try:
        today = _today()
        room = (
            db.query(RoomType)
            .filter(RoomType.hotel_id == hotel_id)
            .order_by(RoomType.id.asc())
            .first()
        )
        assert room is not None
        status = overrides.pop("status", "executed")
        kind = overrides.pop("kind", "pricing")
        date_from = overrides.pop("date_from", today)
        date_to = overrides.pop("date_to", today + timedelta(days=3))
        room_type_id = overrides.pop("room_type_id", room.id)
        expected_impact = overrides.pop("expected_impact", None)
        run = WorkflowRun(
            hotel_id=hotel_id,
            kind="pricing_strategy" if kind == "pricing" else kind,
            status=status,
            current_node="memory_node",
            payload={},
            created_by="impact-tester",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        state = {
            "hotel_id": hotel_id,
            "run_id": run.id,
            "kind": kind,
            "status": status,
            "approval_status": status,
            "risk_level": "medium",
            "actor": "impact-tester",
            "as_of": today.isoformat(),
            "date_from": date_from.isoformat() if isinstance(date_from, date) else date_from,
            "date_to": date_to.isoformat() if isinstance(date_to, date) else date_to,
            "room_type_id": room_type_id,
            "strategy": {
                "kind": kind,
                "room_type_id": room_type_id,
                "room_name": room.name if room_type_id == room.id else "",
                "current_price": 512,
                "recommended_price": 488,
            },
            "execution_result": {"accepted": True, "mode": "sandbox_official", "outcome": "accepted"},
            "checkpoint_id": f"cp-impact-{hotel_id}-{run.id}",
        }
        if expected_impact is not None:
            state["strategy"]["expected_impact"] = expected_impact
        if kind == "campaign":
            state["strategy"]["campaign_advice"] = {"campaign_name": "演示活动"}
        state.update(overrides)
        row = persist_decision_episode(db, state)
        assert row is not None
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


def _reload(memory_id: int) -> MemoryRecord:
    db = SessionLocal()
    try:
        row = db.get(MemoryRecord, memory_id)
        assert row is not None
        db.expunge(row)
        return row
    finally:
        db.close()


def _bump_metrics(
    hotel_id: int,
    start: date,
    end: date,
    *,
    room_type_id: int | None = None,
    sold_delta: int = 2,
    revenue_delta: int = 400,
    mark_realized_before: date | None = None,
) -> None:
    db = SessionLocal()
    try:
        q = db.query(DailyMetric).filter(
            DailyMetric.hotel_id == hotel_id,
            DailyMetric.date >= start,
            DailyMetric.date <= end,
        )
        if room_type_id is not None:
            q = q.filter(DailyMetric.room_type_id == room_type_id)
        rows = list(q.all())
        assert rows, f"no metric rows to adjust for hotel {hotel_id} {start}..{end}"
        for row in rows:
            if mark_realized_before is not None and row.date < mark_realized_before:
                row.is_future = False
            row.sold_rooms = int(row.sold_rooms or 0) + sold_delta
            if row.available_rooms:
                row.sold_rooms = min(row.sold_rooms, row.available_rooms)
                row.occupancy = row.sold_rooms / row.available_rooms
            if not row.is_future:
                row.revenue = float(row.revenue or 0) + revenue_delta
                row.bookings = int(row.bookings or 0) + 1
                if row.sold_rooms:
                    row.adr = row.revenue / row.sold_rooms
            else:
                row.bookings = int(row.bookings or 0) + 1
        db.commit()
    finally:
        db.close()


def _measure(hotel_id: int, today: date | None = None) -> dict:
    db = SessionLocal()
    try:
        result = measure_hotel_episodes(db, hotel_id, today=today or _today())
        return result
    finally:
        db.close()


def test_fresh_executed_episode_snapshots_baseline_and_stays_unmeasured(client):
    hangzhou, _ = _hotels()
    room = _room(hangzhou.id)
    today = _today()
    start, end = today - timedelta(days=4), today - timedelta(days=2)
    row = _persist_episode(hangzhou.id, date_from=start, date_to=end, room_type_id=room.id)
    payload = row.payload or {}
    assert payload.get("outcome") == OUTCOME_NOT_MEASURED
    assert payload.get("impact") is None
    baseline = payload.get("baseline") or {}
    assert baseline.get("metrics_available") is True
    assert baseline.get("captured_at") == today.isoformat()
    assert baseline.get("date_from") == start.isoformat()
    assert baseline.get("date_to") == end.isoformat()
    assert baseline.get("room_type_id") == room.id
    assert baseline.get("realized")
    assert baseline["realized"]["kpis"]["sold_rooms"] >= 0
    assert payload.get("impact") is None
    _assert_no_invented_fields(payload)
    # Even though stay dates are already realized, measurement waits for a later pass.
    listed = client.get("/api/memories", headers=_headers(client))
    assert listed.status_code == 200
    item = next(m for m in listed.json()["memories"] if m["id"] == row.id)
    assert item["outcome"] == OUTCOME_NOT_MEASURED
    assert item.get("impact") in (None, {})
    assert "尚未复盘" in item["summary"]


def test_realized_window_is_measured_from_real_metric_rows(client):
    hangzhou, _ = _hotels()
    room = _room(hangzhou.id)
    today = _today()
    start, end = today - timedelta(days=5), today - timedelta(days=3)
    row = _persist_episode(
        hangzhou.id,
        date_from=start,
        date_to=end,
        room_type_id=room.id,
        expected_impact={
            "occupancy_delta_pct": 1.5,
            "revenue_delta": 600,
            "note": "启发式弹性测算，仅供人审参考，不是承诺。",
        },
    )
    baseline_revenue = row.payload["baseline"]["realized"]["kpis"]["revenue"]
    _bump_metrics(hangzhou.id, start, end, room_type_id=room.id, sold_delta=2, revenue_delta=500)
    result = _measure(hangzhou.id, today=today)
    assert result["updated"] >= 1
    row = _reload(row.id)
    payload = row.payload or {}
    assert payload["outcome"] == OUTCOME_MEASURED
    impact = payload["impact"]
    assert impact is not None
    assert impact["realized"]
    assert impact["otb_pickup"] is None
    assert impact["realized"]["delta"]["revenue_abs"] == 500 * 3  # 3 stay dates, +500 each
    assert impact["realized"]["current_kpis"]["revenue"] == baseline_revenue + 1500
    # Baseline must remain the decision-time snapshot.
    assert payload["baseline"]["realized"]["kpis"]["revenue"] == baseline_revenue
    scenario = impact.get("scenario_comparison") or {}
    assert scenario.get("recorded_scenario", {}).get("revenue_delta") == 600
    assert scenario.get("observed", {}).get("revenue_delta") == 1500
    assert "准确" not in str(scenario)
    _assert_no_invented_fields(payload)

    headers = _headers(client)
    listed = client.get("/api/memories", headers=headers)
    item = next(m for m in listed.json()["memories"] if m["id"] == row.id)
    assert item["outcome"] == OUTCOME_MEASURED
    assert item["impact"]["realized"]["delta"]["revenue_abs"] == 1500
    one = client.get(f"/api/memories/{row.id}", headers=headers)
    assert one.status_code == 200
    assert one.json()["impact"]["realized"]["delta"]["sold_rooms_abs"] == 2 * 3


def test_future_otb_pickup_is_partially_measured(client):
    hangzhou, _ = _hotels()
    room = _room(hangzhou.id)
    today = _today()
    start, end = today + timedelta(days=3), today + timedelta(days=5)
    row = _persist_episode(hangzhou.id, date_from=start, date_to=end, room_type_id=room.id)
    assert row.payload["outcome"] == OUTCOME_NOT_MEASURED
    baseline_sold = row.payload["baseline"]["otb"]["kpis"]["sold_rooms"]
    _bump_metrics(hangzhou.id, start, end, room_type_id=room.id, sold_delta=3, revenue_delta=0)
    _measure(hangzhou.id, today=today)
    row = _reload(row.id)
    payload = row.payload or {}
    assert payload["outcome"] == OUTCOME_PARTIAL
    impact = payload["impact"]
    assert impact["realized"] is None
    assert impact["otb_pickup"]
    assert "在手订单变化" in impact["otb_pickup"]["label"]
    assert "预测" in impact["otb_pickup"]["label"]
    assert impact["otb_pickup"]["delta"]["sold_rooms_abs"] == 3 * 3
    assert payload["baseline"]["otb"]["kpis"]["sold_rooms"] == baseline_sold
    _assert_no_invented_fields(payload)


def test_rejected_rolled_back_and_failed_are_measured(client):
    hangzhou, _ = _hotels()
    room = _room(hangzhou.id)
    today = _today()
    start, end = today - timedelta(days=6), today - timedelta(days=4)
    expected_titles = {
        "rejected": "否决后实际发生了什么",
        "execution_failed": "执行失败后实际发生了什么",
        "rolled_back": "回滚后实际发生了什么",
    }
    expected_kinds = {
        "rejected": "after_no_change",
        "execution_failed": "after_failed_write",
        "rolled_back": "after_rollback",
    }
    rejected = _persist_episode(
        hangzhou.id, status="rejected", date_from=start, date_to=end, room_type_id=room.id
    )
    failed = _persist_episode(
        hangzhou.id, status="execution_failed", date_from=start, date_to=end, room_type_id=room.id
    )
    rolled = _persist_episode(
        hangzhou.id, status="rolled_back", date_from=start, date_to=end, room_type_id=room.id
    )
    for row in (rejected, failed, rolled):
        payload = row.payload or {}
        assert payload.get("outcome") == OUTCOME_NOT_MEASURED
        assert payload.get("impact") is None
        assert (payload.get("baseline") or {}).get("metrics_available") is True
    _bump_metrics(hangzhou.id, start, end, room_type_id=room.id, revenue_delta=180)
    result = _measure(hangzhou.id, today=today)
    assert result["updated"] >= 3
    headers = _headers(client)
    for memory_id, status in (
        (rejected.id, "rejected"),
        (failed.id, "execution_failed"),
        (rolled.id, "rolled_back"),
    ):
        row = _reload(memory_id)
        assert row.approval_status == status
        payload = row.payload or {}
        assert payload.get("outcome") == OUTCOME_MEASURED
        impact = payload.get("impact") or {}
        assert impact["terminal_status"] == status
        assert impact["observation_kind"] == expected_kinds[status]
        assert impact["title"] == expected_titles[status]
        assert impact["realized"]["delta"]["revenue_abs"] == 180 * 3
        _assert_no_invented_fields(payload)
        listed = client.get("/api/memories", headers=headers)
        item = next(m for m in listed.json()["memories"] if m["id"] == memory_id)
        assert item["outcome"] == OUTCOME_MEASURED
        assert item["impact"]["title"] == expected_titles[status]
        assert item["impact"]["terminal_status"] == status


def test_hotel_isolation_does_not_write_impact_across_hotels(client):
    hangzhou, chengdu = _hotels()
    today = _today()
    start, end = today - timedelta(days=7), today - timedelta(days=5)
    hz_room = _room(hangzhou.id)
    cd_room = _room(chengdu.id)
    hz = _persist_episode(hangzhou.id, date_from=start, date_to=end, room_type_id=hz_room.id)
    cd = _persist_episode(chengdu.id, date_from=start, date_to=end, room_type_id=cd_room.id)
    _bump_metrics(hangzhou.id, start, end, room_type_id=hz_room.id, revenue_delta=300)
    _bump_metrics(chengdu.id, start, end, room_type_id=cd_room.id, revenue_delta=900)
    _measure(hangzhou.id, today=today)
    hz = _reload(hz.id)
    cd = _reload(cd.id)
    assert hz.payload["outcome"] == OUTCOME_MEASURED
    assert hz.hotel_id == hangzhou.id
    assert cd.payload["outcome"] == OUTCOME_NOT_MEASURED
    assert cd.payload.get("impact") is None
    assert cd.hotel_id == chengdu.id

    # Chengdu members cannot read Hangzhou's measured episode.
    other = client.get(f"/api/memories/{hz.id}", headers=_headers(client, "admin_b"))
    assert other.status_code == 404
    own_list = client.get("/api/memories", headers=_headers(client, "admin_b"))
    ids = {item["id"] for item in own_list.json()["memories"]}
    assert cd.id in ids
    assert hz.id not in ids


def test_mixed_window_is_partially_measured(client):
    hangzhou, _ = _hotels()
    room = _room(hangzhou.id)
    today = _today()
    start, end = today - timedelta(days=2), today + timedelta(days=2)
    row = _persist_episode(hangzhou.id, date_from=start, date_to=end, room_type_id=room.id)
    past_end = today - timedelta(days=1)
    future_start = today + timedelta(days=1)
    _bump_metrics(hangzhou.id, start, past_end, room_type_id=room.id, sold_delta=2, revenue_delta=200)
    _bump_metrics(hangzhou.id, future_start, end, room_type_id=room.id, sold_delta=4, revenue_delta=0)
    _measure(hangzhou.id, today=today)
    row = _reload(row.id)
    payload = row.payload or {}
    assert payload["outcome"] == OUTCOME_PARTIAL
    impact = payload["impact"]
    assert impact["realized"]
    assert impact["otb_pickup"]
    assert impact["realized"]["delta"]["revenue_abs"] == 200 * 2
    assert impact["otb_pickup"]["delta"]["sold_rooms_abs"] == 4 * 2
    # Today stays out of the realized slice.
    assert today.isoformat() not in (impact["realized"]["stay_dates"] or [])
    _assert_no_invented_fields(payload)


def test_missing_metrics_stay_unmeasured_without_invented_revenue(client):
    hangzhou, _ = _hotels()
    room = _room(hangzhou.id)
    today = _today()
    start, end = today + timedelta(days=40), today + timedelta(days=42)
    row = _persist_episode(hangzhou.id, date_from=start, date_to=end, room_type_id=room.id)
    payload = row.payload or {}
    assert payload["outcome"] == OUTCOME_NOT_MEASURED
    assert payload["impact"] is None
    baseline = payload["baseline"]
    assert baseline["metrics_available"] is False
    assert "尚无可用客房指标" in (baseline.get("missing_reason") or "")
    assert all(item.get("kpis") is None for item in baseline.get("daily") or [])
    _measure(hangzhou.id, today=today)
    row = _reload(row.id)
    assert row.payload["outcome"] == OUTCOME_NOT_MEASURED
    assert row.payload["impact"] is None
    _assert_no_invented_fields(row.payload)


def test_remeasure_updates_same_row_and_keeps_baseline(client):
    hangzhou, _ = _hotels()
    room = _room(hangzhou.id)
    today = _today()
    start, end = today - timedelta(days=8), today - timedelta(days=6)
    row = _persist_episode(hangzhou.id, date_from=start, date_to=end, room_type_id=room.id)
    original_key = row.episode_key
    baseline_revenue = row.payload["baseline"]["realized"]["kpis"]["revenue"]
    _bump_metrics(hangzhou.id, start, end, room_type_id=room.id, revenue_delta=100, sold_delta=1)
    _measure(hangzhou.id, today=today)
    first = _reload(row.id)
    assert first.payload["impact"]["realized"]["delta"]["revenue_abs"] == 100 * 3
    _bump_metrics(hangzhou.id, start, end, room_type_id=room.id, revenue_delta=50, sold_delta=1)
    _measure(hangzhou.id, today=today)
    second = _reload(row.id)
    assert second.id == first.id
    assert second.episode_key == original_key
    assert second.payload["baseline"]["realized"]["kpis"]["revenue"] == baseline_revenue
    assert second.payload["impact"]["realized"]["delta"]["revenue_abs"] == 150 * 3
    db = SessionLocal()
    try:
        count = (
            db.query(MemoryRecord)
            .filter(MemoryRecord.hotel_id == hangzhou.id, MemoryRecord.episode_key == original_key)
            .count()
        )
        assert count == 1
    finally:
        db.close()


def test_industry_patterns_remain_anonymized_and_unmeasured(client):
    hangzhou, chengdu = _hotels()
    today = _today()
    start, end = today - timedelta(days=9), today - timedelta(days=8)
    hz_room = _room(hangzhou.id)
    cd_room = _room(chengdu.id)
    signals = {"flags": ["rank_drop", "competitor_cut", "conversion_drop"]}
    hz = _persist_episode(
        hangzhou.id, date_from=start, date_to=end, room_type_id=hz_room.id, signals=signals
    )
    cd = _persist_episode(
        chengdu.id, date_from=start, date_to=end, room_type_id=cd_room.id, signals=signals
    )
    _bump_metrics(hangzhou.id, start, end, room_type_id=hz_room.id, revenue_delta=250)
    _measure(hangzhou.id, today=today)
    assert _reload(hz.id).payload["outcome"] == OUTCOME_MEASURED
    listed = client.get("/api/memories/patterns", headers=_headers(client))
    assert listed.status_code == 200
    body = listed.json()
    assert body["patterns"]
    for item in body["patterns"]:
        assert item.get("outcome") == OUTCOME_NOT_MEASURED
        assert item.get("impact") in (None, {})
        assert "hotel_id" not in item
        assert hangzhou.name not in str(item)
        assert chengdu.name not in str(item)
        _assert_no_invented_fields(item)


def test_ingest_job_triggers_measurement(client):
    hangzhou, _ = _hotels()
    room = _room(hangzhou.id)
    today = _today()
    start, end = today - timedelta(days=10), today - timedelta(days=9)
    row = _persist_episode(hangzhou.id, date_from=start, date_to=end, room_type_id=room.id)
    _bump_metrics(hangzhou.id, start, end, room_type_id=room.id, revenue_delta=120)
    db = SessionLocal()
    try:
        from app.jobs.tasks import ingest_hotel

        result = ingest_hotel(db, hangzhou.id, actor="system:scheduler")
    finally:
        db.close()
    assert result["status"] == "succeeded"
    measurement = (result.get("result") or {}).get("impact_measurement") or {}
    assert measurement.get("hotel_id") == hangzhou.id
    assert measurement.get("updated") >= 1
    row = _reload(row.id)
    assert row.payload["outcome"] == OUTCOME_MEASURED
    assert row.payload["impact"]["realized"]["delta"]["revenue_abs"] == 120 * 2
