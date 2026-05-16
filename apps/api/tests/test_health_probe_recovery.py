"""Regression test: mark_service_health_probe(ok=True) restores 'error' status.

Without status recovery, a service that once entered 'error' (e.g. transient
SSRF/network failure) would stay 'error' forever even after every probe
succeeds. Validate that a successful probe transitions error/auth_required
back to active while leaving admin-imposed states untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coremcp.db.repository import Repository


async def _create_service(repo: Repository, *, slug: str, status: str) -> str:
    service = await repo.create_mcp_service(
        name=f"svc-{slug}",
        slug=slug,
        endpoint_url="http://127.0.0.1:9000/mcp",
        auth_type="none",
    )
    if status != "draft":
        await repo.update_mcp_service(service["id"], {"status": status})
    return service["id"]


@pytest.mark.asyncio
async def test_probe_success_recovers_error_status(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "probe.sqlite3")
    await repo.connect()
    try:
        service_id = await _create_service(repo, slug="errsvc", status="error")
        await repo.mark_service_health_probe(service_id=service_id, ok=True)
        service = await repo.get_mcp_service(service_id)
        assert service is not None
        assert service["status"] == "active"
        assert service["consecutive_failures"] == 0
        assert service["circuit_open_until"] is None
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_probe_success_recovers_auth_required_status(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "probe2.sqlite3")
    await repo.connect()
    try:
        service_id = await _create_service(repo, slug="authsvc", status="auth_required")
        await repo.mark_service_health_probe(service_id=service_id, ok=True)
        service = await repo.get_mcp_service(service_id)
        assert service is not None
        assert service["status"] == "active"
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_probe_success_preserves_admin_disabled_status(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "probe3.sqlite3")
    await repo.connect()
    try:
        service_id = await _create_service(repo, slug="disabledsvc", status="disabled")
        await repo.mark_service_health_probe(service_id=service_id, ok=True)
        service = await repo.get_mcp_service(service_id)
        assert service is not None
        # Admin-imposed states must not be auto-recovered to active.
        assert service["status"] == "disabled"
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_probe_success_keeps_active_active(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "probe4.sqlite3")
    await repo.connect()
    try:
        service_id = await _create_service(repo, slug="okay", status="active")
        await repo.mark_service_health_probe(service_id=service_id, ok=True)
        service = await repo.get_mcp_service(service_id)
        assert service is not None
        assert service["status"] == "active"
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_probe_failure_does_not_change_status(tmp_path: Path) -> None:
    """Failure path must not bounce a healthy service to error directly; the
    circuit breaker / explicit validate flow owns the 'error' transition.
    """
    repo = Repository(tmp_path / "probe5.sqlite3")
    await repo.connect()
    try:
        service_id = await _create_service(repo, slug="failpath", status="active")
        await repo.mark_service_health_probe(
            service_id=service_id,
            ok=False,
            error_message="downstream timeout",
        )
        service = await repo.get_mcp_service(service_id)
        assert service is not None
        # Failure increments consecutive_failures but leaves status='active'
        # until the failure threshold opens the circuit / explicit validate.
        assert service["consecutive_failures"] == 1
        assert service["status"] == "active"
    finally:
        await repo.close()
