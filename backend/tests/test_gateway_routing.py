from __future__ import annotations

import pytest

from app.config import OVERSEAS_VENDORS
from app.gateway.protocol import LLMRequest
from app.gateway.router import ModelGateway


def _gw():
    from app.config import get_settings

    return ModelGateway(get_settings())


@pytest.mark.parametrize("task", ["pricing_strategy", "review_reply"])
def test_critical_tasks_never_include_overseas_vendors_even_if_enabled(task):
    gw = _gw()
    req = LLMRequest(task_type=task, messages=[{"role": "user", "content": "x"}])
    chain = gw.route_chain(req, overseas_enabled=True)
    assert "mock" in chain
    assert "deepseek" in chain
    for vendor in OVERSEAS_VENDORS:
        assert vendor not in chain


def test_execution_critical_flag_also_blocks_overseas():
    gw = _gw()
    req = LLMRequest(task_type="assistant_chat", messages=[], execution_critical=True)
    chain = gw.route_chain(req, overseas_enabled=True)
    for vendor in OVERSEAS_VENDORS:
        assert vendor not in chain


def test_non_critical_may_include_overseas_when_enabled():
    gw = _gw()
    req = LLMRequest(task_type="assistant_chat", messages=[])
    off = gw.route_chain(req, overseas_enabled=False)
    on = gw.route_chain(req, overseas_enabled=True)
    assert not any(v in off for v in OVERSEAS_VENDORS)
    assert any(v in on for v in OVERSEAS_VENDORS)


def test_complete_critical_not_outbound_when_overseas_on_and_keys_present(client, auth_header, monkeypatch):
    # Turn the switch on and pretend overseas keys exist, then spy on those adapters.
    from app.gateway.adapters.gemini import GeminiAdapter
    from app.gateway.adapters.grok import GrokAdapter
    from app.gateway.adapters.openai_ad import OpenAIAdapter
    from app.gateway.router import get_gateway, reset_gateway

    called: list[tuple[str, str]] = []

    def boom(self, request):
        called.append((getattr(self, "vendor", "unknown"), request.task_type))
        if request.task_type in {"pricing_strategy", "review_reply"} or request.execution_critical:
            raise AssertionError(f"overseas adapter {self.vendor} must not be called for critical tasks")
        raise RuntimeError("simulated overseas failure for non-critical task")

    monkeypatch.setattr(OpenAIAdapter, "available", lambda self: True)
    monkeypatch.setattr(GeminiAdapter, "available", lambda self: True)
    monkeypatch.setattr(GrokAdapter, "available", lambda self: True)
    monkeypatch.setattr(OpenAIAdapter, "complete", boom)
    monkeypatch.setattr(GeminiAdapter, "complete", boom)
    monkeypatch.setattr(GrokAdapter, "complete", boom)
    reset_gateway()

    resp = client.put("/api/settings", headers=auth_header, json={"overseas_enabled": True})
    assert resp.status_code == 200
    assert resp.json()["overseas_enabled"] is True

    # Generating strategies triggers pricing_strategy (critical).
    gen = client.post("/api/strategies/generate", headers=auth_header, json={})
    assert gen.status_code == 200, gen.text
    assert gen.json()["created"] >= 1
    critical_calls = [c for c in called if c[1] in {"pricing_strategy", "review_reply"}]
    assert critical_calls == [], critical_calls

    logs = client.get("/api/audit/model-calls", headers=auth_header)
    assert logs.status_code == 200
    rows = logs.json()["logs"]
    critical = [r for r in rows if r["task_type"] in {"pricing_strategy", "review_reply"}]
    assert critical, "expected audited pricing_strategy calls"
    assert all(r["outbound"] is False for r in critical)
    assert all(r["vendor"] not in OVERSEAS_VENDORS for r in critical)


