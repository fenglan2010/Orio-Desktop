"""Phase IV increment 2: anonymized L5 cross-hotel learning."""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import (
    Hotel,
    IndustryPattern,
    IndustryPatternContribution,
    MemoryRecord,
    RoomType,
    WorkflowRun,
)
from app.services.industry_patterns import (
    OUTCOME_NOT_MEASURED,
    extract_features,
    pattern_key,
    record_episode_pattern,
)
from app.services.memory import MEMORY_KIND, persist_decision_episode
from app.services.workflow import start_pricing_run


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
        return hotel_a, hotel_b
    finally:
        db.close()


def _generate(client, headers, **payload):
    resp = client.post("/api/strategies/generate", headers=headers, json=payload or {})
    assert resp.status_code == 200, resp.text
    return resp.json()["strategies"]


MATCH_SIGNALS = ["rank_drop", "competitor_cut", "conversion_drop"]
MATCH_PRICES = {"current_price": 512, "recommended_price": 488}  # 降; amounts must not leak via patterns API


def _episode_state(hotel_id: int, run_id: int, **overrides) -> dict:
    state = {
        "hotel_id": hotel_id,
        "run_id": run_id,
        "kind": "pricing",
        "status": "executed",
        "approval_status": "executed",
        "risk_level": "medium",
        "actor": "pattern-tester",
        "signals": {"flags": list(MATCH_SIGNALS)},
        "strategy": {
            "kind": "pricing",
            "room_name": "本店内部房型",
            **MATCH_PRICES,
        },
        "execution_result": {"accepted": True, "mode": "sandbox_official", "outcome": "accepted"},
        "checkpoint_id": f"cp-hotel-{hotel_id}-run-{run_id}",
    }
    extra_strategy = overrides.pop("strategy", None)
    state.update(overrides)
    if extra_strategy:
        strategy = dict(state["strategy"])
        strategy.update(extra_strategy)
        state["strategy"] = strategy
    return state


