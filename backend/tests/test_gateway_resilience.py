"""Queue, timeout and circuit-breaker tests for the model gateway.

These tests never call a real vendor and must not print or assert raw key values.
"""

from __future__ import annotations

import threading
import time

from app.gateway.protocol import LLMRequest, LLMResponse
from app.gateway.router import ModelGateway


def _settings(**overrides):
    from app.config import Settings

    values = {
        "deepseek_api_key": "",
        "qwen_api_key": "",
        "openai_api_key": "",
        "gemini_api_key": "",
        "grok_api_key": "",
        "self_hosted_api_key": "",
        "self_hosted_base_url": "",
        "self_hosted_model": "",
        "overseas_enabled": False,
        "jwt_secret": "test-secret-oiro-mvp-please-use-32b",
        "llm_timeout_seconds": 1.0,
        "llm_rate_limit_per_minute": 30,
        "llm_vendor_concurrency": 2,
        "llm_global_concurrency": 4,
        "llm_queue_wait_seconds": 1.0,
        "llm_circuit_failure_threshold": 3,
        "llm_circuit_cooldown_seconds": 0.4,
    }
    values.update(overrides)
    return Settings(**values)


def _req(task="analysis"):
    return LLMRequest(task_type=task, messages=[{"role": "user", "content": "x"}])


def _ok(vendor: str, content: str = "ok") -> LLMResponse:
    return LLMResponse(vendor=vendor, model=f"{vendor}-test", content=content, status="ok")


def _install(gw: ModelGateway, vendor: str, fn, *, available: bool = True) -> None:
    gw.adapters[vendor].available = lambda: available  # type: ignore[method-assign]
    gw.adapters[vendor].complete = fn  # type: ignore[method-assign]


def test_timeout_fails_vendor_and_falls_through_to_mock():
    gw = ModelGateway(_settings(llm_timeout_seconds=0.2, llm_circuit_failure_threshold=5))
    calls = {"deepseek": 0}

    def slow(request):
        calls["deepseek"] += 1
        time.sleep(1.0)
        return _ok("deepseek")

    _install(gw, "deepseek", slow, available=True)
    started = time.monotonic()
    resp = gw.complete(_req("analysis"))
    elapsed = time.monotonic() - started
    assert resp.vendor == "mock"
    assert resp.degraded is True
    assert resp.status == "ok"
    assert "timeout" in (resp.error_message or "")
    assert elapsed < 0.9
    assert calls["deepseek"] == 1


def test_errors_trip_breaker_and_skip_vendor_on_later_calls():
    gw = ModelGateway(_settings(llm_circuit_failure_threshold=2, llm_circuit_cooldown_seconds=5))
    hits = {"n": 0}

    def boom(request):
        hits["n"] += 1
        raise RuntimeError("vendor_down")

    _install(gw, "deepseek", boom, available=True)
    first = gw.complete(_req("pricing_strategy"))
    second = gw.complete(_req("pricing_strategy"))
    third = gw.complete(_req("pricing_strategy"))
    assert first.vendor == "mock" and second.vendor == "mock" and third.vendor == "mock"
    assert hits["n"] == 2  # third call skipped after the breaker opened
    states = {item["vendor"]: item["state"] for item in gw.resilience.circuit_snapshot()}
    assert states.get("deepseek") == "open"
    # Fallback notices must not include exception text that could carry secrets or URLs.
    assert "vendor_down" not in (first.error_message or "")
    assert "RuntimeError" in (first.error_message or "") or "deepseek:RuntimeError" in (first.error_message or "")


def test_timeout_trips_breaker_then_uses_backup_or_mock():
    gw = ModelGateway(
        _settings(
            llm_timeout_seconds=0.15,
            llm_circuit_failure_threshold=1,
            llm_circuit_cooldown_seconds=5,
            qwen_api_key="not-a-real-secret",
        )
    )
    qwen_hits = {"n": 0}

    def slow(request):
        time.sleep(0.8)
        return _ok("deepseek")

    def qwen_ok(request):
        qwen_hits["n"] += 1
        return _ok("qwen", "backup")

    _install(gw, "deepseek", slow, available=True)
    _install(gw, "qwen", qwen_ok, available=True)
    first = gw.complete(_req("assistant_chat"))
    assert first.vendor == "qwen"
    assert first.degraded is True
    second = gw.complete(_req("assistant_chat"))
    assert second.vendor == "qwen"
    # DeepSeek was open; Qwen served both after the first timeout.
    assert qwen_hits["n"] == 2


def test_half_open_probe_after_cooldown_then_closes_on_success():
    gw = ModelGateway(
        _settings(
            llm_circuit_failure_threshold=1,
            llm_circuit_cooldown_seconds=0.25,
            llm_timeout_seconds=1.0,
        )
    )
    lock = {"n": 0}

    def flaky(request):
        lock["n"] += 1
        if lock["n"] == 1:
            raise RuntimeError("blip")
        return _ok("deepseek", "recovered")

    _install(gw, "deepseek", flaky, available=True)
    first = gw.complete(_req("analysis"))
    assert first.vendor == "mock"
    # Still in cooldown: skip DeepSeek
    skipped = gw.complete(_req("analysis"))
    assert skipped.vendor == "mock"
    assert lock["n"] == 1
    time.sleep(0.3)
    recovered = gw.complete(_req("analysis"))
    assert recovered.vendor == "deepseek"
    assert recovered.content == "recovered"
    states = {item["vendor"]: item["state"] for item in gw.resilience.circuit_snapshot()}
    assert states.get("deepseek") == "closed"


def test_complete_never_raises_when_vendor_is_down():
    gw = ModelGateway(_settings(llm_circuit_failure_threshold=1))

    def boom(request):
        raise RuntimeError("outage")

    _install(gw, "deepseek", boom, available=True)
    _install(gw, "qwen", boom, available=True)
    resp = gw.complete(_req("pricing_strategy"))
    assert resp.vendor == "mock"
    assert resp.degraded is True
    assert resp.status == "ok"
    assert "outage" not in (resp.error_message or "")
    assert "outage" not in (resp.degrade_notice or "")


def test_queue_concurrency_does_not_deadlock():
    gw = ModelGateway(
        _settings(
            llm_vendor_concurrency=2,
            llm_global_concurrency=2,
            llm_queue_wait_seconds=3.0,
            llm_timeout_seconds=2.0,
            llm_circuit_failure_threshold=20,
        )
    )

    def delayed(request):
        time.sleep(0.05)
        return _ok("deepseek")

    _install(gw, "deepseek", delayed, available=True)
    results: list[LLMResponse] = []
    errors: list[str] = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(gw.complete(_req("analysis")))
        except Exception as exc:  # noqa: BLE001
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert all(not t.is_alive() for t in threads)
    assert errors == []
    assert len(results) == 8
    assert all(r.status == "ok" for r in results)
    # Some may have waited in the queue and then used DeepSeek or fallen through
    # to Mock after a queue timeout; either is acceptable as long as we finish.
    assert {r.vendor for r in results} <= {"deepseek", "qwen", "self_hosted", "mock"}


def test_rate_limit_is_configurable_and_falls_through():
    gw = ModelGateway(_settings(llm_rate_limit_per_minute=1, llm_circuit_failure_threshold=10))
    hits = {"n": 0}

    def ok(request):
        hits["n"] += 1
        return _ok("deepseek")

    _install(gw, "deepseek", ok, available=True)
    first = gw.complete(_req("analysis"))
    second = gw.complete(_req("analysis"))
    assert first.vendor == "deepseek"
    assert second.vendor != "deepseek"
    assert second.degraded is True
    assert hits["n"] == 1