def test_review_reply_also_stays_domestic(client, auth_header, monkeypatch):
    from app.gateway.adapters.gemini import GeminiAdapter
    from app.gateway.adapters.grok import GrokAdapter
    from app.gateway.adapters.openai_ad import OpenAIAdapter
    from app.gateway.router import reset_gateway

    called: list[tuple[str, str]] = []

    def boom(self, request):
        called.append((self.vendor, request.task_type))
        if request.task_type in {"pricing_strategy", "review_reply"} or request.execution_critical:
            raise AssertionError("overseas must not handle review_reply")
        raise RuntimeError("simulated overseas failure for non-critical task")

    monkeypatch.setattr(OpenAIAdapter, "available", lambda self: True)
    monkeypatch.setattr(GeminiAdapter, "available", lambda self: True)
    monkeypatch.setattr(GrokAdapter, "available", lambda self: True)
    monkeypatch.setattr(OpenAIAdapter, "complete", boom)
    monkeypatch.setattr(GeminiAdapter, "complete", boom)
    monkeypatch.setattr(GrokAdapter, "complete", boom)
    reset_gateway()

    client.put("/api/settings", headers=auth_header, json={"overseas_enabled": True})
    reviews = client.get("/api/reviews", headers=auth_header).json()["reviews"]
    bad = next(r for r in reviews if r["rating"] <= 2)
    drafted = client.post(f"/api/reviews/{bad['id']}/draft", headers=auth_header)
    assert drafted.status_code == 200, drafted.text
    assert drafted.json()["reply_draft"]
    assert drafted.json()["reply_status"] == "pending_review"
    critical_calls = [c for c in called if c[1] in {"pricing_strategy", "review_reply"}]
    assert critical_calls == [], critical_calls
    logs = client.get("/api/audit/model-calls", headers=auth_header).json()["logs"]
    replies = [r for r in logs if r["task_type"] == "review_reply"]
    assert replies
    assert all(not r["outbound"] and r["vendor"] not in OVERSEAS_VENDORS for r in replies)


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
    }
    values.update(overrides)
    return Settings(**values)


def _req(task="analysis", **kwargs):
    return LLMRequest(task_type=task, messages=[{"role": "user", "content": "x"}], **kwargs)


def test_default_preference_table_is_domestic_first():
    from app.gateway.preferences import DEFAULT_TASK_PREFERENCES, TASK_STRENGTHS

    gw = ModelGateway(_settings())
    assert set(DEFAULT_TASK_PREFERENCES) == set(TASK_STRENGTHS)
    for task, pref in DEFAULT_TASK_PREFERENCES.items():
        assert pref[0] == "deepseek"
        assert "qwen" in pref
        assert "self_hosted" in pref
        assert pref[-1] == "mock"
        assert pref.index("qwen") < pref.index("self_hosted")
        assert pref.index("self_hosted") < pref.index("mock")
        if task in {"pricing_strategy", "review_reply"}:
            assert not any(v in OVERSEAS_VENDORS for v in pref)


def test_preference_override_changes_order_but_skips_missing_keys():
    gw = ModelGateway(_settings(llm_task_pref_analysis="qwen,deepseek,mock", qwen_api_key=""))
    chain = gw.route_chain(_req("analysis"), overseas_enabled=False)
    assert chain[0] == "qwen"
    assert chain[1] == "deepseek"
    # no keys → both skipped, Mock is used and marked degraded
    resp = gw.complete(_req("analysis"))
    assert resp.vendor == "mock"
    assert resp.degraded is True
    assert resp.status == "ok"


def test_preferred_vendor_without_key_is_skipped_for_next():
    from app.gateway.protocol import LLMResponse

    gw = ModelGateway(_settings(deepseek_api_key="", qwen_api_key="not-a-real-secret"))
    calls: list[str] = []

    def fake_complete(request):
        calls.append("qwen")
        return LLMResponse(vendor="qwen", model="qwen-test", content="ok", status="ok")

    gw.adapters["qwen"].complete = fake_complete  # type: ignore[method-assign]
    resp = gw.complete(_req("pricing_strategy"))
    assert "deepseek" not in calls
    assert calls == ["qwen"]
    assert resp.vendor == "qwen"
    assert resp.degraded is True  # first in default chain was deepseek
    # Never put key material into the response or notices.
    assert "not-a-real-secret" not in (resp.content or "")
    assert "not-a-real-secret" not in (resp.error_message or "")
    assert "not-a-real-secret" not in (resp.degrade_notice or "")


def test_critical_preference_cannot_include_overseas_even_if_env_tries():
    gw = ModelGateway(
        _settings(
            llm_task_pref_pricing_strategy="deepseek,openai,gemini,grok,qwen,mock",
            overseas_enabled=True,
        )
    )
    chain = gw.route_chain(_req("pricing_strategy"), overseas_enabled=True)
    for vendor in OVERSEAS_VENDORS:
        assert vendor not in chain
    assert chain == ["deepseek", "qwen", "mock"]
    notes = [n for n in gw.preferences.notes if n.task == "pricing_strategy"]
    assert any(n.reason == "critical_task_blocked_from_overseas" for n in notes)


def test_unknown_vendor_in_preference_is_ignored():
    gw = ModelGateway(_settings(llm_task_pref_assistant_chat="deepseek,notarealvendor,qwen,mock"))
    chain = gw.route_chain(_req("assistant_chat"), overseas_enabled=False)
    assert "notarealvendor" not in chain
    assert chain == ["deepseek", "qwen", "mock"]
    assert any(n.reason == "unknown_vendor_ignored" for n in gw.preferences.notes)


