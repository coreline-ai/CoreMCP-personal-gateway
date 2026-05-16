from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request

from coremcp.mcp.context import McpHandlerContext

DispatchResult = tuple[dict[str, Any] | None, str | None]


@dataclass(slots=True)
class McpDispatchHandlers:
    jsonrpc_version: str
    get_request_id: Callable[[dict[str, Any]], Any]
    jsonrpc_error: Callable[..., dict[str, Any]]
    jsonrpc_result: Callable[[Any, dict[str, Any]], dict[str, Any]]
    has_scope: Callable[[Request, str], bool]
    scope_denied_response: Callable[..., Awaitable[dict[str, Any]]]
    request_ip: Callable[[Request], str | None]
    handle_initialize: Callable[[FastAPI, dict[str, Any], Request], Awaitable[DispatchResult]]
    forward_downstream_cancellation: Callable[..., Awaitable[None]]
    handle_tools_list: Callable[[FastAPI, dict[str, Any], Request], Awaitable[dict[str, Any]]]
    handle_tools_call: Callable[[FastAPI, dict[str, Any], Request], Awaitable[dict[str, Any]]]
    handle_resources_list: Callable[..., Awaitable[dict[str, Any]]]
    handle_resources_read: Callable[[FastAPI, dict[str, Any], Request], Awaitable[dict[str, Any]]]
    handle_prompts_list: Callable[[FastAPI, dict[str, Any], Request], Awaitable[dict[str, Any]]]
    handle_prompts_get: Callable[[FastAPI, dict[str, Any], Request], Awaitable[dict[str, Any]]]


async def dispatch_mcp(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
    *,
    handlers: McpDispatchHandlers,
) -> DispatchResult:
    request_id = handlers.get_request_id(payload)
    ctx = McpHandlerContext.from_app(app)
    if payload.get("jsonrpc") != handlers.jsonrpc_version or not isinstance(payload.get("method"), str):
        return handlers.jsonrpc_error(request_id, -32600, "Invalid Request"), None

    method = payload["method"]
    if method == "initialize":
        return await handlers.handle_initialize(app, payload, request)
    if method == "notifications/initialized":
        ctx.sessions.mark_initialized(request.headers.get("Mcp-Session-Id"))
        return None, None
    if method == "notifications/cancelled":
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        await handlers.forward_downstream_cancellation(app, request, params=params, request_id=request_id)
        await ctx.repos.audit.log_invocation(
            session_id=request.headers.get("Mcp-Session-Id"),
            method="notifications/cancelled",
            tool_name=None,
            status="cancelled",
            request_id=str(params.get("requestId") or request_id or "cancelled"),
            error_message=params.get("reason") if isinstance(params.get("reason"), str) else None,
            client_ip=handlers.request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return None, None
    if method == "ping":
        return handlers.jsonrpc_result(request_id, {}), None
    if method == "tools/list":
        if not handlers.has_scope(request, "mcp:tools.read"):
            return await handlers.scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await handlers.handle_tools_list(app, payload, request), None
    if method == "tools/call":
        if not handlers.has_scope(request, "mcp:tools.call"):
            return await handlers.scope_denied_response(app, payload, request, required_scope="mcp:tools.call"), None
        return await handlers.handle_tools_call(app, payload, request), None
    if method == "resources/list":
        if not handlers.has_scope(request, "mcp:tools.read"):
            return await handlers.scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await handlers.handle_resources_list(app, payload, request), None
    if method == "resources/templates/list":
        if not handlers.has_scope(request, "mcp:tools.read"):
            return await handlers.scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await handlers.handle_resources_list(app, payload, request, method="resources/templates/list", result_key="resourceTemplates"), None
    if method == "resources/read":
        if not handlers.has_scope(request, "mcp:tools.read"):
            return await handlers.scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await handlers.handle_resources_read(app, payload, request), None
    if method == "prompts/list":
        if not handlers.has_scope(request, "mcp:tools.read"):
            return await handlers.scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await handlers.handle_prompts_list(app, payload, request), None
    if method == "prompts/get":
        if not handlers.has_scope(request, "mcp:tools.read"):
            return await handlers.scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await handlers.handle_prompts_get(app, payload, request), None
    return handlers.jsonrpc_error(request_id, -32601, "Method not found"), None


async def dispatch_mcp_batch(
    app: FastAPI,
    payloads: list[Any],
    request: Request,
    *,
    handlers: McpDispatchHandlers,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Dispatch a JSON-RPC 2.0 batch request sequentially.

    JSON-RPC notifications do not produce response items. Sequential dispatch
    keeps side-effect ordering deterministic for initialize/cancel/tools calls.
    """

    responses: list[dict[str, Any]] = []
    new_session_id: str | None = None
    for item in payloads:
        if not isinstance(item, dict):
            responses.append(handlers.jsonrpc_error(None, -32600, "Invalid Request"))
            continue
        response_payload, item_session_id = await dispatch_mcp(app, item, request, handlers=handlers)
        if item_session_id and new_session_id is None:
            new_session_id = item_session_id
        if response_payload is not None:
            responses.append(response_payload)
    return (responses or None), new_session_id
