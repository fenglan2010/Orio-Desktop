"""Phase IV increment 4: offline RL and the conservative evolution engine."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import EvolutionEngineState, Hotel, MemoryRecord, RoomType, Strategy, WorkflowRun
from app.services.impact import measure_hotel_episodes
from app.services.memory import persist_decision_episode
from app.services.risk_policy import load_execution_policy
from app.services.rl import MIN_EPISODES_FOR_POLICY, fit_hotel_policy, serialize_policy


def _login(client: TestClient, username: str, password: str = "admin123"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _headers(client: TestClient, username: str = "admin", password: str = "admin123") -> dict:
    resp = _login(client, username, password)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _hotels():
    db = SessionLocal()
    try:
        return (
            db.query(Hotel).filter(Hotel.tenant_key == "demo").one(),
            db.query(Hotel).filter(Hotel.tenant_key == "demo_chengdu").one(),
        )
    finally:
        db.close()


def _room(hotel_id: int) -> RoomType:
    db = SessionLocal()
    try:
        room = db.query(RoomType).filter(RoomType.hotel_id == hotel_id).order_by(RoomType.id.asc()).first()
        assert room is not None
        db.expunge(room)
        return room
    finally:
        db.close()


def _persist_episode(hotel_id: int, **overrides) -> MemoryRecord:
    db = SessionLocal()
    try:
        today = date.today()
        room = db.query(RoomType).filter(RoomType.hotel_id == hotel_id).order_by(RoomType.id.asc()).first()
        status = overrides.pop("status", "executed")
        kind = overrides.pop("kind", "pricing")
        date_from = overrides.pop("date_from", today - timedelta(days=8))
        date_to = overrides.pop("date_to", today - timedelta(days=6))
        room_type_id = overrides.pop("room_type_id", room.id)
        current = overrides.pop("current_price", 512)
        recommended = overrides.pop("recommended_price", 488)
        run = WorkflowRun(
            hotel_id=hotel_id,
            kind="pricing_strategy",
            status=status,
            current_node="memory_node",
            payload={},
            created_by="rl-tester",
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
            "risk_level": overrides.pop("risk_level", "medium"),
            "actor": "rl-tester",
            "as_of": today.isoformat(),
            "date_from": date_from.isoformat() if isinstance(date_from, date) else date_from,
            "date_to": date_to.isoformat() if isinstance(date_to, date) else date_to,
            "room_type_id": room_type_id,
            "signals": overrides.pop("signals", {"flags": ["conversion_drop"]}),
            "strategy": {
                "kind": kind,
                "room_type_id": room_type_id,
                "room_name": room.name,
                "current_price": current,
                "recommended_price": recommended,
            },
        }
        state.update(overrides)
        row = persist_decision_episode(db, state)
        assert row is not None
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


def _bump(hotel_id: int, start: date, end: date, room_type_id: int, revenue_delta: int) -> None:
    from app.models import DailyMetric

    db = SessionLocal()
    try:
        rows = (
            db.query(DailyMetric)
            .filter(
                DailyMetric.hotel_id == hotel_id,
                DailyMetric.date >= start,
                DailyMetric.date <= end,
                DailyMetric.room_type_id == room_type_id,
            )
            .all()
        )
        assert rows
        for row in rows:
            row.is_future = False
            row.revenue = float(row.revenue or 0) + revenue_delta
            row.sold_rooms = int(row.sold_rooms or 0) + 1
            if row.available_rooms:
                row.sold_rooms = min(row.sold_rooms, row.available_rooms)
                row.occupancy = row.sold_rooms / row.available_rooms
            if row.sold_rooms:
                row.adr = row.revenue / row.sold_rooms
        db.commit()
    finally:
        db.close()


def _measure(hotel_id: int) -> None:
    db = SessionLocal()
    try:
        measure_hotel_episodes(db, hotel_id, today=date.today())
    finally:
        db.close()


FORBIDDEN = (
    "同比",
    "准确率",
    "yoy",
    "year_over_year",
    "year_ago",
    "accuracy",
    "revenue_lift",
    "caused_revenue",
    "causal_revenue",
    "这次调价带来了",
)


def _assert_clean(blob) -> None:
    text = str(blob).lower()
    for token in FORBIDDEN:
        assert token.lower() not in text, token
    if isinstance(blob, dict):
        assert blob.get("causal") in (False, None)
        assert "yoy" not in blob


def test_rl_does_not_use_other_hotel_rewards_or_invent_yoy(client):
    hangzhou, chengdu = _hotels()
    today = date.today()
    start, end = today - timedelta(days=10), today - timedelta(days=8)
    hz_room = _room(hangzhou.id)
    cd_room = _room(chengdu.id)
    signals = {"flags": ["conversion_drop"]}
    for _ in range(3):
        _persist_episode(
            hangzhou.id,
            date_from=start,
            date_to=end,
            room_type_id=hz_room.id,
            signals=signals,
            recommended_price=480,
        )
        _persist_episode(
            chengdu.id,
            date_from=start,
            date_to=end,
            room_type_id=cd_room.id,
            signals=signals,
            recommended_price=480,
        )
    _bump(hangzhou.id, start, end, hz_room.id, 120)
    _bump(chengdu.id, start, end, cd_room.id, 900)
    _measure(hangzhou.id)
    _measure(chengdu.id)
    db = SessionLocal()
    try:
        hz_policy = fit_hotel_policy(db, hangzhou.id)
        cd_policy = serialize_policy(db, chengdu.id)
    finally:
        db.close()
    assert hz_policy["measured_episode_count"] >= 3
    assert hz_policy["insufficient"] is False
    assert hz_policy["reward_definition"]["causal"] is False
    _assert_clean(hz_policy)
    _assert_clean(cd_policy)
    hz_rewards = {row["mean_observed_reward"] for row in hz_policy["action_values"]}
    cd_rewards = {row["mean_observed_reward"] for row in cd_policy["action_values"]}
    assert hz_rewards
    assert cd_rewards
    assert hz_rewards != cd_rewards
    listed = client.get("/api/evolution/rl", headers=_headers(client))
    assert listed.status_code == 200
    body = listed.json()
    assert body["hotel_id"] == hangzhou.id
    _assert_clean(body)
    assert chengdu.name not in str(body)
    other = client.get("/api/evolution/rl", headers=_headers(client, "admin_b"))
    assert other.status_code == 200
    assert other.json()["hotel_id"] == chengdu.id
    assert hangzhou.name not in str(other.json())


def test_rl_insufficient_data_is_honest(client):
    hangzhou, _ = _hotels()
    db = SessionLocal()
    try:
        policy = serialize_policy(db, hangzhou.id)
    finally:
        db.close()
    assert policy["insufficient"] is True
    assert policy["suggestions"] == []
    assert str(MIN_EPISODES_FOR_POLICY) in policy["insufficient_reason"]
    listed = client.get("/api/evolution/rl", headers=_headers(client))
    assert listed.status_code == 200
    assert listed.json()["insufficient"] is True
    _assert_clean(listed.json())


def test_engine_defaults_off_and_toggle_requires_update_policy(client):
    hangzhou, chengdu = _hotels()
    listed = client.get("/api/evolution/engine", headers=_headers(client))
    assert listed.status_code == 200
    body = listed.json()
    assert body["enabled"] is False
    assert body["sandbox_mode"] is False
    assert body["one_click_migrate_enabled"] is False
    assert body["write_mode"] == "sandbox"
    assert body["auto_execute_enabled"] is False
    db = SessionLocal()
    try:
        assert db.query(EvolutionEngineState).filter(EvolutionEngineState.hotel_id == hangzhou.id).one_or_none() is None
        policy = load_execution_policy(db, hotel_id=hangzhou.id)
        assert policy.auto_execute_enabled is False
        assert policy.write_mode == "sandbox"
    finally:
        db.close()

    manager = _headers(client, "manager", "manager123")
    viewed = client.get("/api/evolution/engine", headers=manager)
    assert viewed.status_code == 200
    denied = client.post("/api/evolution/engine", headers=manager, json={"enabled": True})
    assert denied.status_code == 403
    analyst = client.post("/api/auth/login", json={"username": "analyst", "password": "analyst123"})
    assert analyst.status_code == 200
    analyst_headers = {"Authorization": f"Bearer {analyst.json()['access_token']}"}
    assert client.get("/api/evolution/engine", headers=analyst_headers).status_code == 403
    assert client.post("/api/evolution/engine", headers=analyst_headers, json={"enabled": True}).status_code == 403

    enabled = client.post("/api/evolution/engine", headers=_headers(client), json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert "不会跳过人审" in enabled.json()["note"]
    assert enabled.json()["write_mode"] == "sandbox"
    assert enabled.json()["auto_execute_enabled"] is False

    # Chengdu stays off.
    other = client.get("/api/evolution/engine", headers=_headers(client, "admin_b"))
    assert other.status_code == 200
    assert other.json()["enabled"] is False

    db = SessionLocal()
    try:
        policy = load_execution_policy(db, hotel_id=hangzhou.id)
        assert policy.auto_execute_enabled is False
        assert policy.write_mode == "sandbox"
    finally:
        db.close()


def test_invent_and_sandbox_do_not_auto_execute_or_set_live(client):
    hangzhou, _ = _hotels()
    today = date.today()
    start, end = today - timedelta(days=11), today - timedelta(days=9)
    room = _room(hangzhou.id)
    for _ in range(3):
        _persist_episode(
            hangzhou.id,
            date_from=start,
            date_to=end,
            room_type_id=room.id,
            signals={"flags": ["conversion_drop"]},
            recommended_price=470,
        )
    _bump(hangzhou.id, start, end, room.id, 200)
    _measure(hangzhou.id)

    headers = _headers(client)
    # Invent while engine is off should fail.
    closed = client.post("/api/evolution/invent", headers=headers)
    assert closed.status_code == 400
    sandbox_closed = client.post("/api/evolution/sandbox", headers=headers, json={"sandbox_mode": True})
    assert sandbox_closed.status_code == 400

    assert client.post("/api/evolution/engine", headers=headers, json={"enabled": True}).status_code == 200
    invented = client.post("/api/evolution/invent", headers=headers)
    assert invented.status_code == 200
    body = invented.json()
    assert body["auto_executed"] is False
    assert body["write_mode"] == "sandbox"
    _assert_clean(body)
    if body["insufficient"]:
        assert body["created"] == 0
        assert body["candidates"] is not None
    else:
        assert body["created"] >= 1
        db = SessionLocal()
        try:
            for item in body["candidates"]:
                if not item.get("strategy_id"):
                    continue
                strategy = db.get(Strategy, item["strategy_id"])
                assert strategy is not None
                assert strategy.hotel_id == hangzhou.id
                assert strategy.status == "pending_review"
                assert strategy.reviewer in (None, "")
                run = db.get(WorkflowRun, strategy.workflow_run_id)
                assert run is not None
                assert (run.payload or {}).get("auto_executed") is False
                assert (run.payload or {}).get("auto_execute_suppressed") is True
            policy = load_execution_policy(db, hotel_id=hangzhou.id)
            assert policy.auto_execute_enabled is False
            assert policy.write_mode == "sandbox"
        finally:
            db.close()

    entered = client.post("/api/evolution/sandbox", headers=headers, json={"sandbox_mode": True})
    assert entered.status_code == 200
    assert entered.json()["engine"]["sandbox_mode"] is True
    assert entered.json()["engine"]["write_mode"] == "sandbox"
    assert entered.json()["engine"]["auto_execute_enabled"] is False
    for cand in entered.json()["candidates"]:
        evaluation = cand.get("sandbox_evaluation") or {}
        if evaluation:
            assert evaluation.get("live_write") is False
            assert evaluation.get("causal") is False
    exited = client.post("/api/evolution/sandbox", headers=headers, json={"sandbox_mode": False})
    assert exited.status_code == 200
    assert exited.json()["engine"]["sandbox_mode"] is False
    assert exited.json()["engine"]["write_mode"] == "sandbox"
    assert exited.json()["engine"]["auto_execute_enabled"] is False
    assert exited.json()["engine"]["one_click_migrate_enabled"] is False
    db = SessionLocal()
    try:
        policy = load_execution_policy(db, hotel_id=hangzhou.id)
        assert policy.auto_execute_enabled is False
        assert policy.write_mode == "sandbox"
    finally:
        db.close()


def test_review_reply_stays_unmeasured(client):
    hangzhou, _ = _hotels()
    db = SessionLocal()
    try:
        today = date.today()
        run = WorkflowRun(
            hotel_id=hangzhou.id,
            kind="review_reply",
            status="executed",
            current_node="memory_node",
            payload={},
            created_by="rl-tester",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        row = persist_decision_episode(
            db,
            {
                "hotel_id": hangzhou.id,
                "run_id": run.id,
                "kind": "review_reply",
                "status": "executed",
                "approval_status": "executed",
                "risk_level": "low",
                "actor": "rl-tester",
                "as_of": today.isoformat(),
                "strategy": {"kind": "review_reply", "review_id": 1, "draft": "谢谢反馈"},
            },
        )
        assert row is not None
        measure_hotel_episodes(db, hangzhou.id, today=today)
        db.refresh(row)
        assert (row.payload or {}).get("outcome") == "not_measured"
        assert (row.payload or {}).get("impact") is None
    finally:
        db.close()
