from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from coremcp.mcp_gateway.sessions import SessionStore
from coremcp.proxy.circuit_breaker import CircuitBreaker, CircuitOpenError


def test_circuit_breaker_failure_threshold_opens() -> None:
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=10)

    first = breaker.record_failure("svc-a", now=100.0)
    assert first.state == "closed"
    assert first.failure_count == 1

    second = breaker.record_failure("svc-a", now=101.0)
    assert second.state == "open"
    assert second.failure_count == 2

    with pytest.raises(CircuitOpenError) as exc_info:
        breaker.before_request("svc-a", now=105.0)
    assert exc_info.value.service_id == "svc-a"
    assert exc_info.value.retry_after_seconds == pytest.approx(6.0)

    allowed, snapshot = breaker.allow_request("svc-a", now=105.0)
    assert allowed is False
    assert snapshot.state == "open"


def test_circuit_breaker_cooldown_half_open_success_closes() -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=5)

    opened = breaker.record_failure("svc-a", now=200.0)
    assert opened.state == "open"

    trial = breaker.before_request("svc-a", now=205.0)
    assert trial.state == "half-open"

    closed = breaker.record_success("svc-a")
    assert closed.state == "closed"
    assert closed.failure_count == 0

    allowed = breaker.before_request("svc-a", now=206.0)
    assert allowed.state == "closed"


def test_circuit_breaker_uses_service_id_scoped_state() -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10)

    breaker.record_failure("svc-a", now=10.0)

    with pytest.raises(CircuitOpenError):
        breaker.before_request("svc-a", now=11.0)

    assert breaker.before_request("svc-b", now=11.0).state == "closed"


def test_session_store_reap_idle_sessions_and_touch() -> None:
    store = SessionStore()
    base = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)

    idle = store.create("2025-03-26")
    active = store.create("2025-03-26")
    touched = store.create("2025-03-26")

    assert store.touch(idle.id, now=base - timedelta(seconds=120)) is True
    assert store.touch(active.id, now=base - timedelta(seconds=10)) is True
    assert store.touch(touched.id, now=base - timedelta(seconds=120)) is True
    assert store.touch(touched.id, now=base - timedelta(seconds=1)) is True

    removed = store.reap_idle(max_idle_seconds=60, now=base)

    assert removed == 1
    assert store.get(idle.id) is None
    assert store.get(active.id) is active
    assert store.get(touched.id) is touched
    assert store.count_active() == 2


def test_session_store_rejects_negative_idle_threshold() -> None:
    with pytest.raises(ValueError):
        SessionStore().reap_idle(-1)
