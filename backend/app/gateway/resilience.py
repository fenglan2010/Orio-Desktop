"""In-process rate limit, concurrency queue, timeout, and circuit breaker.

These guards protect a single API process. They are not a distributed limiter:
Compose / Kubernetes multi-replica deployments need a later shared limiter
(for example the existing Redis broker) if request volume is split across
processes. No secrets are stored or logged here.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")

TIMEOUT_ERROR_NAMES = frozenset(
    {
        "TimeoutError",
        "VendorTimeoutError",
        "TimeoutException",
        "ReadTimeout",
        "ConnectTimeout",
        "WriteTimeout",
        "PoolTimeout",
    }
)


class VendorTimeoutError(TimeoutError):
    """The vendor call exceeded the configured per-call timeout."""


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, VendorTimeoutError)):
        return True
    return type(exc).__name__ in TIMEOUT_ERROR_NAMES


def error_code_for(exc: BaseException) -> str:
    if is_timeout_error(exc):
        return "timeout"
    return type(exc).__name__


class RateLimiter:
    """Rolling-window per-vendor limiter. 0 disables the limit."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = max(0, int(per_minute))
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, vendor: str, now: float | None = None) -> bool:
        if self.per_minute <= 0:
            return True
        ts = now if now is not None else time.monotonic()
        with self._lock:
            q = self._calls[vendor]
            while q and ts - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(ts)
            return True

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


class ConcurrencyGate:
    """Global + per-vendor in-process semaphore with a short wait (the queue)."""

    def __init__(self, global_limit: int, vendor_limit: int, queue_wait_seconds: float) -> None:
        self.global_limit = max(1, int(global_limit))
        self.vendor_limit = max(1, int(vendor_limit))
        self.queue_wait_seconds = max(0.0, float(queue_wait_seconds))
        self._global = threading.BoundedSemaphore(self.global_limit)
        self._vendors: dict[str, threading.BoundedSemaphore] = {}
        self._lock = threading.Lock()

    def _vendor_sem(self, vendor: str) -> threading.BoundedSemaphore:
        with self._lock:
            sem = self._vendors.get(vendor)
            if sem is None:
                sem = threading.BoundedSemaphore(self.vendor_limit)
                self._vendors[vendor] = sem
            return sem

    def acquire(self, vendor: str, wait_seconds: float | None = None) -> bool:
        budget = self.queue_wait_seconds if wait_seconds is None else max(0.0, float(wait_seconds))
        deadline = time.monotonic() + budget

        def remaining() -> float:
            return max(0.0, deadline - time.monotonic())

        if not self._global.acquire(timeout=remaining() if budget > 0 else 0):
            return False
        if not self._vendor_sem(vendor).acquire(timeout=remaining() if budget > 0 else 0):
            self._global.release()
            return False
        return True

    def release(self, vendor: str) -> None:
        try:
            self._vendor_sem(vendor).release()
        except ValueError:
            pass
        try:
            self._global.release()
        except ValueError:
            pass


@dataclass
class CircuitState:
    vendor: str
    state: str  # closed | open | half_open
    failures: int
    opened_for_seconds: float | None
    probe_in_flight: bool


