from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request

from coremcp.db import DEFAULT_TOOLBOX_ID
from coremcp.mcp.context import McpHandlerContext
from coremcp.mcp.catalog import active_toolbox_services
from coremcp.mcp.resources import (
    cached_resource_template_to_mcp,
    cached_resource_to_mcp,
    truncate_resource_read_result,
    unambiguous_resource_rows,
)
from coremcp.proxy import DownstreamMcpError


@dataclass(slots=True)
class ResourcesHandlerDeps:
    get_request_id: Callable[[dict[str, Any]], Any]
    jsonrpc_result: Callable[[Any, dict[str, Any]], dict[str, Any]]
    jsonrpc_error: Callable[..., dict[str, Any]]
    request_service_rpc: Callable[..., Awaitable[dict[str, Any]]]
    request_default_downstream_rpc: Callable[..., Awaitable[dict[str, Any]]]
    service_config_from_catalog_row: Callable[[dict[str, Any]], dict[str, Any]]
    correlation_id: Callable[[Request], str]


async def handle_resources_list(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
    *,
    deps: ResourcesHandlerDeps,
    method: str = "resources/list",
    result_key: str = "resources",
) -> dict[str, Any]:
    request_id = deps.get_request_id(payload)
    ctx = McpHandlerContext.from_app(app)
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    session_id = request.headers.get("Mcp-Session-Id")
    session = ctx.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    if method == "resources/list":
        cached = await ctx.repos.catalog.list_catalog_resources(DEFAULT_TOOLBOX_ID)
        if cached:
            return deps.jsonrpc_result(
                request_id,
                {
                    "resources": [
                        cached_resource_to_mcp(row)
                        for row in unambiguous_resource_rows(cached)
                    ],
                    "nextCursor": None,
                },
            )
    elif method == "resources/templates/list":
        cached_templates = await ctx.repos.catalog.list_catalog_resource_templates(DEFAULT_TOOLBOX_ID)
        if cached_templates:
            return deps.jsonrpc_result(
                request_id,
                {
                    "resourceTemplates": [cached_resource_template_to_mcp(row) for row in cached_templates],
                    "nextCursor": None,
                },
            )
    services = await active_toolbox_services(app)
    if not services:
        try:
            response = await deps.request_default_downstream_rpc(
                app,
                method=method,
                params=params,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=deps.correlation_id(request),
            )
            result = response.get("result")
            return deps.jsonrpc_result(request_id, result if isinstance(result, dict) else {result_key: [], "nextCursor": None})
        except DownstreamMcpError as exc:
            return deps.jsonrpc_error(request_id, exc.code, str(exc))

    merged: list[dict[str, Any]] = []
    for service in services:
        try:
            response = await deps.request_service_rpc(
                app,
                service,
                method=method,
                params=params,
                request_id=f"{request_id}-{service['id']}-{method}",
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=deps.correlation_id(request),
            )
        except DownstreamMcpError as exc:
            if exc.code == -32601:
                continue
            continue
        result = response.get("result")
        items = result.get(result_key) if isinstance(result, dict) else None
        if isinstance(items, list):
            merged.extend(item for item in items if isinstance(item, dict))
    return deps.jsonrpc_result(request_id, {result_key: merged, "nextCursor": None})


async def handle_resources_read(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
    *,
    deps: ResourcesHandlerDeps,
) -> dict[str, Any]:
    request_id = deps.get_request_id(payload)
    ctx = McpHandlerContext.from_app(app)
    params = payload.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("uri"), str):
        return deps.jsonrpc_error(request_id, -32602, "Invalid params")
    session_id = request.headers.get("Mcp-Session-Id")
    session = ctx.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    cached = await ctx.repos.catalog.get_catalog_resource_by_uri(str(params["uri"]), DEFAULT_TOOLBOX_ID)
    if cached is not None:
        try:
            response = await deps.request_service_rpc(
                app,
                deps.service_config_from_catalog_row(cached),
                method="resources/read",
                params=params,
                request_id=f"{request_id}-{cached['service_id']}-resource-read",
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=deps.correlation_id(request),
            )
            result = response.get("result")
            return deps.jsonrpc_result(request_id, truncate_resource_read_result(result) if isinstance(result, dict) else {})
        except DownstreamMcpError as exc:
            return deps.jsonrpc_error(request_id, exc.code, str(exc))
    services = await active_toolbox_services(app)
    if not services:
        try:
            response = await deps.request_default_downstream_rpc(
                app,
                method="resources/read",
                params=params,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=deps.correlation_id(request),
            )
            result = response.get("result")
            return deps.jsonrpc_result(request_id, truncate_resource_read_result(result) if isinstance(result, dict) else {})
        except DownstreamMcpError as exc:
            return deps.jsonrpc_error(request_id, exc.code, str(exc))
    return deps.jsonrpc_error(request_id, -32602, "Unknown resource")
