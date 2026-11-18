"""Phase III increment 3: scheduled ingest, write retry, and reconcile jobs."""

from __future__ import annotations

from datetime import date, timedelta

from app.database import SessionLocal, redact_url
from app.models import DailyMetric, Hotel, JobRun, Strategy, WriteJob
from app.services.official_contracts import install_official_contract
from app.services.risk_policy import load_execution_policy
from app.services.writes import create_write_job, is_retryable


def _login(client, username="admin", password="admin123"):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _hotels(db):
    hangzhou = db.query(Hotel).filter(Hotel.tenant_key == "demo").one()
    chengdu = db.query(Hotel).filter(Hotel.tenant_key == "demo_chengdu").one()
    return hangzhou, chengdu


def _metric_fingerprint(db, hotel_id: int):
    rows = (
        db.query(DailyMetric)
        .filter(DailyMetric.hotel_id == hotel_id)
        .order_by(DailyMetric.id)
        .all()
    )
    return [
        (
            row.id,
            row.date,
            row.room_type_id,
            row.occupancy,
            row.list_price,
            row.adr,
            row.revenue,
            row.search_rank,
            row.impressions,
            row.clicks,
            row.bookings,
            row.sold_rooms,
        )
        for row in rows
    ]


def _strategy_fingerprint(db, hotel_id: int):
    rows = db.query(Strategy).filter(Strategy.hotel_id == hotel_id).order_by(Strategy.id).all()
    return [(row.id, row.status) for row in rows]


def _read_contract(base_url: str, platform: str = "ctrip") -> dict:
    return {
        "platform": platform,
        "version": 1,
        "base_url": base_url,
        "auth": "header_api_key",
        "auth_header": "X-Api-Key",
        "timeout_seconds": 2,
        "actions": {
            "fetch_metrics": {
                "method": "GET",
                "path": "/hotel/metrics",
                "kind": "ingest",
                "request_template": {
                    "hotel_id": "{{platform_hotel_id}}",
                    "date_from": "{{date_from}}",
                    "date_to": "{{date_to}}",
                },
                "response_mapping": {"items_path": "items", "message_path": "message"},
            }
        },
    }


def _write_only_contract(base_url: str, platform: str = "ctrip") -> dict:
    return {
        "platform": platform,
        "version": 1,
        "base_url": base_url,
        "auth": "header_api_key",
        "auth_header": "X-Api-Key",
        "timeout_seconds": 2,
        "actions": {
            "apply_price": {
                "method": "POST",
                "path": "/hotel/prices",
                "request_template": {"hotel_id": "{{platform_hotel_id}}", "items": "{{items}}"},
            }
        },
    }


def test_ingest_is_isolated_and_does_not_mutate_demo_story(client, auth_header, monkeypatch):
    from app.adapters.ota import official_http
    from app.jobs.tasks import ingest_all_hotels, ingest_hotel

    calls: list[tuple] = []

    def _forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("ingest must not open official HTTP when no read contract exists")

    monkeypatch.setattr(official_http, "perform_http_request", _forbidden)

    db = SessionLocal()
    try:
        hangzhou, chengdu = _hotels(db)
        before_h = _metric_fingerprint(db, hangzhou.id)
        before_c = _metric_fingerprint(db, chengdu.id)
        before_sh = _strategy_fingerprint(db, hangzhou.id)
        before_sc = _strategy_fingerprint(db, chengdu.id)
        assert before_h and before_c

        result_h = ingest_hotel(db, hangzhou.id, actor="system:scheduler")
        db.expire_all()
        assert result_h["hotel_id"] == hangzhou.id
        assert result_h["status"] == "succeeded"
        assert result_h["result"]["source"] == "mock"
        assert result_h["result"]["official_http"] is False
        assert result_h["result"]["reason"] == "no_official_read_action"
        assert result_h["result"]["mutated"] is False
        assert result_h["result"]["strategy_executed"] is False
        assert result_h["result"]["auto_approved"] is False
        assert result_h["result"]["counts"]["metrics"] > 0
        assert result_h["result"]["last_ingested_at"]

        assert _metric_fingerprint(db, hangzhou.id) == before_h
        assert _metric_fingerprint(db, chengdu.id) == before_c
        assert _strategy_fingerprint(db, hangzhou.id) == before_sh
        assert db.query(JobRun).filter(JobRun.hotel_id == chengdu.id).count() == 0
        assert db.query(JobRun).filter(JobRun.hotel_id == hangzhou.id, JobRun.job_type == "ingest").count() == 1

        result_c = ingest_hotel(db, chengdu.id, actor="system:scheduler")
        db.expire_all()
        assert result_c["hotel_id"] == chengdu.id
        assert result_c["result"]["source"] == "mock"
        assert _metric_fingerprint(db, hangzhou.id) == before_h
        assert _metric_fingerprint(db, chengdu.id) == before_c
        assert _strategy_fingerprint(db, chengdu.id) == before_sc

        sweep = ingest_all_hotels(db, actor="system:scheduler")
        assert sweep["hotels"] == 2
        hangzhou_runs = db.query(JobRun).filter(JobRun.hotel_id == hangzhou.id, JobRun.job_type == "ingest").all()
        chengdu_runs = db.query(JobRun).filter(JobRun.hotel_id == chengdu.id, JobRun.job_type == "ingest").all()
        assert len(hangzhou_runs) == 2
        assert len(chengdu_runs) == 2
        assert all(run.hotel_id == hangzhou.id for run in hangzhou_runs)
        assert not calls
    finally:
        db.close()


