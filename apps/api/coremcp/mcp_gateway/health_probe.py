"""Periodic health-probe loop for registered MCP services.

Extracted from ``coremcp.main`` per ADR-042. The probe re-runs ``initialize``
against each non-archived service, refreshes stdio client state, detects tool
schema drift, and updates the circuit-breaker + health columns on the service
row. Behaviour is preserved verbatim.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from coremcp.mcp_gateway.stdio_pool import (
    persist_stdio_state,
    stdio_client_for_config,
)
from coremcp.runtime import AppContext

if TYPE_CHECKING:
    from fastapi import FastAPI


SERVICE_HEALTH_FAILURE_THRESHOLD = 3
SERVICE_HEALTH_CIRCUIT_OPEN_SECONDS = 30


async def probe_service_health(app: FastAPI, service: dict[str, Any]) -> tuple[bool, str | None]:
    """Run one ``initialize`` against ``service`` and update health columns."""
    # Lazy imports keep us off the main.py circular path.
    from coremcp.main import (
        _detect_service_tool_schema_drift,
        _record_downstream_failure,
        _request_service_rpc,
        _transport_type,
        validate_service,
    )

    ctx = AppContext.from_app(app)
    service_id = str(service.get("id") or "")
    if not service_id:
        return False, "missing service id"
    protocol_version = str(service.get("protocol_version") or "2025-11-25")
    timeout_seconds = max(0.1, float(ctx.settings.service_health_probe_timeout_seconds))
    timeout = httpx.Timeout(
        timeout_seconds,
        connect=min(float(ctx.settings.downstream_connect_timeout_seconds), timeout_seconds),
        read=timeout_seconds,
        write=timeout_seconds,
        pool=timeout_seconds,
    )
    try:
        await _request_service_rpc(
            app,
            service,
            method="initialize",
            params={
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "coremcp-health-probe", "version": ctx.settings.app_version},
            },
            request_id=f"health-{service_id}",
            protocol_version=protocol_version,
            correlation_id_value=f"health-probe-{service_id}",
            timeout=timeout,
            send_downstream_session=False,
        )
        if _transport_type(service) == "stdio":
            stdio_client = await stdio_client_for_config(app, service)
            await persist_stdio_state(app, service_id, stdio_client)
        if await _detect_service_tool_schema_drift(
            app,
            service,
            protocol_version=protocol_version,
            timeout=timeout,
        ):
            await validate_service(app, service_id, correlation_id_value=f"health-drift-{service_id}")
        ctx.circuit_breaker.record_success(service_id)
        await ctx.repos.services.mark_service_health_probe(service_id=service_id, ok=True)
        return True, None
    except Exception as exc:  # noqa: BLE001 - health probes must isolate failing services.
        _record_downstream_failure(app, service_id)
        await ctx.repos.services.mark_service_health_probe(
            service_id=service_id,
            ok=False,
            error_message=str(exc),
            circuit_open_seconds=SERVICE_HEALTH_CIRCUIT_OPEN_SECONDS,
            failure_threshold=SERVICE_HEALTH_FAILURE_THRESHOLD,
        )
        return False, str(exc)


async def run_service_health_probe_once(app: FastAPI) -> dict[str, Any]:
    """Probe every non-archived service exactly once."""
    ctx = AppContext.from_app(app)
    services = await ctx.repos.services.list_mcp_services(limit=500)
    candidates = [
        service
        for service in services
        if str(service.get("status") or "") in {"active", "error", "auth_required", "validating"}
    ]
    checked = 0
    failed = 0
    for service in candidates:
        checked += 1
        ok, _error = await probe_service_health(app, service)
        if not ok:
            failed += 1
    return {"checked": checked, "failed": failed}


async def run_service_health_probe_loop(app: FastAPI) -> None:
    """Background coroutine that calls :func:`run_service_health_probe_once` forever."""
    ctx = AppContext.from_app(app)
    interval = max(5.0, float(ctx.settings.service_health_probe_interval_seconds))
    while True:
        await asyncio.sleep(interval)
        try:
            await run_service_health_probe_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            # This loop is best-effort observability; API serving must continue.
            continue


__all__ = [
    "SERVICE_HEALTH_CIRCUIT_OPEN_SECONDS",
    "SERVICE_HEALTH_FAILURE_THRESHOLD",
    "probe_service_health",
    "run_service_health_probe_loop",
    "run_service_health_probe_once",
]
