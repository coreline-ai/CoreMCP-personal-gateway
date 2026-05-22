"""STDIO MCP client lifecycle management (process pool).

Extracted from ``coremcp.main`` per ADR-042. These helpers manage the cache of
``StdioMcpClient`` instances keyed by service id, evict idle clients when the
configured ceiling is hit, and persist process-state snapshots back to the
repository. They run against the ``AppContext`` (which is just a typed view
over ``app.state``), so behaviour is identical to the pre-extraction code.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from coremcp.proxy.downstream import DownstreamMcpError
from coremcp.proxy.stdio import StdioCommandNotAllowedError, StdioMcpClient
from coremcp.runtime import AppContext

if TYPE_CHECKING:
    from fastapi import FastAPI


async def ensure_stdio_client_capacity_locked(
    app: FastAPI,
    *,
    service_key: str,
    clients: dict[str, tuple[tuple[Any, ...], StdioMcpClient]],
) -> None:
    """Evict idle clients until ``len(clients) < stdio_max_concurrent_processes``.

    Caller must already hold ``app.state.stdio_clients_lock``. Raises
    ``DownstreamMcpError`` (-32010) if the ceiling is 0 or no idle candidate
    can be evicted.
    """
    ctx = AppContext.from_app(app)
    max_processes = int(ctx.settings.stdio_max_concurrent_processes)
    if max_processes < 1:
        raise DownstreamMcpError(
            "CoreMCP stdio process capacity exceeded: maximum concurrent processes is 0",
            code=-32010,
        )

    while len(clients) >= max_processes:
        candidates: list[tuple[float, str, StdioMcpClient]] = []
        for key, (_, candidate) in clients.items():
            if key == service_key:
                continue
            snapshot = candidate.snapshot()
            if int(snapshot.get("pending_requests") or 0) > 0:
                continue
            candidates.append((_snapshot_sort_key(snapshot), key, candidate))

        if not candidates:
            raise DownstreamMcpError(
                "CoreMCP stdio process capacity exceeded and no idle stdio client can be evicted",
                code=-32010,
            )

        _, evicted_key, evicted_client = min(candidates, key=lambda item: item[0])
        clients.pop(evicted_key, None)
        await evicted_client.aclose()


async def close_stdio_client_for_service(app: FastAPI, service_id: str | None) -> None:
    """Drop a service's stdio client (if any) and close the child process."""
    if not service_id:
        return
    ctx = AppContext.from_app(app)
    clients = ctx.stdio_clients
    lock: asyncio.Lock | None = getattr(app.state, "stdio_clients_lock", None)
    if lock is None:
        entry = clients.pop(str(service_id), None)
        if entry is not None:
            await entry[1].aclose()
        return
    async with lock:
        entry = clients.pop(str(service_id), None)
    if entry is not None:
        await entry[1].aclose()


async def stdio_client_for_config(app: FastAPI, config: dict[str, Any]) -> StdioMcpClient:
    """Return a cached or freshly spawned stdio client matching ``config``."""
    # Lazy import to avoid module-level cycle with coremcp.main.
    from coremcp.main import _downstream_notification_callback, _stdio_signature

    ctx = AppContext.from_app(app)
    signature = _stdio_signature(config, ctx.settings)
    service_key = str(
        config.get("service_id")
        or config.get("id")
        or config.get("endpoint_url")
        or signature[0]
    )
    clients = ctx.stdio_clients
    lock: asyncio.Lock | None = getattr(app.state, "stdio_clients_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.stdio_clients_lock = lock
    async with lock:
        existing = clients.get(service_key)
        if existing is not None and existing[0] == signature:
            existing[1].notification_callback = _downstream_notification_callback(
                app,
                service_id=service_key,
                source="stdio",
            )
            return existing[1]
        if existing is not None:
            clients.pop(service_key, None)
            await existing[1].aclose()

        await ensure_stdio_client_capacity_locked(app, service_key=service_key, clients=clients)

        command = [str(signature[0]), *list(signature[1])]
        try:
            client = StdioMcpClient(
                command,
                cwd=signature[3],
                env=dict(signature[2]),
                timeout=float(ctx.settings.downstream_timeout_seconds),
                idle_timeout_seconds=int(signature[4]),
                max_response_bytes=ctx.settings.downstream_max_response_bytes,
                allowed_basenames=ctx.settings.stdio_allowed_command_set,
            )
        except StdioCommandNotAllowedError as exc:
            raise DownstreamMcpError(str(exc), code=-32602) from exc
        client.notification_callback = _downstream_notification_callback(
            app,
            service_id=service_key,
            source="stdio",
        )
        clients[service_key] = (signature, client)
        return client


async def persist_stdio_state(
    app: FastAPI, service_id: str | None, client: StdioMcpClient | None
) -> None:
    """Write the latest stdio snapshot back to ``mcp_services`` columns."""
    if not service_id or client is None:
        return
    ctx = AppContext.from_app(app)
    snapshot = client.snapshot()
    await ctx.repos.services.update_mcp_service(
        service_id,
        {
            "last_stdio_started_at": snapshot.get("started_at"),
            "last_stdio_used_at": snapshot.get("last_used_at"),
            "stdio_restart_count": int(snapshot.get("restart_count") or 0),
            "last_stdio_exit_code": snapshot.get("last_exit_code"),
            "last_stdio_error": snapshot.get("last_error"),
            "last_stdio_stderr_tail": snapshot.get("stderr_tail"),
        },
    )


def _snapshot_sort_key(snapshot: dict[str, Any]) -> float:
    value = snapshot.get("last_used_at") or snapshot.get("started_at") or 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "close_stdio_client_for_service",
    "ensure_stdio_client_capacity_locked",
    "persist_stdio_state",
    "stdio_client_for_config",
]
