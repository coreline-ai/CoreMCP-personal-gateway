from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from coremcp.auth.rate_limit import (
    InMemoryRateLimiter,
    RateLimitDecision,
    RedisRateLimiter,
)


@pytest.fixture()
def fake_redis_client():
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.FakeStrictRedis(decode_responses=False)


@pytest.fixture()
def limiter(fake_redis_client) -> RedisRateLimiter:
    rl = RedisRateLimiter()
    rl._redis_client = fake_redis_client  # type: ignore[attr-defined]
    return rl


def test_redis_limiter_increments_and_allows_within_window(limiter: RedisRateLimiter) -> None:
    for attempt in range(5):
        decision = limiter.check("bucket-a", limit=5, window_seconds=10)
        assert decision.allowed is True, f"attempt {attempt}: {decision}"
        assert decision.remaining == 5 - attempt - 1
        assert decision.retry_after_seconds is None


def test_redis_limiter_blocks_after_limit(limiter: RedisRateLimiter) -> None:
    for _ in range(3):
        assert limiter.check("bucket-b", limit=3, window_seconds=10).allowed
    blocked = limiter.check("bucket-b", limit=3, window_seconds=10)
    assert blocked.allowed is False
    assert blocked.remaining == 0
    assert blocked.retry_after_seconds is not None and blocked.retry_after_seconds >= 1


def test_redis_limiter_window_expiry_resets_count(limiter: RedisRateLimiter, fake_redis_client) -> None:
    limiter.check("bucket-c", limit=2, window_seconds=1)
    limiter.check("bucket-c", limit=2, window_seconds=1)
    blocked = limiter.check("bucket-c", limit=2, window_seconds=1)
    assert blocked.allowed is False
    # Simulate window expiry by deleting the key (fakeredis time isn't moving).
    fake_redis_client.delete("bucket-c")
    after = limiter.check("bucket-c", limit=2, window_seconds=1)
    assert after.allowed is True
    assert after.remaining == 1


def test_redis_limiter_falls_back_to_memory_when_client_unavailable() -> None:
    rl = RedisRateLimiter(url=None)
    assert rl._redis_client is None  # noqa: SLF001 - test asserts fallback wiring
    decision = rl.check("bucket-d", limit=2, window_seconds=10)
    assert decision.allowed is True
    again = rl.check("bucket-d", limit=2, window_seconds=10)
    assert again.allowed is True
    third = rl.check("bucket-d", limit=2, window_seconds=10)
    assert third.allowed is False


def test_redis_limiter_falls_back_on_transport_error(limiter: RedisRateLimiter) -> None:
    class BrokenPipeline:
        def incr(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            return self

        def expire(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            return self

        def pttl(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            return self

        def execute(self):
            raise RuntimeError("simulated wire failure")

    with patch.object(limiter._redis_client, "pipeline", return_value=BrokenPipeline()):  # noqa: SLF001
        decision = limiter.check("bucket-e", limit=3, window_seconds=10)
    assert isinstance(decision, RateLimitDecision)
    assert decision.allowed is True  # in-memory fallback grants first hit


def test_redis_limiter_rejects_invalid_arguments(limiter: RedisRateLimiter) -> None:
    with pytest.raises(ValueError):
        limiter.check("bucket-f", limit=0, window_seconds=10)
    with pytest.raises(ValueError):
        limiter.check("bucket-f", limit=5, window_seconds=0)


def test_in_memory_and_redis_share_decision_protocol(limiter: RedisRateLimiter) -> None:
    in_mem = InMemoryRateLimiter()
    a = in_mem.check("bucket-g", limit=2, window_seconds=10)
    b = limiter.check("bucket-g", limit=2, window_seconds=10)
    # Both backends return the same dataclass shape.
    assert type(a).__name__ == type(b).__name__ == "RateLimitDecision"
    assert a.allowed is True and b.allowed is True
