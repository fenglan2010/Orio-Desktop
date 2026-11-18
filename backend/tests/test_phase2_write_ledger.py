from __future__ import annotations

from app.adapters.ota.official_write import OfficialWriteAdapter
from app.database import SessionLocal
from app.models import Strategy, WriteJob
from app.services.risk_policy import save_execution_policy, validate_execution_policy, load_execution_policy
from app.services.writes import attempt_write, is_retryable, reconcile_job, retry_write_job


def _login(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _pending_pricing(client, headers):
    gen = client.post("/api/strategies/generate", headers=headers, json={"kind": "pricing"})
    assert gen.status_code == 200, gen.text
    rows = [s for s in gen.json()["strategies"] if s["kind"] == "pricing" and s["status"] == "pending_review"]
    assert rows
    return rows[0]


def test_sandbox_success_creates_ledger_row(client, auth_header):
    strategy = _pending_pricing(client, auth_header)
    approved = client.post(
        f"/api/strategies/{strategy['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "写入账本验收"},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "executed"
    assert body["execution_payload"]["mode"] == "sandbox_official"
    assert body["execution_payload"]["accepted"] is True
    assert body["execution_payload"]["receipt_id"]
    assert body["execution_payload"]["request_id"]
    assert body["execution_payload"]["items"]

    listed = client.get("/api/writes", headers=auth_header)
    assert listed.status_code == 200, listed.text
    jobs = listed.json()["writes"]
    match = [j for j in jobs if j["strategy_id"] == strategy["id"]]
    assert match
    job = match[0]
    assert job["status"] in {"accepted", "reconciled"}
    assert job["write_mode"] == "sandbox"
    assert job["receipt_mode"] == "sandbox_official"
    assert job["attempt_count"] == 1
    assert job["checkpoint_id"]
    assert job["items"]
    assert any(i.get("date") or i.get("intended_price") is not None for i in job["items"])

    detail = client.get(f"/api/writes/{job['id']}", headers=auth_header)
    assert detail.status_code == 200
    assert detail.json()["receipt_id"] == job["receipt_id"]


def test_timeout_can_be_retried_then_succeeds(client, auth_header):
    strategy = _pending_pricing(client, auth_header)
    db = SessionLocal()
    try:
        row = db.get(Strategy, strategy["id"])
        from app.models import WorkflowRun

        run = db.get(WorkflowRun, row.workflow_run_id)
        payload = dict(run.payload or {})
        payload["_sandbox_simulate"] = "timeout"
        run.payload = payload
        db.commit()
    finally:
        db.close()

    approved = client.post(
        f"/api/strategies/{strategy['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "模拟超时"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "execution_failed"

    jobs = client.get(f"/api/writes?strategy_id={strategy['id']}", headers=auth_header).json()["writes"]
    assert jobs
    job = jobs[0]
    assert job["status"] == "timeout"
    assert job["retryable"] is True
    assert job["attempt_count"] == 1
    assert not job["receipt_id"]

    alerts = client.get("/api/alerts", headers=auth_header).json()["alerts"]
    write_alerts = [a for a in alerts if a["alert_type"] == "write_failed" and a["status"] == "open"]
    assert write_alerts

    # Next retry should succeed once the simulate flag is cleared.
    db = SessionLocal()
    try:
        row = db.get(WriteJob, job["id"])
        payload = dict(row.intended_payload or {})
        payload.pop("_sandbox_simulate", None)
        payload.pop("sandbox_simulate", None)
        row.intended_payload = payload
        db.commit()
    finally:
        db.close()

    retried = client.post(f"/api/writes/{job['id']}/retry", headers=auth_header)
    assert retried.status_code == 200, retried.text
    body = retried.json()
    assert body["status"] in {"accepted", "reconciled"}
    assert body["attempt_count"] == 2
    assert body["receipt"]["accepted"] is True
    fresh = client.get(f"/api/strategies/{strategy['id']}", headers=auth_header).json()
    assert fresh["status"] == "executed"


def test_after_max_retries_stays_failed(client, auth_header):
    strategy = _pending_pricing(client, auth_header)
    db = SessionLocal()
    try:
        from app.models import WorkflowRun

        row = db.get(Strategy, strategy["id"])
        run = db.get(WorkflowRun, row.workflow_run_id)
        payload = dict(run.payload or {})
        payload["_sandbox_simulate"] = "failed"
        run.payload = payload
        db.commit()
    finally:
        db.close()

    approved = client.post(
        f"/api/strategies/{strategy['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "模拟失败"},
    )
    assert approved.status_code == 200
    job = client.get(f"/api/writes?strategy_id={strategy['id']}", headers=auth_header).json()["writes"][0]
    assert job["status"] == "failed"
    assert job["attempt_count"] == 1

    second = client.post(f"/api/writes/{job['id']}/retry", headers=auth_header)
    assert second.status_code == 200, second.text
    assert second.json()["attempt_count"] == 2
    assert second.json()["status"] == "failed"

    third = client.post(f"/api/writes/{job['id']}/retry", headers=auth_header)
    assert third.status_code == 200, third.text
    assert third.json()["attempt_count"] == 3
    assert third.json()["status"] == "failed"
    assert third.json()["retryable"] is False

    fourth = client.post(f"/api/writes/{job['id']}/retry", headers=auth_header)
    assert fourth.status_code == 400
    assert "最大重试" in fourth.json()["detail"]
    stuck = client.get(f"/api/writes/{job['id']}", headers=auth_header).json()
    assert stuck["status"] == "failed"
    assert stuck["attempt_count"] == 3


def test_partial_success_is_visible_and_reconcilable(client, auth_header):
    strategy = _pending_pricing(client, auth_header)
    db = SessionLocal()
    try:
        from app.models import WorkflowRun

        row = db.get(Strategy, strategy["id"])
        run = db.get(WorkflowRun, row.workflow_run_id)
        payload = dict(run.payload or {})
        payload["_sandbox_simulate"] = "partial"
        run.payload = payload
        db.commit()
    finally:
        db.close()

    approved = client.post(
        f"/api/strategies/{strategy['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "模拟部分成功"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "execution_partial"
    job = client.get(f"/api/writes?strategy_id={strategy['id']}", headers=auth_header).json()["writes"][0]
    assert job["status"] == "partial"
    items = job["items"]
    assert any(i.get("accepted") for i in items)
    assert any(not i.get("accepted") for i in items)
    assert job["retryable"] is True

    rec = client.post(f"/api/writes/{job['id']}/reconcile", headers=auth_header)
    assert rec.status_code == 200, rec.text
    result = rec.json()["reconcile_result"] or rec.json().get("reconcile")
    assert result
    assert result["ok"] is False
    codes = {i["code"] for i in result["issues"]}
    assert "partial_leftover" in codes


def test_reconcile_mismatch_is_detected(client, auth_header):
    strategy = _pending_pricing(client, auth_header)
    approved = client.post(
        f"/api/strategies/{strategy['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "制造对账不一致"},
    )
    assert approved.status_code == 200
    job = client.get(f"/api/writes?strategy_id={strategy['id']}", headers=auth_header).json()["writes"][0]
    db = SessionLocal()
    try:
        row = db.get(Strategy, strategy["id"])
        row.status = "pending_review"
        db.commit()
        job_row = db.get(WriteJob, job["id"])
        if job_row.status == "reconciled":
            job_row.status = "accepted"
            db.commit()
        result = reconcile_job(db, job_row, actor="admin")
        assert result["ok"] is False
        assert any(i["code"] == "accepted_but_state_not_executed" for i in result["issues"])
        job_id = job_row.id
    finally:
        db.close()

    alerts = client.get("/api/alerts", headers=auth_header).json()["alerts"]
    mismatches = [a for a in alerts if a["alert_type"] == "reconcile_mismatch" and a["status"] == "open"]
    assert mismatches
    diag = client.post(f"/api/alerts/{mismatches[0]['id']}/diagnose", headers=auth_header)
    assert diag.status_code == 200, diag.text
    narrative = (diag.json().get("diagnosis") or {}).get("narrative") or ""
    assert "对账" in narrative
    assert "转化走弱" not in narrative

    sweep = client.post("/api/writes/reconcile", headers=auth_header)
    assert sweep.status_code == 200
    assert sweep.json()["checked"] >= 1


def test_auto_on_write_failure_rolls_back(client, auth_header):
    updated = client.put(
        "/api/settings",
        headers=auth_header,
        json={"execution_policy": {"rollback_policy": "auto_on_write_failure", "auto_execute_enabled": False}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["execution_policy"]["rollback_policy"] == "auto_on_write_failure"
    assert updated.json()["execution_policy"]["auto_execute_enabled"] is False

    strategy = _pending_pricing(client, auth_header)
    db = SessionLocal()
    try:
        from app.models import WorkflowRun

        row = db.get(Strategy, strategy["id"])
        run = db.get(WorkflowRun, row.workflow_run_id)
        payload = dict(run.payload or {})
        payload["_sandbox_simulate"] = "timeout"
        run.payload = payload
        db.commit()
    finally:
        db.close()

    approved = client.post(
        f"/api/strategies/{strategy['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "失败后应自动回滚"},
    )
    assert approved.status_code == 200, approved.text
    # First timeout is still retryable, so no auto rollback yet.
    assert approved.json()["status"] == "execution_failed"
    job = client.get(f"/api/writes?strategy_id={strategy['id']}", headers=auth_header).json()["writes"][0]
    client.post(f"/api/writes/{job['id']}/retry", headers=auth_header)
    last = client.post(f"/api/writes/{job['id']}/retry", headers=auth_header)
    assert last.status_code == 200, last.text
    assert last.json()["attempt_count"] == 3
    assert last.json()["status"] == "rolled_back"
    fresh = client.get(f"/api/strategies/{strategy['id']}", headers=auth_header).json()
    assert fresh["status"] == "rolled_back"
    assert any(p["node"] == "rollback" for p in fresh["path"])


def test_live_without_credentials_is_refused_and_not_retried(client, auth_header, monkeypatch):
    monkeypatch.delenv("CTRIP_WRITE_ENABLED", raising=False)
    monkeypatch.delenv("CTRIP_WRITE_KEY", raising=False)
    monkeypatch.delenv("CTRIP_API_KEY", raising=False)
    from app.config import clear_settings_cache

    clear_settings_cache()
    receipt = OfficialWriteAdapter().apply_price({"recommended_price": 688, "checkpoint_id": "ckpt-live"}, write_mode="live")
    assert receipt["accepted"] is False
    assert receipt["outcome"] == "refused"
    assert receipt["refuse_reason"] == "missing_credentials"

    updated = client.put(
        "/api/settings",
        headers=auth_header,
        json={"execution_policy": {"write_mode": "live", "auto_execute_enabled": False, "rollback_policy": "manual"}},
    )
    assert updated.status_code == 200, updated.text
    strategy = _pending_pricing(client, auth_header)
    approved = client.post(
        f"/api/strategies/{strategy['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "live 无凭证"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "execution_failed"
    job = client.get(f"/api/writes?strategy_id={strategy['id']}", headers=auth_header).json()["writes"][0]
    assert job["status"] == "failed"
    assert job["refuse_reason"] == "missing_credentials"
    assert job["retryable"] is False
    retried = client.post(f"/api/writes/{job['id']}/retry", headers=auth_header)
    assert retried.status_code == 400
    assert "凭证" in retried.json()["detail"] or "不能当作瞬时" in retried.json()["detail"]

    # Restore sandbox so later tests in this process (if any) stay default-like.
    client.put(
        "/api/settings",
        headers=auth_header,
        json={"execution_policy": {"write_mode": "sandbox", "rollback_policy": "manual"}},
    )


def test_official_write_makes_no_outbound_http(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("unexpected outbound HTTP")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    try:
        import httpx

        monkeypatch.setattr(httpx, "request", boom, raising=False)
        monkeypatch.setattr(httpx, "get", boom, raising=False)
        monkeypatch.setattr(httpx, "post", boom, raising=False)
    except ImportError:
        pass
    try:
        import requests

        monkeypatch.setattr(requests, "request", boom, raising=False)
        monkeypatch.setattr(requests, "get", boom, raising=False)
        monkeypatch.setattr(requests, "post", boom, raising=False)
    except ImportError:
        pass

    adapter = OfficialWriteAdapter()
    ok = adapter.apply_price({"recommended_price": 500, "daily_breakdown": [{"date": "2026-11-16", "recommended": 500}]}, write_mode="sandbox")
    assert ok["accepted"] is True
    refused = adapter.apply_campaign({"campaign": {"campaign_name": "演示活动"}}, write_mode="live")
    assert refused["accepted"] is False
    timed = adapter.apply_price({"recommended_price": 500, "_sandbox_simulate": "timeout"}, write_mode="sandbox")
    assert timed["outcome"] == "timeout"
