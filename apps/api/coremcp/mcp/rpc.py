from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from coremcp.proxy import DownstreamMcpClient, StdioMcpClient, UrlSafetyChecker
from coremcp.settings import Settings


NotificationCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
SessionCallback = Callable[[str], Awaitable[None] | None]
RpcTimeout = httpx.Timeout | float | None


def stdio_timeout_seconds(timeout: RpcTimeout) -> float | None:
    if timeout is None:
        return None
    if isinstance(timeout, httpx.Timeout):
        value = timeout.read
        return float(value) if value is not None else None
    return float(timeout)


@dataclass(frozen=True)
class RpcHelperDeps:
    """Explicit dependencies for MCP downstream RPC helper functions."""

    settings: Settings
    downstream: DownstreamMcpClient
    stdio_client_for_config: Callable[[dict[str, Any]], Awaitable[StdioMcpClient]]
    downstream_headers_for_service: Callable[[str | None], Awaitable[dict[str, str]]]
    downstream_session_id: Callable[[str | None], str | None]
    downstream_session_callback: Callable[[str | None], SessionCallback]
    downstream_notification_callback: Callable[[str | None, str], NotificationCallback]


def service_transport_type(config: dict[str, Any]) -> str:
    transport = str(config.get("transport_type") or "http").lower()
    return "stdio" if transport == "stdio" else "http"


async def request_service_rpc(
    deps: RpcHelperDeps,
    service: dict[str, Any],
    *,
    method: str,
    params: dict[str, Any] | None,
    request_id: Any,
    protocol_version: str | None,
    session_id: str | None = None,
    correlation_id_value: str | None = None,
    timeout: RpcTimeout = None,
    send_downstream_session: bool = True,
) -> dict[str, Any]:
    service_id = str(service.get("id") or service.get("service_id") or "")
    if service_transport_type(service) == "stdio":
        client = await deps.stdio_client_for_config(service)
        return await client.request(
            method=method,
            params=params or {},
            request_id=request_id,
            protocol_version=protocol_version,
            session_id=session_id,
            correlation_id=correlation_id_value,
            timeout=stdio_timeout_seconds(timeout),
        )

    checker = UrlSafetyChecker(deps.settings)
    endpoint_url = str(service["endpoint_url"])
    safety_result = checker.assert_safe(endpoint_url)
    return await deps.downstream.request(
        method=method,
        params=params or {},
        request_id=request_id,
        protocol_version=protocol_version,
        session_id=deps.downstream_session_id(service_id) if send_downstream_session else None,
        url=endpoint_url,
        downstream_headers=await deps.downstream_headers_for_service(service_id),
        url_safety_checker=checker,
        safety_result=safety_result,
        correlation_id=correlation_id_value,
        session_id_callback=deps.downstream_session_callback(service_id),
        notification_callback=deps.downstream_notification_callback(service_id, "http"),
        timeout=timeout,
    )


async def request_default_downstream_rpc(
    deps: RpcHelperDeps,
    *,
    method: str,
    params: dict[str, Any] | None,
    request_id: Any,
    protocol_version: str | None,
    session_id: str | None,
    correlation_id_value: str | None,
    timeout: RpcTimeout = None,
    send_downstream_session: bool = True,
) -> dict[str, Any]:
    return await deps.downstream.request(
        method=method,
        params=params or {},
        request_id=request_id,
        protocol_version=protocol_version,
        session_id=deps.downstream_session_id(None) if send_downstream_session else None,
        correlation_id=correlation_id_value,
        session_id_callback=deps.downstream_session_callback(None),
        notification_callback=deps.downstream_notification_callback(None, "http"),
        timeout=timeout,
    )