def test_vision_prefers_gemini_after_domestic_when_overseas_on():
    gw = ModelGateway(_settings())
    off = gw.route_chain(_req("vision_or_longdoc"), overseas_enabled=False)
    on = gw.route_chain(_req("vision_or_longdoc"), overseas_enabled=True)
    assert off == ["deepseek", "qwen", "self_hosted", "mock"]
    assert on[:3] == ["deepseek", "qwen", "self_hosted"]
    assert on[3] == "gemini"
    assert "openai" in on and "grok" in on
    assert on[-1] == "mock"
    assert "self_hosted" not in OVERSEAS_VENDORS


def test_describe_ops_includes_preferences_and_resilience_hotel_payload_does_not(client, auth_header):
    gw = ModelGateway(_settings())
    public = gw.describe(for_ops=False)
    ops = gw.describe(for_ops=True)
    assert "task_preferences" not in public
    assert "resilience" not in public
    assert "circuit_breakers" not in public
    assert "task_preferences" in ops
    assert ops["task_preferences"]["pricing_strategy"]["default"] == ["deepseek", "qwen", "self_hosted", "mock"]
    assert ops["default_chain"] == ["deepseek", "qwen", "self_hosted", "mock"]
    assert any(a["vendor"] == "self_hosted" for a in ops["adapters"])
    assert ops["resilience"]["rate_limit_per_minute"] == 30
    assert ops["resilience"]["scope"] == "in-process"

    settings = client.get("/api/settings", headers=auth_header)
    assert settings.status_code == 200
    body = settings.json()["gateway"]
    assert "task_preferences" not in body
    assert "resilience" not in body
    assert "circuit_breakers" not in body
    vendors = {a["vendor"] for a in body["adapters"]}
    assert {"deepseek", "qwen", "mock"} <= vendors
    assert "self_hosted" not in vendors
    assert body.get("default_chain") == ["deepseek", "qwen", "mock"]


def test_default_critical_chain_includes_self_hosted_but_never_overseas():
    gw = ModelGateway(_settings())
    for task in ("pricing_strategy", "review_reply"):
        chain = gw.route_chain(_req(task), overseas_enabled=True)
        assert chain == ["deepseek", "qwen", "self_hosted", "mock"]
        for vendor in OVERSEAS_VENDORS:
            assert vendor not in chain


def test_missing_self_hosted_config_skips_vendor():
    from app.gateway.protocol import LLMResponse

    gw = ModelGateway(_settings())
    assert gw.adapters["self_hosted"].available() is False
    chain = gw.route_chain(_req("analysis"), overseas_enabled=False)
    assert "self_hosted" in chain
    calls: list[str] = []

    def boom(request):
        calls.append("self_hosted")
        raise AssertionError("self_hosted must be skipped when url/key/model are empty")

    gw.adapters["self_hosted"].complete = boom  # type: ignore[method-assign]
    resp = gw.complete(_req("analysis"))
    assert calls == []
    assert resp.vendor == "mock"
    assert resp.degraded is True
    assert resp.status == "ok"


def test_enabled_self_hosted_is_between_qwen_and_mock_and_usable_for_critical():
    from app.gateway.protocol import LLMResponse

    # Local loopback only — not a production host. complete() is stubbed, so no HTTP.
    gw = ModelGateway(
        _settings(
            self_hosted_api_key="test-self-hosted-secret",
            self_hosted_base_url="http://127.0.0.1:9/v1",
            self_hosted_model="oss-chat",
        )
    )
    assert gw.adapters["self_hosted"].available() is True
    chain = gw.route_chain(_req("pricing_strategy"), overseas_enabled=True)
    assert chain == ["deepseek", "qwen", "self_hosted", "mock"]
    for vendor in OVERSEAS_VENDORS:
        assert vendor not in chain

    def fake_complete(request):
        return LLMResponse(vendor="self_hosted", model="oss-chat", content="draft", status="ok", outbound=False)

    gw.adapters["self_hosted"].complete = fake_complete  # type: ignore[method-assign]
    resp = gw.complete(_req("review_reply"))
    assert resp.vendor == "self_hosted"
    assert resp.outbound is False
    assert resp.degraded is True  # official domestic APIs were skipped
    assert "test-self-hosted-secret" not in (resp.content or "")
    assert "test-self-hosted-secret" not in (resp.error_message or "")
    assert "127.0.0.1" not in (resp.error_message or "")

    public = gw.describe(for_ops=False)
    ops = gw.describe(for_ops=True)
    assert "self_hosted" not in {a["vendor"] for a in public["adapters"]}
    assert any(a["vendor"] == "self_hosted" and a["available"] is True for a in ops["adapters"])
    # Describe must not leak the key or a configured URL on either payload.
    import json

    dumped = json.dumps({"public": public, "ops": ops}, ensure_ascii=False)
    assert "test-self-hosted-secret" not in dumped
    assert "127.0.0.1" not in dumped
