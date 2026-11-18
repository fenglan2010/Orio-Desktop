"""Task-based model router with domestic-first fallback and outbound guards."""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.config import CRITICAL_TASKS, OVERSEAS_VENDORS, Settings, get_settings
from app.gateway.adapters.deepseek import DeepSeekAdapter
from app.gateway.adapters.gemini import GeminiAdapter
from app.gateway.adapters.grok import GrokAdapter
from app.gateway.adapters.mock import MockModelAdapter
from app.gateway.adapters.openai_ad import OpenAIAdapter
from app.gateway.adapters.qwen import QwenAdapter
from app.gateway.adapters.self_hosted import SelfHostedAdapter
from app.gateway.preferences import (
    DEFAULT_DOMESTIC_CHAIN,
    DEFAULT_TASK_PREFERENCES,
    TASK_STRENGTHS,
    PreferenceTable,
    build_route_chain,
    resolve_preferences,
)
from app.gateway.protocol import LLMRequest, LLMResponse
from app.gateway.resilience import (
    ResiliencePolicy,
    VendorTimeoutError,
    error_code_for,
    invoke_with_timeout,
)
from app.models import ModelCallLog, SystemSetting


def read_overseas_enabled(db: Session | None, settings: Settings) -> bool:
    """DB toggle overrides the environment default when present."""
    if db is None:
        return bool(settings.overseas_enabled)
    row = db.get(SystemSetting, "overseas_enabled")
    if row is None:
        return bool(settings.overseas_enabled)
    return row.value.strip().lower() in {"1", "true", "yes", "on"}


def is_critical(request: LLMRequest) -> bool:
    return request.execution_critical or request.task_type in CRITICAL_TASKS


def _policy_from_settings(settings: Settings) -> ResiliencePolicy:
    return ResiliencePolicy(
        rate_limit_per_minute=settings.llm_rate_limit_per_minute,
        vendor_concurrency=settings.llm_vendor_concurrency,
        global_concurrency=settings.llm_global_concurrency,
        queue_wait_seconds=settings.llm_queue_wait_seconds,
        timeout_seconds=settings.llm_timeout_seconds,
        circuit_failure_threshold=settings.llm_circuit_failure_threshold,
        circuit_cooldown_seconds=settings.llm_circuit_cooldown_seconds,
    )


