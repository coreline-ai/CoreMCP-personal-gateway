from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int | None
    reset_at: float


class FixedWindowRateLimiter:
    """Small in-memory fixed-window limiter for single-process OAuth endpoints."""

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