def test_jobs_api_is_hotel_scoped(client, auth_header):
    from app.jobs.tasks import ingest_hotel

    db = SessionLocal()
    try:
        hangzhou, chengdu = _hotels(db)
        ingest_hotel(db, hangzhou.id, actor="system:scheduler")
        ingest_hotel(db, chengdu.id, actor="system:scheduler")
    finally:
        db.close()

    listed = client.get("/api/jobs", headers=auth_header)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["runs"]
    assert all(row["hotel_id"] == body["runs"][0]["hotel_id"] for row in body["runs"])
    assert body["last_ingest"]["result"]["source"] == "mock"
    assert "redis" not in listed.text.lower()
    assert "celery" not in listed.text.lower()
    assert "broker" not in listed.text.lower()

    other = _login(client, "admin_b", "admin123")
    listed_b = client.get("/api/jobs", headers=other)
    assert listed_b.status_code == 200, listed_b.text
    body_b = listed_b.json()
    ids_a = {row["id"] for row in body["runs"]}
    ids_b = {row["id"] for row in body_b["runs"]}
    assert ids_a and ids_b
    assert ids_a.isdisjoint(ids_b)
    assert all(row["hotel_id"] != body["runs"][0]["hotel_id"] for row in body_b["runs"])


def test_retry_and_reconcile_follow_existing_rules_and_isolation(client, auth_header):
    from app.jobs.tasks import reconcile_hotel_writes, retry_hotel_writes
    from app.jobs.runtime import SYSTEM_ACTOR

    db = SessionLocal()
    try:
        hangzhou, chengdu = _hotels(db)
        today = date.today()
        payload = {
            "kind": "pricing",
            "recommended_price": 488,
            "date_from": today.isoformat(),
            "date_to": (today + timedelta(days=2)).isoformat(),
        }
        retryable = create_write_job(
            db,
            hotel_id=hangzhou.id,
            kind="pricing",
            payload=payload,
            write_mode="sandbox",
            actor="admin",
        )
        retryable.status = "timeout"
        retryable.attempt_count = 1
        retryable.max_attempts = 3
        retryable.refuse_reason = ""
        retryable.last_error = "simulated timeout"

        blocked = create_write_job(
            db,
            hotel_id=hangzhou.id,
            kind="pricing",
            payload=payload,
            write_mode="sandbox",
            actor="admin",
        )
        blocked.status = "failed"
        blocked.attempt_count = 1
        blocked.refuse_reason = "missing_credentials"
        blocked.last_error = "缺少凭证"

        reply = create_write_job(
            db,
            hotel_id=hangzhou.id,
            kind="review_reply",
            payload={"kind": "review_reply", "review_id": 1},
            write_mode="sandbox",
            actor="system:auto_execute",
        )
        reply.status = "failed"
        reply.attempt_count = 1
        reply.refuse_reason = ""

        other = create_write_job(
            db,
            hotel_id=chengdu.id,
            kind="pricing",
            payload=payload,
            write_mode="sandbox",
            actor="admin_b",
        )
        other.status = "timeout"
        other.attempt_count = 1
        other.refuse_reason = ""
        db.commit()

        retryable_id, blocked_id, reply_id, other_id = retryable.id, blocked.id, reply.id, other.id
        assert is_retryable(db.get(WriteJob, retryable_id)) is True
        assert is_retryable(db.get(WriteJob, blocked_id)) is False
        assert is_retryable(db.get(WriteJob, reply_id)) is False
        assert is_retryable(db.get(WriteJob, other_id)) is True

        result = retry_hotel_writes(db, hangzhou.id, actor=SYSTEM_ACTOR)
        assert result["hotel_id"] == hangzhou.id
        assert result["status"] == "succeeded"
        retried_ids = {item["id"] for item in result["result"]["retried"]}
        skipped_ids = {item["id"] for item in result["result"]["skipped"]}
        assert retryable_id in retried_ids
        assert blocked_id in skipped_ids
        assert reply_id in skipped_ids
        assert other_id not in retried_ids
        assert other_id not in skipped_ids

        db.expire_all()
        assert db.get(WriteJob, retryable_id).status in {"accepted", "reconciled"}
        assert db.get(WriteJob, retryable_id).attempt_count == 2
        assert db.get(WriteJob, retryable_id).actor == SYSTEM_ACTOR
        assert db.get(WriteJob, blocked_id).status == "failed"
        assert db.get(WriteJob, blocked_id).attempt_count == 1
        assert db.get(WriteJob, reply_id).status == "failed"
        assert db.get(WriteJob, reply_id).attempt_count == 1
        assert db.get(WriteJob, other_id).status == "timeout"
        assert db.get(WriteJob, other_id).attempt_count == 1

        accepted = db.get(WriteJob, retryable_id)
        if accepted.status == "accepted":
            rec = reconcile_hotel_writes(db, hangzhou.id, actor=SYSTEM_ACTOR)
            assert rec["hotel_id"] == hangzhou.id
            checked_ids = {item["id"] for item in rec["result"]["jobs"]}
            assert retryable_id in checked_ids
            assert other_id not in checked_ids
            db.expire_all()
            # Accepted sandbox jobs may become reconciled when intended vs receipt agree.
            assert db.get(WriteJob, retryable_id).reconciled_at is not None
        assert db.get(WriteJob, other_id).reconciled_at is None
        assert db.query(JobRun).filter(JobRun.hotel_id == chengdu.id, JobRun.job_type == "write_retry").count() == 0
    finally:
        db.close()


