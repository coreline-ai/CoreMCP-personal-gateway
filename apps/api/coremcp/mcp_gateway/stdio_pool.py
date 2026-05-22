"""STDIO MCP client lifecycle management (process pool).

Extracted from ``coremcp.main`` per ADR-042. These helpers manage the cache of
``StdioMcpClient`` instances keyed by service id, evict idle clients when the
configured ceiling is hit, and persist process-state snapshots back to the
repository. They run against the ``AppContext`` (which is just a typed view
over ``app.state``), so behaviour is identical to the pre-extraction code.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from coremcp.proxy.downstream import DownstreamMcpError
from coremcp.proxy.stdio import StdioCommandNotAllowedError, StdioMcpClient
from coremcp.runtime import AppContext
from coremcp.settings import Settings, get_settings

if TYPE_CHECKING:
    from fastapi import FastAPI, Request


BLOCKED_STDIO_ENV_KEYS = {
    "authorization",
    "coremcp_admin_token",
    "coremcp_client_token",
    "coremcp_admin_token_value",
}


def stdio_env(value: Any) -> dict[str, str]:
    """Filter an env dict to safe key/value pairs for stdio child processes."""
    if not isinstance(value, dict):
        return {}
    env: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            continue
        normalized = key.lower().replace("-", "_")
        if (
            normalized in BLOCKED_STDIO_ENV_KEYS
            or normalized.startswith("coremcp_admin_token")
            or normalized.startswith("coremcp_client_token")
            or "authorization" in normalized
        ):
            continue
        env[key] = item
    return env


def stdio_command_basename(command: str | None) -> str:
    return Path(str(command or "").strip()).name


def validate_stdio_runtime_config(
    command: str | None,
    cwd: str | None = None,
    *,
    settings: Settings | None = None,
) -> str | None:
    if not command or not command.strip():
        return "stdio_command is required for stdio transport"
    command_path = Path(command.strip()).expanduser()
    if not command_path.is_absolute():
        return "stdio_command must be an absolute path"
    allowed_basenames = (settings or get_settings()).stdio_allowed_command_set
    command_basename = command_path.name
    if command_basename not in allowed_basenames:
        allowed = ", ".join(sorted(allowed_basenames)) or "<none>"
        return f"stdio_command basename is not allowed: {command_basename} (allowed: {allowed})"
    if cwd and cwd.strip():
        cwd_path = Path(cwd.strip()).expanduser()
        if not cwd_path.is_absolute():
            return "stdio_cwd must be an absolute path"
        if not cwd_path.exists() or not cwd_path.is_dir():
            return "stdio_cwd must be an existing directory"
    return None


def stdio_default_idle_timeout(settings: Settings) -> int:
    return max(1, int(settings.stdio_default_idle_timeout_seconds))


def stdio_signature(config: dict[str, Any], settings: Settings | None = None) -> tuple[Any, ...]:
    """Derive the cache key tuple identifying a stdio runtime configuration."""
    from coremcp.main import _positive_int, _string_list  # avoid circular import at module load

    command = str(config.get("stdio_command") or "").strip()
    cwd = str(config.get("stdio_cwd") or "").strip() or None
    settings = settings or get_settings()
    validation_error = validate_stdio_runtime_config(command, cwd, settings=settings)
    if validation_error:
        raise DownstreamMcpError(validation_error, code=-32602)
    args = tuple(_string_list(config.get("stdio_args")))
    env = stdio_env(config.get("stdio_env"))
    idle_timeout = _positive_int(
        config.get("stdio_idle_timeout_seconds"),
        stdio_default_idle_timeout(settings),
    )
    return (command, args, tuple(sorted(env.items())), cwd, idle_timeout)


async def audit_stdio_command_rejected(
    request: Request,
    *,
    command: str | None,
    reason: str,
    service_id: str | None = None,
) -> None:
    """Append a single audit row when an stdio command basename is rejected."""
    from coremcp.main import correlation_id, request_ip  # avoid circular import at module load

    await request.app.state.repos.audit.log_audit(
        action="service.stdio_command_rejected",
        resource_type="mcp_service",
        resource_id=service_id,
        metadata={
            "command_basename": stdio_command_basename(command),
            "reason": reason,
        },
        request_id=correlation_id(request),
        ip=request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


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
    from coremcp.main import _downstream_notification_callback

    ctx = AppContext.from_app(app)
    signature = stdio_signature(config, ctx.settings)
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
    "BLOCKED_STDIO_ENV_KEYS",
    "audit_stdio_command_rejected",
    "close_stdio_client_for_service",
    "ensure_stdio_client_capacity_locked",
    "persist_stdio_state",
    "stdio_client_for_config",
    "stdio_command_basename",
    "stdio_default_idle_timeout",
    "stdio_env",
    "stdio_signature",
    "validate_stdio_runtime_config",
]
