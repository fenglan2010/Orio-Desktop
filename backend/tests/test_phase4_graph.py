"""Phase IV increment 4: in-hotel knowledge graph."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import GraphEdge, GraphNode, Hotel, MemoryRecord, RoomType, WorkflowRun
from app.services.impact import OUTCOME_MEASURED, measure_hotel_episodes
from app.services.knowledge_graph import rebuild_hotel_graph, serialize_graph
from app.services.memory import persist_decision_episode


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
        date_from = overrides.pop("date_from", today - timedelta(days=5))
        date_to = overrides.pop("date_to", today - timedelta(days=3))
        room_type_id = overrides.pop("room_type_id", room.id)
        run = WorkflowRun(
            hotel_id=hotel_id,
            kind="pricing_strategy" if kind == "pricing" else kind,
            status=status,
            current_node="memory_node",
            payload={},
            created_by="graph-tester",
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
            "actor": "graph-tester",
            "as_of": today.isoformat(),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "room_type_id": room_type_id,
            "signals": overrides.pop("signals", {"flags": ["rank_drop", "conversion_drop"]}),
            "strategy": {
                "kind": kind,
                "room_type_id": room_type_id,
                "room_name": room.name,
                "current_price": 512,
                "recommended_price": 488,
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


FORBIDDEN = ("同比", "yoy", "year_over_year", "accuracy", "caused_revenue", "causal_revenue", "revenue_lift")


def _assert_clean(blob, *names: str) -> None:
    text = str(blob).lower()
    for token in FORBIDDEN:
        assert token.lower() not in text, token
    for name in names:
        assert name not in str(blob)


def test_graph_is_hotel_scoped_and_has_no_foreign_identity(client):
    hangzhou, chengdu = _hotels()
    hz_room = _room(hangzhou.id)
    cd_room = _room(chengdu.id)
    today = date.today()
    start, end = today - timedelta(days=8), today - timedelta(days=6)
    hz = _persist_episode(hangzhou.id, date_from=start, date_to=end, room_type_id=hz_room.id)
    cd = _persist_episode(
        chengdu.id,
        date_from=start,
        date_to=end,
        room_type_id=cd_room.id,
        signals={"flags": ["rank_drop", "conversion_drop"]},
    )
    assert hz.hotel_id == hangzhou.id
    db = SessionLocal()
    try:
        graph = serialize_graph(db, hangzhou.id)
        node_hotels = {n.hotel_id for n in db.query(GraphNode).filter(GraphNode.hotel_id == hangzhou.id)}
        assert node_hotels == {hangzhou.id}
        assert db.query(GraphNode).filter(GraphNode.hotel_id == hangzhou.id).count() >= 1
        keys = {n["key"] for n in graph["nodes"]}
        assert any(k.startswith("episode:") for k in keys)
        assert any(n["node_type"] == "signal_type" for n in graph["nodes"])
        assert any(n["node_type"] == "room_type" and n["label"] == hz_room.name for n in graph["nodes"])
        _assert_clean(graph, chengdu.name, "demo_chengdu")
        for node in graph["nodes"]:
            attrs = node.get("attributes") or {}
            assert attrs.get("hotel_id") in (None, hangzhou.id)
            assert "tenant_key" not in attrs
    finally:
        db.close()

    listed = client.get("/api/evolution/graph", headers=_headers(client))
    assert listed.status_code == 200
    body = listed.json()
    assert body["nodes"]
    assert body["edges"]
    assert "当前酒店" in body["note"] or "不含其他酒店" in body["summary"]
    _assert_clean(body, chengdu.name, "admin_b")

    other = client.get("/api/evolution/graph", headers=_headers(client, "admin_b"))
    assert other.status_code == 200
    other_body = other.json()
    other_keys = {n["key"] for n in other_body["nodes"]}
    assert f"episode:{hz.id}" not in other_keys
    assert f"episode:{cd.id}" in other_keys
    _assert_clean(other_body, hangzhou.name, "demo")
    # No write API for painting fake edges.
    assert client.post("/api/evolution/graph", headers=_headers(client)).status_code in {404, 405, 422}


def test_graph_rebuild_is_idempotent_and_includes_visible_patterns(client):
    hangzhou, chengdu = _hotels()
    today = date.today()
    start, end = today - timedelta(days=9), today - timedelta(days=7)
    signals = {"flags": ["rank_drop", "competitor_cut", "conversion_drop"]}
    _persist_episode(hangzhou.id, date_from=start, date_to=end, room_type_id=_room(hangzhou.id).id, signals=signals)
    _persist_episode(chengdu.id, date_from=start, date_to=end, room_type_id=_room(chengdu.id).id, signals=signals)
    db = SessionLocal()
    try:
        first = rebuild_hotel_graph(db, hangzhou.id)
        node_count = len(first["nodes"])
        edge_count = len(first["edges"])
        second = rebuild_hotel_graph(db, hangzhou.id)
        assert len(second["nodes"]) == node_count
        assert len(second["edges"]) == edge_count
        assert db.query(GraphNode).filter(GraphNode.hotel_id == hangzhou.id).count() == node_count
        pattern_nodes = [n for n in second["nodes"] if n["node_type"] == "industry_pattern"]
        for node in pattern_nodes:
            assert "pattern_key" in (node.get("attributes") or {})
            assert hangzhou.name not in str(node)
            assert chengdu.name not in str(node)
        if pattern_nodes:
            assert any(e["edge_type"] == "similar_to" for e in second["edges"])
    finally:
        db.close()


def test_measured_rejected_episode_gets_observed_outcome_node(client):
    hangzhou, _ = _hotels()
    room = _room(hangzhou.id)
    today = date.today()
    start, end = today - timedelta(days=6), today - timedelta(days=4)
    row = _persist_episode(hangzhou.id, status="rejected", date_from=start, date_to=end, room_type_id=room.id)
    db = SessionLocal()
    try:
        from app.models import DailyMetric

        metrics = (
            db.query(DailyMetric)
            .filter(DailyMetric.hotel_id == hangzhou.id, DailyMetric.date >= start, DailyMetric.date <= end, DailyMetric.room_type_id == room.id)
            .all()
        )
        for metric in metrics:
            metric.revenue = float(metric.revenue or 0) + 100
            metric.is_future = False
        db.commit()
        measure_hotel_episodes(db, hangzhou.id, today=today)
        graph = serialize_graph(db, hangzhou.id)
        assert any(n["node_type"] == "observed_outcome" and n["attributes"].get("terminal_status") == "rejected" for n in graph["nodes"])
        assert any(e["edge_type"] == "observed_outcome" for e in graph["edges"])
        reloaded = db.get(MemoryRecord, row.id)
        assert reloaded.payload["outcome"] == OUTCOME_MEASURED
    finally:
        db.close()