class ModelGateway:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.adapters = {
            "deepseek": DeepSeekAdapter(self.settings),
            "qwen": QwenAdapter(self.settings),
            "self_hosted": SelfHostedAdapter(self.settings),
            "openai": OpenAIAdapter(self.settings),
            "gemini": GeminiAdapter(self.settings),
            "grok": GrokAdapter(self.settings),
            "mock": MockModelAdapter(),
        }
        self.preferences: PreferenceTable = resolve_preferences(self.settings)
        self.resilience = _policy_from_settings(self.settings)

    def route_chain(self, request: LLMRequest, overseas_enabled: bool) -> list[str]:
        """
        Return vendor names to try, in order. The model never picks the next hop —
        the gateway does. Critical tasks are barred from overseas vendors even if
        the overseas switch is on or a preference list tries to include them.
        """
        return build_route_chain(
            request.task_type,
            critical=is_critical(request),
            overseas_enabled=overseas_enabled,
            table=self.preferences,
        )

    def complete(self, request: LLMRequest, db: Session | None = None) -> LLMResponse:
        overseas_enabled = read_overseas_enabled(db, self.settings)
        chain = self.route_chain(request, overseas_enabled)
        errors: list[str] = []
        started_chain = time.perf_counter()

        try:
            for vendor in chain:
                response, skip_reason = self._try_vendor(
                    request, vendor=vendor, chain=chain, overseas_enabled=overseas_enabled, db=db
                )
                if response is not None:
                    if errors and (response.vendor == "mock" or response.degraded):
                        # vendor:code tokens only — never exception text
                        if not response.error_message:
                            response.error_message = "; ".join(errors)
                    return response
                if skip_reason:
                    errors.append(skip_reason)
        except Exception:  # noqa: BLE001 — never abort the LangGraph decision flow
            errors.append("gateway:uncaught")

        return self._mock_fallback(request, db, errors, started_chain)

    def _try_vendor(
        self,
        request: LLMRequest,
        *,
        vendor: str,
        chain: list[str],
        overseas_enabled: bool,
        db: Session | None,
    ) -> tuple[LLMResponse | None, str]:
        adapter = self.adapters.get(vendor)
        if adapter is None:
            return None, f"{vendor}:unknown_vendor"
        if vendor != "mock" and not adapter.available():
            return None, f"{vendor}:no_key"
        if vendor in OVERSEAS_VENDORS:
            if is_critical(request) or not overseas_enabled:
                return None, f"{vendor}:blocked_outbound"
        if vendor != "mock":
            allowed, state = self.resilience.breaker.allow(vendor)
            if not allowed:
                self._audit(
                    db,
                    request,
                    vendor=vendor,
                    model=getattr(adapter, "model", vendor),
                    outbound=vendor in OVERSEAS_VENDORS,
                    degraded=False,
                    status="skipped",
                    latency_ms=0,
                    error_code="circuit_open",
                )
                return None, f"{vendor}:circuit_open"
        else:
            state = "closed"

        if vendor != "mock" and not self.resilience.rate_limiter.allow(vendor):
            return None, f"{vendor}:rate_limited"

        acquired = True if vendor == "mock" else self.resilience.gate.acquire(vendor)
        if not acquired:
            self._audit(
                db,
                request,
                vendor=vendor,
                model=getattr(adapter, "model", vendor),
                outbound=vendor in OVERSEAS_VENDORS,
                degraded=False,
                status="skipped",
                latency_ms=0,
                error_code="queue_timeout",
            )
            return None, f"{vendor}:queue_timeout"

        t0 = time.perf_counter()
        try:
            if vendor == "mock":
                response = adapter.complete(request)
            else:
                response = self._invoke_guarded(vendor, lambda: adapter.complete(request))
        except Exception as exc:  # noqa: BLE001 — we degrade, then audit
            latency = int((time.perf_counter() - t0) * 1000)
            code = error_code_for(exc)
            if vendor != "mock":
                self.resilience.breaker.record_failure(vendor)
            self._audit(
                db,
                request,
                vendor=vendor,
                model=getattr(adapter, "model", vendor),
                outbound=vendor in OVERSEAS_VENDORS,
                degraded=False,
                status="error",
                latency_ms=latency,
                error_code=code,
            )
            return None, f"{vendor}:{code}"

        latency = int((time.perf_counter() - t0) * 1000)
        response.latency_ms = latency
        if vendor != "mock":
            self.resilience.breaker.record_success(vendor)
        used_fallback = vendor != chain[0] or vendor == "mock"
        if used_fallback:
            response.degraded = True
            if not response.degrade_notice:
                response.degrade_notice = "当前为降级结果"
        if is_critical(request) and response.outbound:
            return None, f"{vendor}:rejected_outbound_result"
        # Probe state is only used for ops visibility; success already closed the breaker.
        _ = state
        self._audit(
            db,
            request,
            vendor=response.vendor,
            model=response.model,
            outbound=response.outbound,
            degraded=response.degraded,
            status=response.status,
            latency_ms=latency,
            error_code="",
            usage=response.usage,
        )
        return response, ""

    def _invoke_guarded(self, vendor: str, func):
        """Run a vendor call with timeout; release the concurrency slot when the worker finishes."""

        def work():
            try:
                return func()
            finally:
                self.resilience.gate.release(vendor)

        try:
            return invoke_with_timeout(work, self.resilience.timeout_seconds)
        except VendorTimeoutError:
            # work() still holds the slot until the underlying call returns.
            raise
        except Exception:
            # work() already released in finally if it raised before returning.
            raise

    def _mock_fallback(
        self,
        request: LLMRequest,
        db: Session | None,
        errors: list[str],
        started_chain: float,
    ) -> LLMResponse:
        latency = int((time.perf_counter() - started_chain) * 1000)
        response = self.adapters["mock"].complete(request)
        response.degraded = True
        response.degrade_notice = "当前为降级结果（全部上游失败）"
        response.error_code = "all_vendors_failed"
        # Only vendor:code tokens — never include exception text (may leak URLs or keys).
        response.error_message = "; ".join(errors)
        response.latency_ms = latency
        self._audit(
            db,
            request,
            vendor="mock",
            model="mock-deterministic",
            outbound=False,
            degraded=True,
            status="ok",
            latency_ms=latency,
            error_code="all_vendors_failed",
        )
        return response

    def _audit(
        self,
        db: Session | None,
        request: LLMRequest,
        *,
        vendor: str,
        model: str,
        outbound: bool,
        degraded: bool,
        status: str,
        latency_ms: int,
        error_code: str,
        usage: dict | None = None,
    ) -> None:
        if db is None:
            return
        usage = usage or {}
        db.add(
            ModelCallLog(
                vendor=vendor,
                model=model,
                task_type=request.task_type,
                hotel_id=request.hotel_id,
                tenant_key=request.tenant_key,
                outbound=outbound,
                degraded=degraded,
                status=status,
                latency_ms=latency_ms,
                error_code=error_code or "",
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            )
        )
        try:
            db.commit()
        except Exception:
            db.rollback()

    def describe(self, db: Session | None = None, *, for_ops: bool = False) -> dict:
        settings = self.settings
        overseas_enabled = read_overseas_enabled(db, settings)
        adapters = []
        for name, adapter in self.adapters.items():
            adapters.append(
                {
                    "vendor": name,
                    "role": {
                        "deepseek": "国产主模型",
                        "qwen": "国产备用",
                        "self_hosted": "自建开源模型（预留，默认关闭）",
                        "openai": "境外对照（默认关闭）",
                        "gemini": "境外，长上下文与多模态（默认关闭）",
                        "grok": "境外，非收益决策首选（默认关闭）",
                        "mock": "本地演示与降级",
                    }.get(name, name),
                    "available": adapter.available(),
                    "outbound": name in OVERSEAS_VENDORS,
                    "model": getattr(adapter, "model", "mock-deterministic"),
                    "status": (
                        "ready"
                        if adapter.available()
                        else (
                            "always_on"
                            if name == "mock"
                            else ("not_configured_will_skip" if name == "self_hosted" else "no_key_will_skip")
                        )
                    ),
                }
            )
        # Hotel Settings must not see the reserved self-hosted hook, its URL, or keys.
        visible = adapters if for_ops else [a for a in adapters if a["vendor"] != "self_hosted"]
        payload = {
            "overseas_enabled": overseas_enabled,
            "critical_tasks_blocked_from_overseas": sorted(CRITICAL_TASKS),
            "default_chain": list(DEFAULT_DOMESTIC_CHAIN) if for_ops else ["deepseek", "qwen", "mock"],
            "adapters": visible,
            "rules": [
                "按任务路由，不按厂商写业务。",
                "主模型失败则切备用国产模型，再失败则切 Mock，并标明降级。",
                "境外模型默认关闭，需单独授权开启。",
                "pricing_strategy、review_reply 及任何将导致调价/回复/执行的调用，即使境外模型已开启也不得出境。",
                "调用审计不记录密钥。",
            ],
        }
        if for_ops:
            payload["task_preferences"] = {
                task: {
                    "strength": TASK_STRENGTHS.get(task, ""),
                    "default": list(DEFAULT_TASK_PREFERENCES[task]),
                    "configured": list(self.preferences.preferred(task)),
                    "overridden": task in self.preferences.overrides,
                }
                for task in TASK_STRENGTHS
            }
            payload["preference_notes"] = [
                {"task": n.task, "vendor": n.vendor, "reason": n.reason}
                for n in self.preferences.notes
            ]
            payload["resilience"] = self.resilience.describe()
            payload["circuit_breakers"] = self.resilience.circuit_snapshot()
            payload["rules"] = list(payload["rules"]) + [
                "任务偏好与限流/排队/熔断只在服务器环境或内部运维可见，酒店设置不配置路由或密钥。",
                "自建开源模型是预留接入：默认关闭，不编造生产地址；在腾讯云或阿里云部署后填写服务器环境变量即可启用。",
                "限流与熔断为单进程内实现；多副本需后续接入共享限流。",
            ]
        return payload


_gateway: ModelGateway | None = None


def get_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway()
    return _gateway


def reset_gateway() -> None:
    global _gateway
    if _gateway is not None:
        try:
            _gateway.resilience.reset()
        except Exception:
            pass
    _gateway = None