def test_tasks_run_synchronously_without_redis(client):
    from app.jobs.celery_app import celery_app, ingest_all_hotels_task
    from app.jobs.runtime import job_queue_mode

    assert job_queue_mode() == "local"
    assert celery_app.conf.task_always_eager is True
    result = ingest_all_hotels_task.apply(kwargs={"actor": "system:scheduler"}).get()
    assert result["hotels"] == 2
    assert all(run["status"] == "succeeded" for run in result["runs"])
    assert all(run["result"]["source"] == "mock" for run in result["runs"])


def test_no_invented_official_http_without_read_contract(client, auth_header, monkeypatch):
    from app.adapters.ota import official_http
    from app.config import clear_settings_cache
    from app.jobs.ingest import official_read_actions, resolve_official_ingest
    from app.jobs.tasks import ingest_hotel

    calls: list[str] = []

    def _capture(method, url, **kwargs):
        calls.append(url)
        raise AssertionError(f"unexpected official HTTP: {url}")

    monkeypatch.setattr(official_http, "perform_http_request", _capture)

    db = SessionLocal()
    try:
        hangzhou, chengdu = _hotels(db)
        install_official_contract(
            db,
            "ctrip",
            _write_only_contract("http://127.0.0.1:9"),
            actor="admin",
            hotel_id=hangzhou.id,
        )
        official = resolve_official_ingest(db, hangzhou, "ctrip")
        assert official["allowed"] is False
        assert official["reason"] == "no_official_read_action"
        assert official_read_actions(official["contract"]) == []

        result = ingest_hotel(db, hangzhou.id, actor="system:scheduler")
        assert result["result"]["source"] == "mock"
        assert result["result"]["official_http"] is False
        assert not calls

        # Read action exists but credentials / authorization are missing: still no socket.
        install_official_contract(
            db,
            "ctrip",
            _read_contract("http://127.0.0.1:9"),
            actor="admin",
            hotel_id=hangzhou.id,
        )
        official = resolve_official_ingest(db, hangzhou, "ctrip")
        assert official["allowed"] is False
        assert official["reason"] == "missing_credentials"
        result = ingest_hotel(db, hangzhou.id, actor="system:scheduler")
        assert result["result"]["source"] == "mock"
        assert result["result"]["reason"] == "missing_credentials"
        assert not calls

        # Chengdu must not inherit Hangzhou's contract and still uses mock ingest.
        official_b = resolve_official_ingest(db, chengdu, "ctrip")
        assert official_b["allowed"] is False
        assert official_b["reason"] == "no_official_read_action"
    finally:
        db.close()
        clear_settings_cache()


