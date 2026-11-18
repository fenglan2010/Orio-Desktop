"""Phase III increment 2: durable LangGraph checkpointer on the default SQLite path."""

from __future__ import annotations

from app.database import is_sqlite_url, normalize_database_url, redact_database_url, reset_engine
from app.services.checkpointer import (
    assert_thread_belongs_to_hotel,
    checkpointer_meta,
    graph_thread_id,
    reset_checkpointer,
)
from app.services.workflow import engine_info, load_graph_state, reset_langgraph


def _headers(client, username="admin", password="admin123"):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}, resp.json()


def test_default_url_helpers_do_not_leak_passwords():
    raw = "postgresql+psycopg://oiro:super-secret@127.0.0.1:55432/oiro_test?sslmode=require"
    hidden = redact_database_url(raw)
    assert "super-secret" not in hidden
    assert "oiro:***@" in hidden
    assert is_sqlite_url(normalize_database_url("sqlite:////tmp/demo.db"))
    assert not is_sqlite_url(normalize_database_url("postgresql://u:p@localhost/db"))
    assert normalize_database_url("postgres://u:p@localhost/db").startswith("postgresql+psycopg://")


def test_sqlite_checkpointer_survives_new_graph_instance(client):
    headers, login = _headers(client)
    me = client.get("/api/auth/me", headers=headers).json()
    hotel_id = me["hotel_id"]
    info = engine_info()
    assert info["engine"] == "langgraph"
    assert info["checkpointer"] == "sqlite"
    assert info["checkpointer_durable"] is True
    assert is_sqlite_url(__import__("app.config", fromlist=["get_settings"]).get_settings().database_url)

    gen = client.post("/api/strategies/generate", headers=headers, json={"kind": "pricing"})
    assert gen.status_code == 200, gen.text
    strategies = gen.json()["strategies"]
    pending = [s for s in strategies if s["status"] == "pending_review"]
    assert pending
    run_id = pending[0]["workflow_run_id"]
    assert run_id

    snap = load_graph_state(hotel_id, run_id)
    assert snap is not None
    assert (snap.values or {}).get("run_id") == run_id
    assert (snap.values or {}).get("hotel_id") == hotel_id

    # New graph / engine instance, same sqlite checkpoint file.
    reset_langgraph()
    reset_checkpointer()
    info2 = engine_info()
    assert info2["checkpointer_durable"] is True
    snap2 = load_graph_state(hotel_id, run_id)
    assert snap2 is not None
    assert (snap2.values or {}).get("run_id") == run_id
    assert (snap2.values or {}).get("hotel_id") == hotel_id

    # Approve still works after the graph was rebuilt, and rollback binds checkpoint_id.
    approved = client.post(
        f"/api/strategies/{pending[0]['id']}/review",
        headers=headers,
        json={"decision": "approve", "comment": "checkpointer 重建后继续人审"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] in {"executed", "execution_failed", "execution_partial"}
    checkpoint_id = approved.json().get("checkpoint_id")
    assert checkpoint_id
    rolled = client.post(
        f"/api/strategies/{pending[0]['id']}/rollback",
        headers=headers,
        json={"reason": "验证检查点仍绑定 checkpoint_id"},
    )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["status"] == "rolled_back"
    assert rolled.json()["checkpoint_id"]


def test_cross_hotel_thread_cannot_be_resumed(client):
    headers_a, login_a = _headers(client, "admin", "admin123")
    headers_b, login_b = _headers(client, "admin_b", "admin123")
    hotel_a = login_a["hotel_id"]
    hotel_b = login_b["hotel_id"]
    assert hotel_a != hotel_b

    gen = client.post("/api/strategies/generate", headers=headers_a, json={"kind": "pricing"})
    assert gen.status_code == 200, gen.text
    pending = [s for s in gen.json()["strategies"] if s.get("workflow_run_id")]
    assert pending
    run_id = pending[0]["workflow_run_id"]

    assert load_graph_state(hotel_a, run_id) is not None
    assert load_graph_state(hotel_b, run_id) is None
    with __import__("pytest").raises(PermissionError):
        assert_thread_belongs_to_hotel(graph_thread_id(hotel_a, run_id), hotel_b)
