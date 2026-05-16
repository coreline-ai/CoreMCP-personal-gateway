from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request

from coremcp.db import DEFAULT_TOOLBOX_ID
from coremcp.mcp.context import McpHandlerContext
from coremcp.mcp.catalog import active_toolbox_services
from coremcp.mcp.prompts import cached_prompt_to_mcp
from coremcp.proxy import DownstreamMcpError


@dataclass(slots=True)
class PromptsHandlerDeps:
    get_request_id: Callable[[dict[str, Any]], Any]
    jsonrpc_result: Callable[[Any, dict[str, Any]], dict[str, Any]]
    jsonrpc_error: Callable[..., dict[str, Any]]
    request_service_rpc: Callable[..., Awaitable[dict[str, Any]]]
    request_default_downstream_rpc: Callable[..., Awaitable[dict[str, Any]]]
    service_config_from_catalog_row: Callable[[dict[str, Any]], dict[str, Any]]
    correlation_id: Callable[[Request], str]


async def handle_prompts_list(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
    *,
    deps: PromptsHandlerDeps,
) -> dict[str, Any]:
    request_id = deps.get_request_id(payload)
    ctx = McpHandlerContext.from_app(app)
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    session_id = request.headers.get("Mcp-Session-Id")
    session = ctx.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    cached_prompts = await ctx.repos.catalog.list_catalog_prompts(DEFAULT_TOOLBOX_ID)
    if cached_prompts:
        return deps.jsonrpc_result(request_id, {"prompts": [cached_prompt_to_mcp(row) for row in cached_prompts], "nextCursor": None})
    services = await active_toolbox_services(app)
    if not services:
        try:
            response = await deps.request_default_downstream_rpc(
                app,
                method="prompts/list",
                params=params,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=deps.correlation_id(request),
            )
            result = response.get("result")
            return deps.jsonrpc_result(request_id, result if isinstance(result, dict) else {"prompts": [], "nextCursor": None})
        except DownstreamMcpError as exc:
            return deps.jsonrpc_error(request_id, exc.code, str(exc))

    prompts: list[dict[str, Any]] = []
    for service in services:
        try:
            response = await deps.request_service_rpc(
                app,
                service,
                method="prompts/list",
                params=params,
                request_id=f"{request_id}-{service['id']}-prompts-list",
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=deps.correlation_id(request),
            )
        except DownstreamMcpError as exc:
            if exc.code == -32601:
                continue
            continue
        result = response.get("result")
        items = result.get("prompts") if isinstance(result, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            prompt = dict(item)
            prompt["name"] = f"{service['slug']}.{item['name']}"
            prompts.append(prompt)
    return deps.jsonrpc_result(request_id, {"prompts": prompts, "nextCursor": None})


async def handle_prompts_get(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
    *,
    deps: PromptsHandlerDeps,
) -> dict[str, Any]:
    request_id = deps.get_request_id(payload)
    ctx = McpHandlerContext.from_app(app)
    params = payload.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        return deps.jsonrpc_error(request_id, -32602, "Invalid params")
    session_id = request.headers.get("Mcp-Session-Id")
    session = ctx.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    requested_name = str(params["name"])
    cached = await ctx.repos.catalog.get_catalog_prompt_by_exposed_name(requested_name, DEFAULT_TOOLBOX_ID)
    if cached is not None:
        downstream_params = dict(params)
        downstream_params["name"] = cached["name"]
        try:
            response = await deps.request_service_rpc(
                app,
                deps.service_config_from_catalog_row(cached),
                method="prompts/get",
                params=downstream_params,
                request_id=f"{request_id}-{cached['service_id']}-prompts-get",
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=deps.correlation_id(request),
            )
            result = response.get("result")
            return deps.jsonrpc_result(request_id, result if isinstance(result, dict) else {})
        except DownstreamMcpError as exc:
            return deps.jsonrpc_error(request_id, exc.code, str(exc))
    services = await active_toolbox_services(app)
    if not services:
        try:
            response = await deps.request_default_downstream_rpc(
                app,
                method="prompts/get",
                params=params,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=deps.correlation_id(request),
            )
            result = response.get("result")
            return deps.jsonrpc_result(request_id, result if isinstance(result, dict) else {})
        except DownstreamMcpError as exc:
            return deps.jsonrpc_error(request_id, exc.code, str(exc))

    candidates: list[tuple[dict[str, Any], str]] = []
    if "." in requested_name:
        service_slug, original_name = requested_name.split(".", 1)
        candidates = [(service, original_name) for service in services if service.get("slug") == service_slug]
    else:
        candidates = [(service, requested_name) for service in services]

    last_error: DownstreamMcpError | None = None
    for service, original_name in candidates:
        downstream_params = dict(params)
        downstream_params["name"] = original_name
        try:
            response = await deps.request_service_rpc(
                app,
                service,
                method="prompts/get",
                params=downstream_params,
                request_id=f"{request_id}-{service['id']}-prompts-get",
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=deps.correlation_id(request),
            )
            result = response.get("result")
            if isinstance(result, dict):
                return deps.jsonrpc_result(request_id, result)
        except DownstreamMcpError as exc:
            last_error = exc
            continue
    if last_error is not None and last_error.code not in {-32601, -32602}:
        return deps.jsonrpc_error(request_id, last_error.code, str(last_error))
    return deps.jsonrpc_error(request_id, -32602, "Unknown prompt")
