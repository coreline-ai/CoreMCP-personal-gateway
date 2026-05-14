from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from coremcp.mcp_gateway.reaper import InflightReapResult, reap_stale_inflight, run_reaper_loop


def test_reaper_helpers_are_exported_from_gateway_package() -> None:
    from coremcp.mcp_gateway import InflightReapResult as ExportedResult
    from coremcp.mcp_gateway import reap_inflight

    assert ExportedResult is InflightReapResult
    assert reap_inflight is reap_stale_inflight


def test_reap_stale_inflight_removes_stale_entry_and_reports_ids() -> None:
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    inflight = {
        "old-call": {
            "started_at": now - timedelta(seconds=120),
            "timeout_at": now - timedelta(seconds=60),
            "method": "tools/call",
        },
        "fresh-call": {
            "started_at": now - timedelta(seconds=10),
            "timeout_at": now + timedelta(seconds=20),
            "method": "tools/call",
        },
    }

    result = reap_stale_inflight(inflight, now=now)

    assert result == InflightReapResult(removed_count=1, removed_ids=["old-call"])
    assert "old-call" not in inflight
    assert "fresh-call" in inflight


def test_reap_stale_inflight_keeps_fresh_entry_with_timeout_multiplier() -> None:
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    inflight = {
        "past-timeout-but-inside-grace": {
            "started_at": now - timedelta(seconds=100),
            "timeout_at": now - timedelta(seconds=10),
            "method": "tools/call",
        },
        "past-double-timeout": {
            "started_at": now - timedelta(seconds=120),
            "timeout_at": now - timedelta(seconds=60),
            "method": "tools/call",
        },
    }

    result = reap_stale_inflight(inflight, now=now, timeout_multiplier=2.0)

    assert result.removed_count == 1
    assert result.removed_ids == ["past-double-timeout"]
    assert "past-timeout-but-inside-grace" in inflight
    assert "past-double-timeout" not in inflight


def test_reap_stale_inflight_defends_against_malformed_entries() -> None:
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    inflight = {
        "not-a-dict": object(),
        "missing-timeout": {"started_at": now - timedelta(hours=1)},
        "invalid-timeout": {"started_at": now - timedelta(hours=1), "timeout_at": "not-a-date"},
        "fresh-call": {"started_at": now - timedelta(seconds=1), "timeout_at": now + timedelta(seconds=1)},
    }

    result = reap_stale_inflight(inflight, now=now)

    assert result.removed_count == 0
    assert result.removed_ids == []
    assert set(inflight) == {"not-a-dict", "missing-timeout", "invalid-timeout", "fresh-call"}


def test_reap_stale_inflight_can_remove_malformed_entries_when_requested() -> None:
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    inflight = {
        "not-a-dict": object(),
        "invalid-timeout": {"started_at": now - timedelta(hours=1), "timeout_at": "not-a-date"},
        "fresh-call": {"started_at": now - timedelta(seconds=1), "timeout_at": now + timedelta(seconds=1)},
    }

    result = reap_stale_inflight(inflight, now=now, remove_malformed=True)

    assert result.removed_count == 2
    assert result.removed_ids == ["not-a-dict", "invalid-timeout"]
    assert set(inflight) == {"fresh-call"}


async def test_background_reaper_loop_runs_callbacks_and_is_cancel_safe() -> None:
    calls: list[str] = []

    async def session_reap() -> int:
        calls.append("session")
        return 1

    def inflight_reap() -> InflightReapResult:
        calls.append("inflight")
        return InflightReapResult(removed_count=0, removed_ids=[])

    async def stuck_job_cleanup() -> int:
        calls.append("jobs")
        return 2

    task = asyncio.create_task(
        run_reaper_loop(
            interval_seconds=0.01,
            session_reap=session_reap,
            inflight_reap=inflight_reap,
            stuck_job_cleanup=stuck_job_cleanup,
        )
    )
    await asyncio.sleep(0.02)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls[:3] == ["session", "inflight", "jobs"]