def test_official_read_uses_contract_url_only(client, auth_header, monkeypatch):
    import httpx
    from app.adapters.ota import official_http
    from app.config import clear_settings_cache
    from app.jobs.tasks import ingest_hotel

    recorded: list[str] = []

    def _fake_request(method, url, **kwargs):
        recorded.append(url)
        request = httpx.Request(method, url)
        return httpx.Response(200, json={"message": "ok", "items": []}, request=request)

    monkeypatch.setenv("CTRIP_WRITE_KEY", "test-ingest-key")
    monkeypatch.delenv("CTRIP_WRITE_ENABLED", raising=False)
    clear_settings_cache()
    monkeypatch.setattr(official_http, "perform_http_request", _fake_request)

    db = SessionLocal()
    try:
        hangzhou, chengdu = _hotels(db)
        before_h = _metric_fingerprint(db, hangzhou.id)
        before_c = _metric_fingerprint(db, chengdu.id)
        install_official_contract(
            db,
            "ctrip",
            _read_contract("http://127.0.0.1:18765"),
            actor="admin",
            hotel_id=hangzhou.id,
        )
        result = ingest_hotel(db, hangzhou.id, actor="system:scheduler")
        assert result["result"]["source"] == "official"
        assert result["result"]["official_http"] is True
        assert result["result"]["persisted"] is False
        assert result["result"]["mutated"] is False
        assert recorded
        assert all(url.startswith("http://127.0.0.1:18765/hotel/metrics") for url in recorded)
        assert all("ctrip.com" not in url and "meituan.com" not in url and "taobao.com" not in url for url in recorded)
        db.expire_all()
        assert _metric_fingerprint(db, hangzhou.id) == before_h
        assert _metric_fingerprint(db, chengdu.id) == before_c
    finally:
        db.close()
        monkeypatch.delenv("CTRIP_WRITE_KEY", raising=False)
        clear_settings_cache()


def test_demo_defaults_and_health_job_queue(client, auth_header):
    from app.jobs.runtime import SYSTEM_ACTOR, job_queue_mode

    db = SessionLocal()
    try:
        hangzhou, chengdu = _hotels(db)
        for hotel in (hangzhou, chengdu):
            policy = load_execution_policy(db, hotel_id=hotel.id)
            assert policy.auto_execute_enabled is False
            assert policy.write_mode == "sandbox"
    finally:
        db.close()

    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["job_queue"] == "local"
    assert job_queue_mode() == "local"
    blob = health.text.lower()
    assert "redis://" not in blob
    assert "amqp://" not in blob
    assert "password" not in blob
    assert redact_url("redis://:super-secret@127.0.0.1:6379/0") == "redis://:***@127.0.0.1:6379/0"

    settings = client.get("/api/settings", headers=auth_header)
    assert settings.status_code == 200
    text = settings.text.lower()
    assert "celery" not in text
    assert "redis" not in text
    assert "broker" not in text
    assert "CELERY_BROKER_URL" not in settings.text
    assert SYSTEM_ACTOR == "system:scheduler"

    # Manual retry/reconcile HTTP APIs remain available.
    listed = client.get("/api/writes", headers=auth_header)
    assert listed.status_code == 200
