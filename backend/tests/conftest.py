from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    dbfile = tmp_path / "oiro.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{dbfile}")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("QWEN_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GROK_API_KEY", "")
    monkeypatch.setenv("SELF_HOSTED_API_KEY", "")
    monkeypatch.setenv("SELF_HOSTED_BASE_URL", "")
    monkeypatch.setenv("SELF_HOSTED_MODEL", "")
    monkeypatch.setenv("OVERSEAS_ENABLED", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-oiro-mvp-please-use-32b")

    from app.config import clear_settings_cache
    from app.database import reset_engine
    from app.gateway.router import reset_gateway

    clear_settings_cache()
    reset_engine()
    reset_gateway()

    from app.main import app

    with TestClient(app) as c:
        yield c

    reset_engine()
    reset_gateway()
    clear_settings_cache()


@pytest.fixture
def auth_header(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
