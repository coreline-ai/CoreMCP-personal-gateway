"""Rate-limit backends.

The codebase originally shipped a single ``FixedWindowRateLimiter`` —
in-memory, single-process. ``RateLimitBackend`` formalises the protocol so
the in-memory implementation can be swapped for a distributed one (Redis,
etc.) without touching the call sites.

For now we ship ``InMemoryRateLimiter`` (the historical behaviour, kept as
``FixedWindowRateLimiter`` alias for back-compat) and ``RedisRateLimiter``
which is a stub: it attempts to import ``redis`` and, on failure or absence
of a connection URL, transparently delegates to an in-memory backend with a
single warning log. The actual Redis wire integration is intentionally
deferred — see dev-plan/implement_20260523_082116.md Phase 2.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int | None
    reset_at: float


@runtime_checkable
class RateLimitBackend(Protocol):
    """Protocol every rate-limit backend must satisfy."""

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        ...

    def clear(self) -> None:
        ...


class InMemoryRateLimiter:
    """Single-process fixed-window limiter. Backwards-compatible with the
    original ``FixedWindowRateLimiter`` — values and decisions match exactly.
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._buckets: dict[str, tuple[int, float]] = {}

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be >= 1")

        now = float(self._clock())
        count, reset_at = self._buckets.get(key, (0, now + window_seconds))
        if now >= reset_at:
            count = 0
            reset_at = now + window_seconds

        if count >= limit:
            retry_after = max(1, int(math.ceil(reset_at - now)))
            return RateLimitDecision(
                allowed=False,
                remaining=0,
                retry_after_seconds=retry_after,
                reset_at=reset_at,
            )

        count += 1
        self._buckets[key] = (count, reset_at)
        return RateLimitDecision(
            allowed=True,
            remaining=max(0, limit - count),
            retry_after_seconds=None,
            reset_at=reset_at,
        )

    def clear(self) -> None:
        self._buckets.clear()


# Back-compat alias. Existing imports of FixedWindowRateLimiter continue to work.
FixedWindowRateLimiter = InMemoryRateLimiter


class RedisRateLimiter:
    """Stub for a distributed Redis-backed limiter.

    Until the Redis wire integration ships, this class transparently delegates
    to :class:`InMemoryRateLimiter`. The single behavioural difference is a
    one-time warning emitted on construction so operators see they are not in
    fact getting cross-process limits yet.
    """

    def __init__(self, url: str | None = None, *, clock: Callable[[], float] | None = None) -> None:
        self.url = url
        self._fallback = InMemoryRateLimiter(clock=clock)
        self._redis_client = None
        try:
            if url:
                import redis  # type: ignore[import-not-found]  # optional dependency
                self._redis_client = redis.Redis.from_url(url)
        except Exception as exc:  # noqa: BLE001 - any failure falls back to memory
            logger.warning("RedisRateLimiter falling back to in-memory: %s", exc)
            self._redis_client = None
        if self._redis_client is None and url:
            logger.warning(
                "RedisRateLimiter url=%s configured but redis client unavailable; using in-memory fallback",
                url,
            )

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be >= 1")
        if self._redis_client is None:
            return self._fallback.check(key, limit=limit, window_seconds=window_seconds)

        # Atomic INCR + EXPIRE-on-first-hit fixed-window check.
        try:
            pipe = self._redis_client.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            pipe.pttl(key)
            results = pipe.execute()
        except Exception as exc:  # noqa: BLE001 - redis transport failures must not crash callers
            logger.warning("RedisRateLimiter wire failure for key=%s, falling back to memory: %s", key, exc)
            return self._fallback.check(key, limit=limit, window_seconds=window_seconds)

        count = int(results[0])
        pttl_ms = int(results[2]) if results[2] is not None else window_seconds * 1000
        if pttl_ms < 0:  # key has no TTL (expire NX didn't take); use full window.
            pttl_ms = window_seconds * 1000
        reset_at = time.time() + (pttl_ms / 1000.0)

        if count > limit:
            retry_after = max(1, int(math.ceil(pttl_ms / 1000.0)))
            return RateLimitDecision(
                allowed=False,
                remaining=0,
                retry_after_seconds=retry_after,
                reset_at=reset_at,
            )
        return RateLimitDecision(
            allowed=True,
            remaining=max(0, limit - count),
            retry_after_seconds=None,
            reset_at=reset_at,
        )

    def clear(self) -> None:
        self._fallback.clear()


def build_rate_limiter(
    backend: str = "memory", *, redis_url: str | None = None
) -> RateLimitBackend:
    """Construct a rate limiter from a string backend identifier.

    Used by ``create_app`` to honour the ``COREMCP_RATE_LIMIT_BACKEND`` env.
    Unknown identifiers fall back to in-memory with a warning.
    """
    if backend == "redis":
        return RedisRateLimiter(redis_url)
    if backend != "memory":
        logger.warning("Unknown rate limit backend %r; falling back to in-memory", backend)
    return InMemoryRateLimiter()


__all__ = [
    "FixedWindowRateLimiter",
    "InMemoryRateLimiter",
    "RateLimitBackend",
    "RateLimitDecision",
    "RedisRateLimiter",
    "build_rate_limiter",
]