class CircuitBreaker:
    """Per-vendor closed / open / half-open breaker.

    threshold <= 0 disables the breaker (always closed).
    """

    def __init__(self, threshold: int, cooldown_seconds: float) -> None:
        self.threshold = int(threshold)
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._lock = threading.Lock()
        self._failures: dict[str, int] = defaultdict(int)
        self._state: dict[str, str] = defaultdict(lambda: "closed")
        self._opened_at: dict[str, float] = {}
        self._probe: dict[str, bool] = defaultdict(bool)

    def disabled(self) -> bool:
        return self.threshold <= 0

    def allow(self, vendor: str) -> tuple[bool, str]:
        if self.disabled() or vendor == "mock":
            return True, "closed"
        with self._lock:
            state = self._state[vendor]
            if state == "closed":
                return True, "closed"
            now = time.monotonic()
            opened_at = self._opened_at.get(vendor, now)
            if state == "open":
                if now - opened_at >= self.cooldown_seconds:
                    self._state[vendor] = "half_open"
                    self._probe[vendor] = True
                    return True, "half_open"
                return False, "open"
            # half_open: only one probe at a time
            if self._probe[vendor]:
                return False, "open"
            self._probe[vendor] = True
            return True, "half_open"

    def record_success(self, vendor: str) -> None:
        if self.disabled() or vendor == "mock":
            return
        with self._lock:
            self._failures[vendor] = 0
            self._state[vendor] = "closed"
            self._probe[vendor] = False
            self._opened_at.pop(vendor, None)

    def record_failure(self, vendor: str) -> None:
        if self.disabled() or vendor == "mock":
            return
        with self._lock:
            state = self._state[vendor]
            if state == "half_open":
                self._state[vendor] = "open"
                self._opened_at[vendor] = time.monotonic()
                self._probe[vendor] = False
                self._failures[vendor] = self.threshold
                return
            self._failures[vendor] += 1
            self._probe[vendor] = False
            if self._failures[vendor] >= self.threshold:
                self._state[vendor] = "open"
                self._opened_at[vendor] = time.monotonic()

    def snapshot(self) -> list[CircuitState]:
        with self._lock:
            now = time.monotonic()
            vendors = set(self._state) | set(self._failures)
            out: list[CircuitState] = []
            for vendor in sorted(vendors):
                state = self._state.get(vendor, "closed")
                opened_at = self._opened_at.get(vendor)
                opened_for = (now - opened_at) if opened_at is not None and state != "closed" else None
                out.append(
                    CircuitState(
                        vendor=vendor,
                        state=state,
                        failures=int(self._failures.get(vendor, 0)),
                        opened_for_seconds=None if opened_for is None else round(opened_for, 3),
                        probe_in_flight=bool(self._probe.get(vendor)),
                    )
                )
            return out

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._state.clear()
            self._opened_at.clear()
            self._probe.clear()


# Shared pool so timed-out vendor calls still occupy a worker until they return,
# which is bounded by the concurrency gate held by the caller.
_EXECUTOR = ThreadPoolExecutor(max_workers=32, thread_name_prefix="llm-call")


def invoke_with_timeout(func: Callable[[], T], timeout_seconds: float) -> T:
    """Run func() and raise VendorTimeoutError if it exceeds timeout_seconds.

    A timed-out call may keep running in the background until the vendor
    returns; the caller should keep the concurrency slot until `on_finished`.
    This helper is the simple form: it does not release slots.
    """
    timeout = max(0.05, float(timeout_seconds))
    future = _EXECUTOR.submit(func)
    try:
        return future.result(timeout=timeout)
    except VendorTimeoutError:
        raise
    except TimeoutError as exc:  # concurrent.futures.TimeoutError
        raise VendorTimeoutError("vendor call timed out") from exc


class ResiliencePolicy:
    def __init__(
        self,
        *,
        rate_limit_per_minute: int,
        vendor_concurrency: int,
        global_concurrency: int,
        queue_wait_seconds: float,
        timeout_seconds: float,
        circuit_failure_threshold: int,
        circuit_cooldown_seconds: float,
    ) -> None:
        self.rate_limit_per_minute = max(0, int(rate_limit_per_minute))
        self.vendor_concurrency = max(1, int(vendor_concurrency))
        self.global_concurrency = max(1, int(global_concurrency))
        self.queue_wait_seconds = max(0.0, float(queue_wait_seconds))
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.circuit_failure_threshold = int(circuit_failure_threshold)
        self.circuit_cooldown_seconds = max(0.0, float(circuit_cooldown_seconds))
        self.rate_limiter = RateLimiter(self.rate_limit_per_minute)
        self.gate = ConcurrencyGate(
            self.global_concurrency, self.vendor_concurrency, self.queue_wait_seconds
        )
        self.breaker = CircuitBreaker(self.circuit_failure_threshold, self.circuit_cooldown_seconds)

    def describe(self) -> dict:
        return {
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "vendor_concurrency": self.vendor_concurrency,
            "global_concurrency": self.global_concurrency,
            "queue_wait_seconds": self.queue_wait_seconds,
            "timeout_seconds": self.timeout_seconds,
            "circuit_failure_threshold": self.circuit_failure_threshold,
            "circuit_cooldown_seconds": self.circuit_cooldown_seconds,
            "scope": "in-process",
            "note": (
                "限流、排队与熔断均在单个 API 进程内生效；"
                "Compose / Kubernetes 多副本需要后续接入共享限流（例如已有 Redis）。"
            ),
        }

    def circuit_snapshot(self) -> list[dict]:
        return [
            {
                "vendor": item.vendor,
                "state": item.state,
                "failures": item.failures,
                "opened_for_seconds": item.opened_for_seconds,
                "probe_in_flight": item.probe_in_flight,
            }
            for item in self.breaker.snapshot()
        ]

    def reset(self) -> None:
        self.rate_limiter.reset()
        self.breaker.reset()
