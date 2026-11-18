"""PostgreSQL coverage for Phase III increment 2. Skips when no test server can start."""

from __future__ import annotations

import os
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine.url import make_url

from app.config import clear_settings_cache
from app.database import normalize_database_url, psycopg_conninfo, reset_engine
from app.gateway.router import reset_gateway
from app.services.checkpointer import reset_checkpointer
from app.services.workflow import engine_info, load_graph_state, reset_langgraph


def _available_url() -> str | None:
    env = (os.environ.get("OIRO_TEST_DATABASE_URL") or "").strip()
    if env:
        return env
    try:
        from tests.pg_harness import start

        return start()
    except Exception:
        return None


PG_URL = _available_url()
pytestmark = pytest.mark.skipif(not PG_URL, reason="测试 PostgreSQL 不可用（启动 scripts/run_test_postgres.sh 或设置 OIRO_TEST_DATABASE_URL）")


def _fresh_url(base: str) -> str:
    import psycopg

    name = "oiro_t_" + secrets.token_hex(4)
    conninfo = psycopg_conninfo(base)
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = make_url(normalize_database_url(base)).set(database=name)
    return url.render_as_string(hide_password=False)


@pytest.fixture
def pg_client(monkeypatch, tmp_path):
    assert PG_URL
    url = _fresh_url(PG_URL)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("QWEN_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GROK_API_KEY", "")
    monkeypatch.setenv("OVERSEAS_ENABLED", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-oiro-mvp-please-use-32b")
    clear_settings_cache()
    reset_langgraph()
    reset_checkpointer()
    reset_engine()
    reset_gateway()
    from app.main import app

    with TestClient(app) as client:
        yield client

    reset_langgraph()
    reset_checkpointer()
    reset_engine()
    reset_gateway()
    clear_settings_cache()


def _login(client, username, password="admin123"):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, resp.json()


def test_postgres_schema_seed_and_tenancy(pg_client):
    info = engine_info()
    assert info["engine"] == "langgraph"
    assert info["checkpointer"] == "postgres"
    assert info["checkpointer_durable"] is True

    headers_a, login_a = _login(pg_client, "admin")
    headers_b, login_b = _login(pg_client, "admin_b")
    assert login_a["hotel_name"] == "杭州西湖精品酒店（演示）"
    assert login_b["hotel_name"] == "成都春熙商务酒店（演示）"
    assert login_a["hotel_id"] != login_b["hotel_id"]

    rooms_a = {r["name"] for r in pg_client.get("/api/dashboard/rooms", headers=headers_a).json()["rooms"]}
    rooms_b = {r["name"] for r in pg_client.get("/api/dashboard/rooms", headers=headers_b).json()["rooms"]}
    assert "豪华大床房" in rooms_a
    assert "高级大床房" in rooms_b
    assert "豪华大床房" not in rooms_b

    alerts_b = pg_client.get("/api/alerts", headers=headers_b).json()["alerts"]
    assert alerts_b
    leaked = pg_client.get(f"/api/alerts/{alerts_b[0]['id']}", headers=headers_a)
    assert leaked.status_code == 404

    users_a = {u["username"] for u in pg_client.get("/api/users", headers=headers_a).json()["users"]}
    users_b = {u["username"] for u in pg_client.get("/api/users", headers=headers_b).json()["users"]}
    assert "admin_b" not in users_a
    assert users_b == {"admin_b"}

    policy_a = pg_client.get("/api/settings", headers=headers_a).json()["execution_policy"]
    policy_b = pg_client.get("/api/settings", headers=headers_b).json()["execution_policy"]
    assert policy_a["auto_execute_enabled"] is False
    assert policy_b["write_mode"] == "sandbox"

    changed = pg_client.put(
        "/api/settings",
        headers=headers_b,
        json={"execution_policy": {"auto_execute_enabled": True, "write_mode": "live"}},
    )
    assert changed.status_code == 200
    assert pg_client.get("/api/settings", headers=headers_a).json()["execution_policy"]["auto_execute_enabled"] is False
    reset = pg_client.put(
        "/api/settings",
        headers=headers_b,
        json={"execution_policy": {"auto_execute_enabled": False, "write_mode": "sandbox"}},
    )
    assert reset.json()["execution_policy"]["auto_execute_enabled"] is False


def test_postgres_checkpointer_survives_new_graph_and_isolates_hotels(pg_client):
    headers_a, login_a = _login(pg_client, "admin")
    headers_b, login_b = _login(pg_client, "admin_b")
    gen = pg_client.post("/api/strategies/generate", headers=headers_a, json={"kind": "pricing"})
    assert gen.status_code == 200, gen.text
    pending = [s for s in gen.json()["strategies"] if s.get("workflow_run_id") and s["status"] == "pending_review"]
    assert pending
    run_id = pending[0]["workflow_run_id"]
    hotel_a = login_a["hotel_id"]
    hotel_b = login_b["hotel_id"]

    snap = load_graph_state(hotel_a, run_id)
    assert snap is not None
    reset_langgraph()
    reset_checkpointer()
    assert engine_info()["checkpointer"] == "postgres"
    snap2 = load_graph_state(hotel_a, run_id)
    assert snap2 is not None
    assert (snap2.values or {}).get("run_id") == run_id
    assert load_graph_state(hotel_b, run_id) is None
