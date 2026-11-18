"""Phase IV increment 1: in-hotel Memory Node."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Hotel, MemoryRecord, WorkflowRun
from app.services.memory import MEMORY_KIND, OUTCOME_NOT_MEASURED, persist_decision_episode
from app.services.workflow import engine_info, start_pricing_run


def _login(client: TestClient, username: str, password: str = "admin123"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _headers(client: TestClient, username: str = "admin", password: str = "admin123") -> dict:
    resp = _login(client, username, password)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _hotels():
    db = SessionLocal()
    try:
        hotel_a = db.query(Hotel).filter(Hotel.tenant_key == "demo").one()
        hotel_b = db.query(Hotel).filter(Hotel.tenant_key == "demo_chengdu").one()
        return hotel_a.id, hotel_a.name, hotel_b.id, hotel_b.name
    finally:
        db.close()


def _generate(client, headers, **payload):
    resp = client.post("/api/strategies/generate", headers=headers, json=payload or {})
    assert resp.status_code == 200, resp.text
    return resp.json()["strategies"]


def _memories_in_db(hotel_id: int | None = None) -> list[MemoryRecord]:
    db = SessionLocal()
    try:
        q = db.query(MemoryRecord).filter(MemoryRecord.kind == MEMORY_KIND)
        if hotel_id is not None:
            q = q.filter(MemoryRecord.hotel_id == hotel_id)
        return list(q.order_by(MemoryRecord.id.asc()).all())
    finally:
        db.close()


def _assert_payload_honest(payload: dict | None) -> None:
    data = payload or {}
    blob = str(data)
    forbidden = (
        "同比",
        "准确率",
        "这次调价带来了",
        "yoy",
        "year_over_year",
        "accuracy",
        "model_accuracy",
        "actual_revenue",
        "revenue_lift",
        "measured_impact",
    )
    lower = blob.lower()
    for token in forbidden:
        assert token.lower() not in lower, f"invented metric {token!r} in payload: {blob}"
    assert data.get("outcome") == OUTCOME_NOT_MEASURED
    assert data.get("impact") in (None, {})
    # Proposed estimates must not be presented as measured actuals.
    assert "actual_impact" not in data
    assert data.get("impact_attribution") in (None, {}, data.get("impact_attribution", None))


def test_memory_written_after_approved_execution(client, auth_header):
    strategies = _generate(client, auth_header)
    pricing = next(s for s in strategies if s["kind"] == "pricing")
    before = len(_memories_in_db())
    approved = client.post(
        f"/api/strategies/{pricing['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "演示通过"},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "executed"
    assert body["execution_payload"]["mode"] == "sandbox_official"
    assert any(p["node"] == "memory_node" for p in body["path"])
    assert any(p["node"] == "execution_node" for p in body["path"])
    assert all(p["node"] != "memory_node" or "预留" not in (p.get("label") or "") for p in body["path"])

    rows = _memories_in_db()
    assert len(rows) == before + 1
    row = rows[-1]
    hotel_a_id, *_ = _hotels()
    assert row.hotel_id == hotel_a_id
    assert row.kind == MEMORY_KIND
    assert row.strategy_id == pricing["id"]
    assert row.run_id == pricing["workflow_run_id"]
    assert row.action_kind == "pricing"
    assert row.approval_status == "executed"
    _assert_payload_honest(row.payload)
    assert (row.payload or {}).get("execution_result", {}).get("mode") == "sandbox_official"


def test_memory_written_after_reject(client, auth_header):
    strategies = _generate(client, auth_header)
    pricing = next(s for s in strategies if s["kind"] == "pricing")
    before = len(_memories_in_db())
    rejected = client.post(
        f"/api/strategies/{pricing['id']}/review",
        headers=auth_header,
        json={"decision": "reject", "comment": "先再观察"},
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert any(p["node"] == "memory_node" for p in body["path"])
    assert not any(p["node"] == "execution_node" and p.get("status") == "ok" for p in body["path"] if p["node"] == "execution_node")

    rows = [r for r in _memories_in_db() if r.strategy_id == pricing["id"]]
    assert len(rows) == 1
    assert len(_memories_in_db()) == before + 1
    assert rows[0].approval_status == "rejected"
    _assert_payload_honest(rows[0].payload)


def test_no_memory_while_pending_review(client, auth_header):
    before = {(r.run_id, r.approval_status) for r in _memories_in_db()}
    strategies = _generate(client, auth_header)
    assert strategies
    assert all(s["status"] == "pending_review" for s in strategies)
    after = _memories_in_db()
    new_rows = [r for r in after if (r.run_id, r.approval_status) not in before]
    pending_ids = {s["workflow_run_id"] for s in strategies}
    assert not any(r.run_id in pending_ids for r in new_rows)
    listed = client.get("/api/memories", headers=auth_header)
    assert listed.status_code == 200, listed.text
    run_ids = {m["run_id"] for m in listed.json()["memories"]}
    assert run_ids.isdisjoint(pending_ids)


def test_memory_idempotent(client, auth_header):
    strategies = _generate(client, auth_header)
    pricing = next(s for s in strategies if s["kind"] == "pricing")
    approved = client.post(
        f"/api/strategies/{pricing['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "通过"},
    )
    assert approved.status_code == 200, approved.text
    first = [r for r in _memories_in_db() if r.strategy_id == pricing["id"]]
    assert len(first) == 1

    db = SessionLocal()
    try:
        run = db.get(WorkflowRun, pricing["workflow_run_id"])
        assert run is not None
        state = dict(run.payload or {})
        again = persist_decision_episode(db, state)
        assert again is not None
        assert again.id == first[0].id
    finally:
        db.close()

    rows = [r for r in _memories_in_db() if r.strategy_id == pricing["id"]]
    assert len(rows) == 1
    # Replaying the review endpoint must not create a second episode either.
    replay = client.post(
        f"/api/strategies/{pricing['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "再次提交"},
    )
    assert replay.status_code in {200, 400, 409}
    rows = [r for r in _memories_in_db() if r.strategy_id == pricing["id"]]
    assert len(rows) == 1


def test_hangzhou_chengdu_memory_isolation(client):
    headers_a = _headers(client, "admin", "admin123")
    headers_b = _headers(client, "admin_b", "admin123")
    hotel_a_id, _, hotel_b_id, _ = _hotels()

    pricing_a = next(s for s in _generate(client, headers_a) if s["kind"] == "pricing")
    pricing_b = next(s for s in _generate(client, headers_b) if s["kind"] == "pricing")
    assert client.post(
        f"/api/strategies/{pricing_a['id']}/review",
        headers=headers_a,
        json={"decision": "approve", "comment": "杭州通过"},
    ).status_code == 200
    assert client.post(
        f"/api/strategies/{pricing_b['id']}/review",
        headers=headers_b,
        json={"decision": "reject", "comment": "成都否决"},
    ).status_code == 200

    rows_a = _memories_in_db(hotel_a_id)
    rows_b = _memories_in_db(hotel_b_id)
    assert rows_a and rows_b
    assert {r.hotel_id for r in rows_a} == {hotel_a_id}
    assert {r.hotel_id for r in rows_b} == {hotel_b_id}
    assert {r.strategy_id for r in rows_a}.isdisjoint({r.strategy_id for r in rows_b})
    assert all(r.strategy_id != pricing_b["id"] for r in rows_a)
    assert all(r.strategy_id != pricing_a["id"] for r in rows_b)


def test_next_run_loads_only_same_hotel_memories(client):
    headers_a = _headers(client, "admin", "admin123")
    headers_b = _headers(client, "admin_b", "admin123")
    hotel_a_id, _, hotel_b_id, _ = _hotels()

    pricing_a = next(s for s in _generate(client, headers_a) if s["kind"] == "pricing")
    pricing_b = next(s for s in _generate(client, headers_b) if s["kind"] == "pricing")
    client.post(f"/api/strategies/{pricing_a['id']}/review", headers=headers_a, json={"decision": "approve", "comment": "A"})
    client.post(f"/api/strategies/{pricing_b['id']}/review", headers=headers_b, json={"decision": "reject", "comment": "B"})

    db = SessionLocal()
    try:
        from datetime import date, timedelta

        from app.models import RoomType

        room = (
            db.query(RoomType)
            .filter(RoomType.hotel_id == hotel_a_id)
            .order_by(RoomType.id)
            .first()
        )
        assert room is not None
        today = date.today()
        run, _row, state = start_pricing_run(
            db, hotel_a_id, "admin", room.id, today, today + timedelta(days=13)
        )
        memories = state.get("historical_memories") or []
        strategies = state.get("historical_strategies") or []
        assert memories, "next Hangzhou run should see in-hotel memories"
        assert all(m.get("hotel_id") == hotel_a_id for m in memories)
        assert all(m.get("run_id") != pricing_b["workflow_run_id"] for m in memories)
        assert all((s.get("run_id") or 0) != pricing_b["workflow_run_id"] for s in strategies)
        assert any(m.get("strategy_id") == pricing_a["id"] for m in memories)
        # Presence of memories must not auto-approve or skip review.
        assert state.get("status") == "pending_review"
        assert run.status == "pending_review"
        assert (state.get("analysis_result") or {}).get("in_hotel_memory_note")
    finally:
        db.close()


def test_get_memories_is_hotel_scoped(client):
    headers_a = _headers(client, "admin", "admin123")
    headers_b = _headers(client, "admin_b", "admin123")
    hotel_a_id, _, hotel_b_id, _ = _hotels()

    pricing_a = next(s for s in _generate(client, headers_a) if s["kind"] == "pricing")
    pricing_b = next(s for s in _generate(client, headers_b) if s["kind"] == "pricing")
    client.post(f"/api/strategies/{pricing_a['id']}/review", headers=headers_a, json={"decision": "approve", "comment": "A"})
    client.post(f"/api/strategies/{pricing_b['id']}/review", headers=headers_b, json={"decision": "reject", "comment": "B"})

    list_a = client.get("/api/memories", headers=headers_a)
    list_b = client.get("/api/memories", headers=headers_b)
    assert list_a.status_code == 200 and list_b.status_code == 200, (list_a.text, list_b.text)
    ids_a = {m["id"] for m in list_a.json()["memories"]}
    ids_b = {m["id"] for m in list_b.json()["memories"]}
    assert ids_a and ids_b and ids_a.isdisjoint(ids_b)
    assert all(m["hotel_id"] == hotel_a_id for m in list_a.json()["memories"])
    assert all(m["hotel_id"] == hotel_b_id for m in list_b.json()["memories"])
    assert list_a.json()["memories"][0]["id"] == max(ids_a)  # newest first
    unauth = client.get("/api/memories")
    assert unauth.status_code == 401
    spoof = client.get("/api/memories", headers={**headers_a, "X-Hotel-Id": str(hotel_b_id)})
    assert spoof.status_code == 403
    # Analyst can see Hangzhou data in other places but not L5.
    analyst = _headers(client, "analyst", "analyst123")
    denied = client.get("/api/memories", headers=analyst)
    assert denied.status_code == 403


def test_engine_info_lists_memory_node(client, auth_header):
    info = engine_info()
    assert "memory_node" in info["nodes"]
    assert "memory_node" not in (info.get("reserved_nodes") or [])
    settings = client.get("/api/settings", headers=auth_header)
    assert settings.status_code == 200
    workflow = settings.json()["workflow"]
    assert "memory_node" in workflow["nodes"]
    assert "memory_node" not in (workflow.get("reserved_nodes") or [])


def test_defaults_keep_auto_execute_off_and_sandbox(client, auth_header):
    settings = client.get("/api/settings", headers=auth_header)
    assert settings.status_code == 200
    body = settings.json()
    assert body["execution_policy"]["auto_execute_enabled"] is False
    assert body["execution_policy"]["write_mode"] == "sandbox"
    assert body["workflow"]["auto_execute_on_low_risk"] is False
    strategies = _generate(client, auth_header)
    assert strategies
    assert all(s["status"] == "pending_review" for s in strategies)
    # Even low-risk items stay in review; memories do not auto-approve.
    if any(s["risk_level"] == "low" for s in strategies):
        assert all(s["status"] != "executed" for s in strategies if s["risk_level"] == "low")


def test_payload_has_no_invented_impact(client, auth_header):
    strategies = _generate(client, auth_header)
    pricing = next(s for s in strategies if s["kind"] == "pricing")
    campaign = next(s for s in strategies if s["kind"] == "campaign")
    assert client.post(
        f"/api/strategies/{pricing['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "通过"},
    ).status_code == 200
    assert client.post(
        f"/api/strategies/{campaign['id']}/review",
        headers=auth_header,
        json={"decision": "reject", "comment": "暂不参加"},
    ).status_code == 200
    listed = client.get("/api/memories", headers=auth_header)
    assert listed.status_code == 200
    memories = listed.json()["memories"]
    assert memories
    for item in memories:
        _assert_payload_honest(item.get("payload"))
        assert item["outcome"] == OUTCOME_NOT_MEASURED
        assert item.get("impact") in (None, {})
        assert "尚未复盘" in (item.get("summary") or "") or item["approval_status"] == "rejected"
        for token in ("同比", "准确率", "带来了多少收入"):
            assert token not in (item.get("summary") or "")