def _persist_episode(hotel_id: int, **overrides) -> MemoryRecord:
    db = SessionLocal()
    try:
        flags = list((overrides.get("signals") or {}).get("flags") or MATCH_SIGNALS)
        run = WorkflowRun(
            hotel_id=hotel_id,
            kind="pricing_strategy",
            status=overrides.get("status") or "executed",
            current_node="memory_node",
            payload={"signals": {"flags": flags}},
            created_by="pattern-tester",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        state = _episode_state(hotel_id, run.id, **overrides)
        row = persist_decision_episode(db, state)
        assert row is not None
        db.refresh(row)
        return row
    finally:
        db.close()


def _identifying_tokens(hotel_a: Hotel, hotel_b: Hotel) -> list[str]:
    tokens = [
        hotel_a.name,
        hotel_b.name,
        hotel_a.tenant_key,
        hotel_b.tenant_key,
        hotel_a.address,
        hotel_b.address,
        hotel_a.city,
        hotel_b.city,
        "杭州西湖精品酒店",
        "成都春熙商务酒店",
        "demo_chengdu",
        "杭州市西湖区湖滨路88号",
        "成都市锦江区春熙路12号",
        "豪华大床房",
        "高级大床房",
        "家庭房",
        "本店内部房型",
        "西湖和禧",
        "湖滨商务酒店",
        "湖畔丽舍",
        "春熙商旅",
        "太古里精选",
        "admin_b",
        "成都店管理员",
        "pattern-tester",
        hotel_a.positioning,
        hotel_b.positioning,
        "春熙路",
        "湖滨路",
        "西湖区",
        "锦江区",
        "杭州",
        "成都",
    ]
    return [t for t in tokens if t]


FORBIDDEN_PATTERN_KEYS = {
    "hotel_id",
    "hotel_name",
    "tenant_key",
    "address",
    "city",
    "checkpoint_id",
    "strategy_id",
    "run_id",
    "actor",
    "guest_name",
    "room_name",
    "room_type",
    "room_type_id",
    "current_price",
    "recommended_price",
    "list_price",
    "base_price",
    "min_price",
    "max_price",
    "hotel_ids",
    "contributors",
    "contributor_ids",
    "memory_record_id",
    "memory_id",
    "episode_key",
}


INVENTED_METRICS = (
    "同比",
    "准确率",
    "收入提升",
    "这次调价带来了",
    "yoy",
    "year_over_year",
    "accuracy",
    "model_accuracy",
    "actual_revenue",
    "revenue_lift",
    "measured_impact",
)


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_keys(item)


def _assert_patterns_anonymous(body: dict, hotel_a: Hotel, hotel_b: Hotel) -> None:
    blob = json.dumps(body, ensure_ascii=False)
    lower = blob.lower()
    for token in _identifying_tokens(hotel_a, hotel_b):
        assert token not in blob, f"identifying token {token!r} leaked in patterns payload: {blob}"
    for key in _walk_keys(body):
        assert key not in FORBIDDEN_PATTERN_KEYS, f"forbidden key {key!r} in patterns payload: {blob}"
    for token in INVENTED_METRICS:
        assert token.lower() not in lower, f"invented metric {token!r} in patterns payload: {blob}"
    for amount in (512, 488, 688, 628, 858, 458, 428, 1288, 1188, 1588, 398, 298, 788, 278, 728, 568):
        assert not re.search(rf"(¥|￥|人民币)\s*{amount}(\.0+)?", blob), f"price amount {amount} leaked: {blob}"
        assert f'"{amount}"' not in blob
        assert f": {amount}" not in blob and f":{amount}," not in blob and f":{amount}}}" not in blob
    for item in body.get("patterns") or []:
        assert item.get("outcome") == OUTCOME_NOT_MEASURED
        assert item.get("impact") in (None, {})
        assert "尚未测算" in (item.get("summary") or "")
        assert "匿名" in (item.get("summary") or "")


def test_memories_api_still_isolated_between_hotels(client):
    headers_a = _headers(client, "admin")
    headers_b = _headers(client, "admin_b")
    hotel_a, hotel_b = _hotels()

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

    list_a = client.get("/api/memories", headers=headers_a)
    list_b = client.get("/api/memories", headers=headers_b)
    assert list_a.status_code == 200 and list_b.status_code == 200, (list_a.text, list_b.text)
    mem_a = list_a.json()["memories"]
    mem_b = list_b.json()["memories"]
    ids_a = {m["id"] for m in mem_a}
    ids_b = {m["id"] for m in mem_b}
    assert ids_a and ids_b and ids_a.isdisjoint(ids_b)
    assert all(m["hotel_id"] == hotel_a.id for m in mem_a)
    assert all(m["hotel_id"] == hotel_b.id for m in mem_b)
    assert all(m["strategy_id"] != pricing_b["id"] for m in mem_a)
    assert all(m["strategy_id"] != pricing_a["id"] for m in mem_b)
    blob_a = json.dumps(mem_a, ensure_ascii=False)
    blob_b = json.dumps(mem_b, ensure_ascii=False)
    assert hotel_b.name not in blob_a and hotel_b.tenant_key not in blob_a
    assert hotel_a.name not in blob_b

    db = SessionLocal()
    try:
        rows_a = db.query(MemoryRecord).filter(MemoryRecord.hotel_id == hotel_a.id, MemoryRecord.kind == MEMORY_KIND).all()
        rows_b = db.query(MemoryRecord).filter(MemoryRecord.hotel_id == hotel_b.id, MemoryRecord.kind == MEMORY_KIND).all()
        assert rows_a and rows_b
        assert {r.hotel_id for r in rows_a} == {hotel_a.id}
        assert {r.hotel_id for r in rows_b} == {hotel_b.id}
    finally:
        db.close()


def test_lone_pattern_is_not_shown_as_peer_pattern(client):
    headers_a = _headers(client, "admin")
    headers_b = _headers(client, "admin_b")
    hotel_a, hotel_b = _hotels()
    row = _persist_episode(hotel_a.id)
    assert row.hotel_id == hotel_a.id

    db = SessionLocal()
    try:
        contribs = (
            db.query(IndustryPatternContribution)
            .filter(IndustryPatternContribution.episode_key == row.episode_key)
            .all()
        )
        assert len(contribs) == 1
        assert contribs[0].hotel_id == hotel_a.id
        hotels = {c.hotel_id for c in db.query(IndustryPatternContribution).all()}
        assert hotel_b.id not in hotels
    finally:
        db.close()

    list_a = client.get("/api/memories/patterns", headers=headers_a)
    list_b = client.get("/api/memories/patterns", headers=headers_b)
    assert list_a.status_code == 200 and list_b.status_code == 200, (list_a.text, list_b.text)
    body_a, body_b = list_a.json(), list_b.json()
    assert body_a["patterns"] == []
    assert body_b["patterns"] == []
    assert body_a["total"] == 0 and body_b["total"] == 0
    _assert_patterns_anonymous(body_a, hotel_a, hotel_b)
    _assert_patterns_anonymous(body_b, hotel_a, hotel_b)
    assert "匿名模式" in (body_a.get("note") or "")
    assert "不会训练模型" in (body_a.get("note") or "")


def test_matching_episodes_expose_anonymized_pattern_both_ways(client):
    headers_a = _headers(client, "admin")
    headers_b = _headers(client, "admin_b")
    hotel_a, hotel_b = _hotels()
    row_a = _persist_episode(hotel_a.id)
    before = client.get("/api/memories/patterns", headers=headers_a)
    assert before.status_code == 200
    assert before.json()["patterns"] == []

    row_b = _persist_episode(hotel_b.id)
    db = SessionLocal()
    try:
        features_a = extract_features(db, hotel_a.id, None, row_a.payload)
        features_b = extract_features(db, hotel_b.id, None, row_b.payload)
        assert pattern_key(features_a) == pattern_key(features_b)
        assert db.query(IndustryPattern).filter(IndustryPattern.pattern_key == pattern_key(features_a)).count() == 1
        contrib_hotels = {
            c.hotel_id
            for c in db.query(IndustryPatternContribution)
            .join(IndustryPattern, IndustryPattern.id == IndustryPatternContribution.pattern_id)
            .filter(IndustryPattern.pattern_key == pattern_key(features_a))
            .all()
        }
        assert contrib_hotels == {hotel_a.id, hotel_b.id}
    finally:
        db.close()

    list_a = client.get("/api/memories/patterns", headers=headers_a)
    list_b = client.get("/api/memories/patterns", headers=headers_b)
    assert list_a.status_code == 200 and list_b.status_code == 200, (list_a.text, list_b.text)
    body_a, body_b = list_a.json(), list_b.json()
    assert body_a["patterns"] and body_b["patterns"]
    _assert_patterns_anonymous(body_a, hotel_a, hotel_b)
    _assert_patterns_anonymous(body_b, hotel_a, hotel_b)
    keys_a = {p["pattern_key"] for p in body_a["patterns"]}
    keys_b = {p["pattern_key"] for p in body_b["patterns"]}
    assert keys_a == keys_b
    for body in (body_a, body_b):
        item = body["patterns"][0]
        assert item["action_kind"] == "pricing"
        assert item["terminal_status"] == "executed"
        assert item["price_direction"] == "降"
        assert set(item["signal_types"]) == set(MATCH_SIGNALS)
        assert item["signal_types"] == [s for s in ("rank_drop", "price_inversion", "conversion_drop", "competitor_cut", "negative_review") if s in MATCH_SIGNALS]
        assert item["peer_hotel_count"] == 1
        assert item["peer_episode_count"] == 1
        assert item["outcome"] == OUTCOME_NOT_MEASURED
        if item.get("positioning_bucket"):
            assert item["positioning_bucket"] in {"精品", "商务"}

    mem_a = client.get("/api/memories", headers=headers_a).json()["memories"]
    mem_b = client.get("/api/memories", headers=headers_b).json()["memories"]
    assert {m["id"] for m in mem_a}.isdisjoint({m["id"] for m in mem_b})
    assert all(m["hotel_id"] == hotel_a.id for m in mem_a)
    assert all(m["hotel_id"] == hotel_b.id for m in mem_b)


def test_same_episode_does_not_double_count_pattern(client):
    hotel_a, _hotel_b = _hotels()
    row = _persist_episode(hotel_a.id)
    db = SessionLocal()
    try:
        again = persist_decision_episode(
            db,
            _episode_state(hotel_a.id, row.run_id, status=row.approval_status, approval_status=row.approval_status),
        )
        assert again is not None
        assert again.id == row.id
        record_episode_pattern(db, again, _episode_state(hotel_a.id, row.run_id))
        contribs = (
            db.query(IndustryPatternContribution)
            .filter(IndustryPatternContribution.episode_key == row.episode_key)
            .all()
        )
        assert len(contribs) == 1
        pattern = db.get(IndustryPattern, contribs[0].pattern_id)
        assert pattern.episode_count == 1
    finally:
        db.close()


def test_next_hangzhou_run_loads_peer_patterns_but_in_hotel_memories_only(client):
    headers_a = _headers(client, "admin")
    hotel_a, hotel_b = _hotels()
    _persist_episode(hotel_a.id)
    _persist_episode(hotel_b.id)

    db = SessionLocal()
    try:
        room = db.query(RoomType).filter(RoomType.hotel_id == hotel_a.id).order_by(RoomType.id).first()
        assert room is not None
        from datetime import date, timedelta

        today = date.today()
        run, _row, state = start_pricing_run(db, hotel_a.id, "admin", room.id, today, today + timedelta(days=13))
        memories = state.get("historical_memories") or []
        strategies = state.get("historical_strategies") or []
        peers = state.get("peer_patterns") or state.get("industry_patterns") or []
        assert memories, "next Hangzhou run should still see in-hotel memories"
        assert all(m.get("hotel_id") == hotel_a.id for m in memories)
        assert all(m.get("hotel_id") != hotel_b.id for m in memories)
        assert all((s.get("run_id") or 0) not in {m.get("run_id") for m in []} for s in strategies)
        assert all(
            (m.get("room_name") or "") != "高级大床房" for m in memories
        ), "Chengdu room names must not appear in Hangzhou historical_memories"
        assert peers, "next Hangzhou run should load visible anonymized peer patterns"
        _assert_patterns_anonymous({"patterns": peers}, hotel_a, hotel_b)
        assert state.get("status") == "pending_review"
        assert run.status == "pending_review"
        diagnosis = state.get("analysis_result") or {}
        assert diagnosis.get("peer_pattern_note")
        assert "匿名" in diagnosis["peer_pattern_note"]
        assert "不会自动改价" in diagnosis["peer_pattern_note"]
        # Peer patterns must not auto-approve or skip review.
        assert state.get("approval_status") in {"pending", "pending_review", None} or state.get("status") == "pending_review"
        assert state.get("auto_executed") in (None, False)
    finally:
        db.close()

    settings = client.get("/api/settings", headers=headers_a)
    assert settings.status_code == 200
    body = settings.json()
    assert body["execution_policy"]["auto_execute_enabled"] is False
    assert body["execution_policy"]["write_mode"] == "sandbox"
    assert body["workflow"]["auto_execute_on_low_risk"] is False


def test_patterns_never_skip_review_or_flip_defaults(client, auth_header):
    hotel_a, hotel_b = _hotels()
    _persist_episode(hotel_a.id)
    _persist_episode(hotel_b.id)
    settings = client.get("/api/settings", headers=auth_header)
    assert settings.status_code == 200
    body = settings.json()
    assert body["execution_policy"]["auto_execute_enabled"] is False
    assert body["execution_policy"]["write_mode"] == "sandbox"
    assert body["workflow"]["auto_execute_on_low_risk"] is False
    strategies = _generate(client, auth_header)
    assert strategies
    assert all(s["status"] == "pending_review" for s in strategies)
    if any(s["risk_level"] == "low" for s in strategies):
        assert all(s["status"] != "executed" for s in strategies if s["risk_level"] == "low")


def test_patterns_api_auth_and_note(client):
    hotel_a, hotel_b = _hotels()
    _persist_episode(hotel_a.id)
    _persist_episode(hotel_b.id)
    unauth = client.get("/api/memories/patterns")
    assert unauth.status_code == 401
    analyst = _headers(client, "analyst", "analyst123")
    denied = client.get("/api/memories/patterns", headers=analyst)
    assert denied.status_code == 403
    headers_a = _headers(client, "admin")
    spoof = client.get("/api/memories/patterns", headers={**headers_a, "X-Hotel-Id": str(hotel_b.id)})
    assert spoof.status_code == 403
    ok = client.get("/api/memories/patterns", headers=headers_a)
    assert ok.status_code == 200
    body = ok.json()
    assert body["mode"] == "anonymous"
    assert body["note"] == "跨店学习已开始，但是匿名模式，不展示其他酒店名称，不会训练模型，不会自动改价。"
    _assert_patterns_anonymous(body, hotel_a, hotel_b)


def test_pattern_payload_has_no_invented_impact(client):
    headers_a = _headers(client, "admin")
    hotel_a, hotel_b = _hotels()
    _persist_episode(hotel_a.id, status="executed", approval_status="executed")
    _persist_episode(hotel_b.id, status="executed", approval_status="executed")
    body = client.get("/api/memories/patterns", headers=headers_a).json()
    assert body["patterns"]
    _assert_patterns_anonymous(body, hotel_a, hotel_b)
    for item in body["patterns"]:
        assert "同比" not in (item.get("summary") or "")
        assert "准确率" not in (item.get("summary") or "")
        assert "收入提升" not in (item.get("summary") or "")
        assert item["outcome"] == "not_measured"
