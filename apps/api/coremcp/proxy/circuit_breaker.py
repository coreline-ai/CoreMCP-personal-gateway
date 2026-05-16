from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from coremcp.errors import CoreMcpRuntimeError

CircuitBreakerState = Literal["closed", "open", "half-open"]


class CircuitOpenError(CoreMcpRuntimeError):
    """Raised when a downstream service circuit is open."""

    def __init__(self, service_id: str, retry_after_seconds: float | None = None) -> None:
        self.service_id = service_id
        self.retry_after_seconds = retry_after_seconds
        message = f"Circuit breaker is open for service '{service_id}'"
        if retry_after_seconds is not None:
            message = f"{message}; retry after {retry_after_seconds:.3f}s"
        super().__init__(message)


@dataclass(slots=True)
class CircuitBreakerSnapshot:
    service_id: str
    state: CircuitBreakerState
    failure_count: int
    opened_at: float | None = None
    retry_after_seconds: float | None = None


@dataclass(slots=True)
class _CircuitEntry:
    state: CircuitBreakerState = "closed"
    failure_count: int = 0
    opened_at: float | None = None


class CircuitBreaker:
    """In-memory per-service circuit breaker for downstream MCP calls.

    Integration contract:
    - Gateway/proxy call sites invoke ``before_request(service_id)`` before
      dispatching a downstream request.
    - Successful downstream responses call ``record_success(service_id)``.
    - Timeout/network/5xx-style downstream failures call ``record_failure(service_id)``.
    - Main/repository-owned inflight/job cleanup should use a separate scheduler;
      this helper intentionally has no DB or FastAPI dependencies.
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = float(cooldown_seconds)
        self._entries: dict[str, _CircuitEntry] = {}

    def before_request(
        self,
        service_id: str,
        now: float | None = None,
        raise_on_open: bool = True,
    ) -> CircuitBreakerSnapshot:
        """Validate that a request may proceed.

        If the cooldown has elapsed, an open circuit transitions to half-open and
        allows one trial request. Callers must then record success/failure.
        Set ``raise_on_open=False`` to receive an open-state snapshot instead of
        raising ``CircuitOpenError``.
        """
        current = self._now(now)
        entry = self._entry(service_id)

        if entry.state == "open":
            retry_after = self._retry_after(entry, current)
            if retry_after <= 0:
                entry.state = "half-open"
                entry.opened_at = None
                return self.snapshot(service_id, now=current)
            if not raise_on_open:
                return self.snapshot(service_id, now=current)
            raise CircuitOpenError(service_id, retry_after_seconds=retry_after)

        return self.snapshot(service_id, now=current)

    def allow_request(
        self, service_id: str, now: float | None = None
    ) -> tuple[bool, CircuitBreakerSnapshot]:
        """Status-returning variant of ``before_request`` for non-exception flows."""
        snapshot = self.before_request(service_id, now=now, raise_on_open=False)
        return snapshot.state != "open", snapshot

    def record_success(self, service_id: str) -> CircuitBreakerSnapshot:
        entry = self._entry(service_id)
        entry.state = "closed"
        entry.failure_count = 0
        entry.opened_at = None
        return self.snapshot(service_id)

    def record_failure(
        self, service_id: str, now: float | None = None
    ) -> CircuitBreakerSnapshot:
        current = self._now(now)
        entry = self._entry(service_id)

        if entry.state == "half-open":
            entry.failure_count = max(entry.failure_count, self.failure_threshold)
            entry.state = "open"
            entry.opened_at = current
            return self.snapshot(service_id, now=current)

        entry.failure_count += 1
        if entry.failure_count >= self.failure_threshold:
            entry.state = "open"
            entry.opened_at = current
        return self.snapshot(service_id, now=current)

    def snapshot(self, service_id: str, now: float | None = None) -> CircuitBreakerSnapshot:
        current = self._now(now)
        entry = self._entry(service_id)
        retry_after = None
        if entry.state == "open":
            retry_after = max(0.0, self._retry_after(entry, current))
        return CircuitBreakerSnapshot(
            service_id=service_id,
            state=entry.state,
            failure_count=entry.failure_count,
            opened_at=entry.opened_at,
            retry_after_seconds=retry_after,
        )

    def _entry(self, service_id: str) -> _CircuitEntry:
        if not service_id:
            raise ValueError("service_id is required")
        return self._entries.setdefault(service_id, _CircuitEntry())

    def _retry_after(self, entry: _CircuitEntry, now: float) -> float:
        if entry.opened_at is None:
            return 0.0
        return self.cooldown_seconds - (now - entry.opened_at)

    @staticmethod
    def _now(now: float | None = None) -> float:
        return time.monotonic() if now is None else float(now)
