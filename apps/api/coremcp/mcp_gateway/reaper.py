from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, TypeAlias


@dataclass(frozen=True, slots=True)
class InflightReapResult:
    """Summary returned after stale in-flight request cleanup."""

    removed_count: int
    removed_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReaperTickResult:
    """Summary returned by one background reaper tick."""

    sessions_removed: int | None = None
    inflight: InflightReapResult | None = None
    stuck_jobs_marked_failed: int | None = None


ReaperCallbackResult: TypeAlias = int | InflightReapResult | None
ReaperCallback: TypeAlias = Callable[[], ReaperCallbackResult | Awaitable[ReaperCallbackResult]]
ErrorCallback: TypeAlias = Callable[[BaseException], None | Awaitable[None]]


def reap_stale_inflight(
    inflight: MutableMapping[str, Any],
    *,
    now: datetime | int | float | str | None = None,
    timeout_multiplier: float = 1.0,
    remove_malformed: bool = False,
) -> InflightReapResult:
    """Remove stale in-flight call entries from a process-local mapping.

    Entries are expected to be dicts with ``started_at`` and ``timeout_at``
    values. ``timeout_at`` determines the stale cutoff by default. When
    ``timeout_multiplier`` is greater than 1, valid ``started_at``/``timeout_at``
    pairs extend the cutoff to ``started_at + timeout * timeout_multiplier``;
    this supports Phase 9's planned "timeout x 2" cleanup without coupling this
    module to FastAPI or the Repository.

    Malformed entries are ignored by default so partially rolled-out call sites
    do not lose active requests. Set ``remove_malformed=True`` to purge entries
    that cannot produce a valid cutoff.
    """
    if timeout_multiplier < 1:
        raise ValueError("timeout_multiplier must be >= 1")

    current = _coerce_now(now)
    removed_ids: list[str] = []

    for request_id, entry in list(inflight.items()):
        stale_after = _stale_after(entry, timeout_multiplier=timeout_multiplier)
        if stale_after is None:
            if remove_malformed:
                inflight.pop(request_id, None)
                removed_ids.append(str(request_id))
            continue
        if current >= stale_after:
            inflight.pop(request_id, None)
            removed_ids.append(str(request_id))

    return InflightReapResult(removed_count=len(removed_ids), removed_ids=removed_ids)


# Short alias for call sites/tests that prefer the module name's context.
reap_inflight = reap_stale_inflight


async def run_reaper_once(
    *,
    session_reap: ReaperCallback | None = None,
    inflight_reap: ReaperCallback | None = None,
    stuck_job_cleanup: ReaperCallback | None = None,
) -> ReaperTickResult:
    """Run one reaper tick using callback-only dependencies.

    ``stuck_job_cleanup`` is intentionally a callback so this module never
    imports the Repository or DB layer.
    """
    sessions_removed = await _call_callback(session_reap)
    inflight_result = await _call_callback(inflight_reap)
    stuck_jobs_marked_failed = await _call_callback(stuck_job_cleanup)

    return ReaperTickResult(
        sessions_removed=_as_optional_int(sessions_removed),
        inflight=_as_inflight_result(inflight_result),
        stuck_jobs_marked_failed=_as_optional_int(stuck_jobs_marked_failed),
    )


async def run_reaper_loop(
    *,
    interval_seconds: float,
    session_reap: ReaperCallback | None = None,
    inflight_reap: ReaperCallback | None = None,
    stuck_job_cleanup: ReaperCallback | None = None,
    run_immediately: bool = True,
    on_error: ErrorCallback | None = None,
) -> None:
    """Run session/inflight/stuck-job reapers until cancelled.

    Cancellation is not swallowed: callers can cancel and await the task during
    application shutdown without leaving a pending task behind.
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0")

    async def tick() -> None:
        try:
            await run_reaper_once(
                session_reap=session_reap,
                inflight_reap=inflight_reap,
                stuck_job_cleanup=stuck_job_cleanup,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if on_error is None:
                raise
            await _maybe_await(on_error(exc))

    try:
        if run_immediately:
            await tick()
        while True:
            await asyncio.sleep(interval_seconds)
            await tick()
    except asyncio.CancelledError:
        raise


run_background_reaper_loop = run_reaper_loop


def _stale_after(entry: Any, *, timeout_multiplier: float) -> datetime | None:
    if not isinstance(entry, dict):
        return None

    timeout_at = _coerce_datetime(entry.get("timeout_at"))
    if timeout_at is None:
        return None

    started_at = _coerce_datetime(entry.get("started_at"))
    if timeout_multiplier == 1 or started_at is None:
        return timeout_at

    timeout_seconds = (timeout_at - started_at).total_seconds()
    if timeout_seconds <= 0:
        return timeout_at
    return started_at + timedelta(seconds=timeout_seconds * timeout_multiplier)


def _coerce_now(value: datetime | int | float | str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    coerced = _coerce_datetime(value)
    if coerced is None:
        raise ValueError("now must be a datetime, epoch seconds, or ISO datetime string")
    return coerced


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    if isinstance(value, bool):
        return None

    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(float(value), UTC)
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    return None


async def _call_callback(callback: ReaperCallback | None) -> ReaperCallbackResult:
    if callback is None:
        return None
    result = callback()
    if inspect.isawaitable(result):
        return await result
    return result


async def _maybe_await(value: None | Awaitable[None]) -> None:
    if inspect.isawaitable(value):
        await value


def _as_optional_int(value: ReaperCallbackResult) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return None


def _as_inflight_result(value: ReaperCallbackResult) -> InflightReapResult | None:
    if isinstance(value, InflightReapResult):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return InflightReapResult(removed_count=value, removed_ids=[])
    return None
