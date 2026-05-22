from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request

from coremcp.db import DEFAULT_TOOLBOX_ID
from coremcp.mcp.args_validator import validate_tool_arguments
from coremcp.mcp.catalog import toolbox_unavailable_services
from coremcp.mcp.context import McpHandlerContext
from coremcp.plugins import PluginExecutionError, ToolCallContext
from coremcp.proxy import (
    CircuitOpenError,
    DownstreamMcpClient,
    DownstreamMcpError,
    DownstreamTimeoutError,
    DownstreamToolError,
    StdioMcpClient,
    UrlSafetyChecker,
    UrlSafetyError,
)
from coremcp.registry.catalog import catalog_row_to_mcp_tool, slugify_tool_name


@dataclass(slots=True)
class ToolsHandlerDeps:
    get_request_id: Callable[[dict[str, Any]], Any]
    jsonrpc_result: Callable[[Any, dict[str, Any]], dict[str, Any]]
    jsonrpc_error: Callable[..., dict[str, Any]]
    request_ip: Callable[[Request], str | None]
    correlation_id: Callable[[Request], str]
    downstream_session_id: Callable[[FastAPI, str | None], str | None]
    downstream_session_callback: Callable[..., Callable[[str], Awaitable[None]]]
    downstream_notification_callback: Callable[..., Callable[[dict[str, Any]], Awaitable[None]]]
    tool_error_result: Callable[..., dict[str, Any]]
    idempotency_cache_key: Callable[[Request, str], str | None]
    check_service_rate_limit: Callable[..., Any]
    rate_limit_tool_error: Callable[[int | None], dict[str, Any]]
    transport_type: Callable[[dict[str, Any]], str]
    stdio_client_for_config: Callable[[FastAPI, dict[str, Any]], Awaitable[StdioMcpClient]]
    downstream_headers_for_service: Callable[[FastAPI, str | None], Awaitable[dict[str, str]]]
    idempotency_downstream_header: Callable[[Request], dict[str, str]]
    record_downstream_failure: Callable[[FastAPI, str], None]
    persist_stdio_state: Callable[[FastAPI, str | None, StdioMcpClient | None], Awaitable[None]]


def _ctx(app: FastAPI) -> McpHandlerContext:
    return McpHandlerContext.from_app(app)


async def _log_plugin_failure(
    app: FastAPI,
    request: Request,
    *,
    deps: ToolsHandlerDeps,
    exc: PluginExecutionError,
    request_id: Any,
    request_log_id: str,
    session_id: str | None,
    protocol_version: str | None,
    exposed_name: str,
    route: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    metadata = {
        "tool": exposed_name,
        "plugin_name": exc.plugin_name,
        "stage": exc.stage,
        "error_type": exc.cause.__class__.__name__,
    }
    await _ctx(app).repos.audit.log_audit(
        action="plugin.error",
        resource_type="service_tool",
        resource_id=route.get("service_tool_id"),
        metadata=metadata,
        request_id=request_log_id,
        ip=deps.request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await _ctx(app).repos.audit.log_invocation(
        session_id=session_id,
        method="tools/call",
        tool_name=exposed_name,
        status="policy_denied",
        error_code="plugin_error",
        latency_ms=int((time.perf_counter() - started) * 1000),
        request_id=request_log_id,
        service_id=route.get("service_id"),
        service_tool_id=route.get("service_tool_id"),
        downstream_tool_name=route.get("original_name"),
        error_message=f"{exc.plugin_name}:{exc.stage}",
        protocol_version=protocol_version,
        client_ip=deps.request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return deps.jsonrpc_result(
        request_id,
        deps.tool_error_result(
            "plugin_error",
            "CoreMCP plugin policy failed; tool call was blocked",
            reason=f"{exc.stage}_failed",
        ),
    )


def normalize_downstream_tool(tool: dict[str, Any]) -> tuple[dict[str, Any], str]:
    original_name = str(tool.get("name", "")).strip()
    exposed_name = f"fake.{slugify_tool_name(original_name)}"
    normalized = dict(tool)
    normalized["name"] = exposed_name
    return normalized, original_name


async def refresh_tools(
    app: FastAPI,
    *,
    deps: ToolsHandlerDeps,
    request_id: Any,
    protocol_version: str | None,
    session_id: str | None,
    params: dict[str, Any] | None = None,
    correlation_id_value: str | None = None,
) -> dict[str, Any]:
    catalog_rows = await _ctx(app).repos.catalog.get_catalog_tools(DEFAULT_TOOLBOX_ID)
    if catalog_rows:
        registry: dict[str, dict[str, Any]] = {}
        tools: list[dict[str, Any]] = []
        for row in catalog_rows:
            tool = catalog_row_to_mcp_tool(row)
            registry[tool["name"]] = {
                "original_name": row["original_name"],
                "endpoint_url": row["endpoint_url"],
                "transport_type": row.get("transport_type") or "http",
                "stdio_command": row.get("stdio_command"),
                "stdio_args": row.get("stdio_args") or [],
                "stdio_env": row.get("stdio_env") or {},
                "stdio_cwd": row.get("stdio_cwd"),
                "stdio_idle_timeout_seconds": row.get("stdio_idle_timeout_seconds"),
                "input_schema_json": row.get("input_schema_json") or {"type": "object"},
                "schema_hash": row.get("schema_hash"),
                "service_id": row["service_id"],
                "service_tool_id": row["service_tool_id"],
                "override_enabled": row.get("override_enabled", 1),
                "permission_level": row.get("permission_level", "callable"),
            }
            if bool(row.get("override_enabled", 1)) and row.get("permission_level", "callable") != "hidden":
                tools.append(tool)
        _ctx(app).set_tool_registry(registry)
        result_payload: dict[str, Any] = {"tools": tools, "nextCursor": None}
        unavailable = await toolbox_unavailable_services(app, DEFAULT_TOOLBOX_ID)
        if unavailable:
            result_payload["_meta"] = {"coremcp": {"unavailable_services": unavailable}}
        return result_payload

    downstream: DownstreamMcpClient = _ctx(app).downstream
    response = await downstream.request(
        method="tools/list",
        params=params or {},
        request_id=request_id,
        protocol_version=protocol_version,
        session_id=deps.downstream_session_id(app, None),
        correlation_id=correlation_id_value,
        session_id_callback=deps.downstream_session_callback(app, None),
        notification_callback=deps.downstream_notification_callback(app, source="http"),
    )
    result = response.get("result")
    if not isinstance(result, dict):
        raise DownstreamMcpError("downstream tools/list returned invalid result")

    transformed_tools: list[dict[str, Any]] = []
    registry = {}
    for tool in result.get("tools", []):
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        transformed, original_name = normalize_downstream_tool(tool)
        transformed_tools.append(transformed)
        registry[transformed["name"]] = {
            "original_name": original_name,
            "endpoint_url": _ctx(app).settings.fake_mcp_url,
            "transport_type": "http",
            "service_id": None,
            "service_tool_id": None,
            "input_schema_json": tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object"},
        }

    _ctx(app).set_tool_registry(registry)
    result = dict(result)
    result["tools"] = transformed_tools
    result.setdefault("nextCursor", None)
    return result


async def handle_tools_list(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
    *,
    deps: ToolsHandlerDeps,
) -> dict[str, Any]:
    started = time.perf_counter()
    session_id = request.headers.get("Mcp-Session-Id")
    session = _ctx(app).sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    request_log_id = deps.correlation_id(request)
    try:
        result = await refresh_tools(
            app,
            deps=deps,
            request_id=deps.get_request_id(payload),
            protocol_version=protocol_version,
            session_id=session_id,
            params=payload.get("params") if isinstance(payload.get("params"), dict) else {},
            correlation_id_value=deps.correlation_id(request),
        )
        await _ctx(app).repos.audit.log_invocation(
            session_id=session_id,
            method="tools/list",
            tool_name=None,
            status="success",
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            protocol_version=protocol_version,
            client_ip=deps.request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return deps.jsonrpc_result(deps.get_request_id(payload), result)
    except DownstreamMcpError as exc:
        await _ctx(app).repos.audit.log_invocation(
            session_id=session_id,
            method="tools/list",
            tool_name=None,
            status="error",
            error_code=exc.code,
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            error_message=str(exc),
            protocol_version=protocol_version,
            client_ip=deps.request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return deps.jsonrpc_error(deps.get_request_id(payload), exc.code, str(exc))


@dataclass(slots=True)
class ToolCallRuntime:
    started: float
    request_id: Any
    params: dict[str, Any]
    session_id: str | None
    protocol_version: str | None
    request_log_id: str
    exposed_name: str
    idempotency_key: str | None


@dataclass(slots=True)
class DownstreamToolExecutionState:
    stdio_client_for_call: StdioMcpClient | None = None


def _tool_call_latency_ms(runtime: ToolCallRuntime) -> int:
    return int((time.perf_counter() - runtime.started) * 1000)


async def _log_tool_call_invocation(
    app: FastAPI,
    request: Request,
    *,
    deps: ToolsHandlerDeps,
    runtime: ToolCallRuntime,
    status: str,
    route: dict[str, Any] | None = None,
    error_code: int | str | None = None,
    error_message: str | None = None,
    idempotency_key: str | None = None,
) -> str:
    route = route or {}
    return await _ctx(app).repos.audit.log_invocation(
        session_id=runtime.session_id,
        method="tools/call",
        tool_name=runtime.exposed_name,
        status=status,
        error_code=error_code,
        latency_ms=_tool_call_latency_ms(runtime),
        request_id=runtime.request_log_id,
        service_id=route.get("service_id"),
        service_tool_id=route.get("service_tool_id"),
        downstream_tool_name=route.get("original_name"),
        error_message=error_message,
        protocol_version=runtime.protocol_version,
        idempotency_key=idempotency_key,
        client_ip=deps.request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


def _prepare_tool_call_runtime(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
    *,
    deps: ToolsHandlerDeps,
    started: float,
) -> tuple[ToolCallRuntime | None, dict[str, Any] | None]:
    request_id = deps.get_request_id(payload)
    params = payload.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        return None, deps.jsonrpc_error(request_id, -32602, "Invalid params")

    session_id = request.headers.get("Mcp-Session-Id")
    session = _ctx(app).sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    exposed_name = params["name"]
    return (
        ToolCallRuntime(
            started=started,
            request_id=request_id,
            params=params,
            session_id=session_id,
            protocol_version=protocol_version,
            request_log_id=deps.correlation_id(request),
            exposed_name=exposed_name,
            idempotency_key=deps.idempotency_cache_key(request, exposed_name),
        ),
        None,
    )


async def _try_tool_call_idempotency_hit(
    app: FastAPI,
    request: Request,
    *,
    deps: ToolsHandlerDeps,
    runtime: ToolCallRuntime,
) -> dict[str, Any] | None:
    cached_response = _ctx(app).idempotency_cache.get(runtime.idempotency_key)
    if cached_response is None:
        return None

    # Safe to mutate: IdempotencyCache.get() returns a deepcopy.
    cached_response["id"] = runtime.request_id
    await _log_tool_call_invocation(
        app,
        request,
        deps=deps,
        runtime=runtime,
        status="success",
        idempotency_key=request.headers.get("Idempotency-Key"),
    )
    return cached_response


async def _resolve_tool_call_route(
    app: FastAPI,
    request: Request,
    *,
    deps: ToolsHandlerDeps,
    runtime: ToolCallRuntime,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    registry: dict[str, dict[str, Any]] = _ctx(app).tool_registry
    if runtime.exposed_name not in registry:
        try:
            await refresh_tools(
                app,
                deps=deps,
                request_id=runtime.request_id,
                protocol_version=runtime.protocol_version,
                session_id=runtime.session_id,
                correlation_id_value=deps.correlation_id(request),
            )
            registry = _ctx(app).tool_registry
        except DownstreamMcpError as exc:
            await _log_tool_call_invocation(
                app,
                request,
                deps=deps,
                runtime=runtime,
                status="error",
                error_code=exc.code,
                error_message=str(exc),
            )
            return None, deps.jsonrpc_error(runtime.request_id, exc.code, str(exc))

    route = registry.get(runtime.exposed_name)
    if route is not None:
        return route, None

    await _log_tool_call_invocation(
        app,
        request,
        deps=deps,
        runtime=runtime,
        status="error",
        error_code=-32602,
        error_message="Unknown tool",
    )
    return None, deps.jsonrpc_error(runtime.request_id, -32602, "Unknown tool")


async def _enforce_tool_permission(
    app: FastAPI,
    request: Request,
    *,
    deps: ToolsHandlerDeps,
    runtime: ToolCallRuntime,
    route: dict[str, Any],
) -> dict[str, Any] | None:
    permission_level = str(route.get("permission_level") or "callable")
    override_enabled = bool(route.get("override_enabled", 1))
    if override_enabled and permission_level == "callable":
        return None

    reason = "tool_disabled" if not override_enabled or permission_level == "hidden" else f"tool_permission_{permission_level}"
    await _ctx(app).repos.audit.log_audit(
        action="policy.deny",
        resource_type="service_tool",
        resource_id=route.get("service_tool_id"),
        metadata={"tool": runtime.exposed_name, "reason": reason, "permission_level": permission_level, "enabled": override_enabled},
        request_id=runtime.request_log_id,
        ip=deps.request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await _log_tool_call_invocation(
        app,
        request,
        deps=deps,
        runtime=runtime,
        route=route,
        status="policy_denied",
        error_code="tool_permission_denied",
        error_message=reason,
    )
    return deps.jsonrpc_result(
        runtime.request_id,
        deps.tool_error_result(
            "policy_denied",
            f"Tool call denied by CoreMCP toolbox policy: {reason}",
            reason=reason,
        ),
    )


async def _validate_tool_call_arguments(
    app: FastAPI,
    request: Request,
    *,
    deps: ToolsHandlerDeps,
    runtime: ToolCallRuntime,
    route: dict[str, Any],
    arguments: Any,
) -> dict[str, Any] | None:
    schema_error = validate_tool_arguments(route.get("input_schema_json"), arguments)
    if schema_error is None:
        return None

    await _ctx(app).repos.audit.log_audit(
        action="policy.invalid_args",
        resource_type="service_tool",
        resource_id=route.get("service_tool_id"),
        metadata={"tool": runtime.exposed_name, "error": schema_error, "schema_hash": route.get("schema_hash")},
        request_id=runtime.request_log_id,
        ip=deps.request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await _log_tool_call_invocation(
        app,
        request,
        deps=deps,
        runtime=runtime,
        route=route,
        status="policy_denied",
        error_code="invalid_args",
        error_message=schema_error,
    )
    return deps.jsonrpc_error(
        runtime.request_id,
        -32602,
        "Invalid tool arguments",
        {"details": schema_error},
    )


def _tool_call_plugin_context(request: Request, *, runtime: ToolCallRuntime, route: dict[str, Any]) -> ToolCallContext:
    return ToolCallContext(
        request_id=runtime.request_log_id,
        session_id=runtime.session_id,
        service_id=route.get("service_id") if isinstance(route.get("service_id"), str) else None,
        service_tool_id=route.get("service_tool_id") if isinstance(route.get("service_tool_id"), str) else None,
        exposed_name=runtime.exposed_name,
        downstream_name=route.get("original_name") if isinstance(route.get("original_name"), str) else None,
        auth_kind=getattr(request.state, "mcp_auth_kind", None),
    )


async def _apply_before_tool_plugins(
    app: FastAPI,
    request: Request,
    *,
    deps: ToolsHandlerDeps,
    runtime: ToolCallRuntime,
    route: dict[str, Any],
    arguments: Any,
) -> tuple[Any, ToolCallContext | None, dict[str, Any] | None]:
    plugin_context = _tool_call_plugin_context(request, runtime=runtime, route=route)
    try:
        return await _ctx(app).plugins.before_tool_call(plugin_context, arguments), plugin_context, None
    except PluginExecutionError as exc:
        return None, plugin_context, await _log_plugin_failure(
            app,
            request,
            deps=deps,
            exc=exc,
            request_id=runtime.request_id,
            request_log_id=runtime.request_log_id,
            session_id=runtime.session_id,
            protocol_version=runtime.protocol_version,
            exposed_name=runtime.exposed_name,
            route=route,
            started=runtime.started,
        )


async def _check_tool_service_resilience(
    app: FastAPI,
    request: Request,
    *,
    deps: ToolsHandlerDeps,
    runtime: ToolCallRuntime,
    route: dict[str, Any],
    service_id: str,
) -> dict[str, Any] | None:
    if not service_id:
        return None

    if decision := deps.check_service_rate_limit(
        app,
        service_id=service_id,
        method="tools/call",
        tool_name=runtime.exposed_name,
    ):
        await _ctx(app).repos.audit.log_audit(
            action="rate_limit.exceeded",
            resource_type="mcp_service",
            resource_id=service_id,
            metadata={"tool": runtime.exposed_name, "route": "tools/call", "retry_after_seconds": decision.retry_after_seconds},
            request_id=runtime.request_log_id,
            ip=deps.request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        await _log_tool_call_invocation(
            app,
            request,
            deps=deps,
            runtime=runtime,
            route=route,
            status="rate_limited",
            error_code="service_rate_limited",
            error_message="service_rate_limit_exceeded",
        )
        return deps.jsonrpc_result(runtime.request_id, deps.rate_limit_tool_error(decision.retry_after_seconds))

    try:
        _ctx(app).circuit_breaker.before_request(service_id)
    except CircuitOpenError as exc:
        await _log_tool_call_invocation(
            app,
            request,
            deps=deps,
            runtime=runtime,
            route=route,
            status="error",
            error_code="circuit_open",
            error_message=str(exc),
        )
        return deps.jsonrpc_result(
            runtime.request_id,
            deps.tool_error_result(
                "circuit_open",
                "Downstream service circuit is temporarily open",
                retry_after_seconds=exc.retry_after_seconds,
            ),
        )
    return None


async def _execute_downstream_tool_call(
    app: FastAPI,
    request: Request,
    *,
    deps: ToolsHandlerDeps,
    runtime: ToolCallRuntime,
    route: dict[str, Any],
    downstream_params: dict[str, Any],
    state: DownstreamToolExecutionState,
) -> dict[str, Any]:
    inflight_key = str(runtime.request_id)
    inflight_started_at = time.time()
    inflight_timeout_at = inflight_started_at + float(_ctx(app).settings.downstream_timeout_seconds)
    if deps.transport_type(route) == "stdio":
        stdio_client = await deps.stdio_client_for_config(app, route)
        state.stdio_client_for_call = stdio_client
        _ctx(app).inflight_downstream_calls[inflight_key] = {
            **route,
            "transport_type": "stdio",
            "method": "tools/call",
            "started_at": inflight_started_at,
            "timeout_at": inflight_timeout_at,
            "service_id": route.get("service_id"),
            "session_id": runtime.session_id,
            "protocol_version": runtime.protocol_version,
        }
        return await stdio_client.request(
            method="tools/call",
            params=downstream_params,
            request_id=runtime.request_id,
            protocol_version=runtime.protocol_version,
            session_id=runtime.session_id,
            correlation_id=deps.correlation_id(request),
        )

    checker = UrlSafetyChecker(_ctx(app).settings)
    safety_result = checker.assert_safe(route["endpoint_url"])
    downstream_headers = await deps.downstream_headers_for_service(app, route.get("service_id"))
    downstream_headers.update(deps.idempotency_downstream_header(request))
    downstream_session_id = deps.downstream_session_id(
        app,
        route.get("service_id") if isinstance(route.get("service_id"), str) else None,
    )
    _ctx(app).inflight_downstream_calls[inflight_key] = {
        "url": route["endpoint_url"],
        "transport_type": "http",
        "method": "tools/call",
        "started_at": inflight_started_at,
        "timeout_at": inflight_timeout_at,
        "service_id": route.get("service_id"),
        "session_id": downstream_session_id,
        "protocol_version": runtime.protocol_version,
        "downstream_headers": downstream_headers,
    }
    return await _ctx(app).downstream.request(
        method="tools/call",
        params=downstream_params,
        request_id=runtime.request_id,
        protocol_version=runtime.protocol_version,
        session_id=downstream_session_id,
        url=route["endpoint_url"],
        downstream_headers=downstream_headers,
        url_safety_checker=checker,
        safety_result=safety_result,
        correlation_id=deps.correlation_id(request),
        session_id_callback=deps.downstream_session_callback(
            app,
            route.get("service_id") if isinstance(route.get("service_id"), str) else None,
        ),
        notification_callback=deps.downstream_notification_callback(
            app,
            service_id=route.get("service_id") if isinstance(route.get("service_id"), str) else None,
            source="http",
        ),
    )


async def handle_tools_call(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
    *,
    deps: ToolsHandlerDeps,
) -> dict[str, Any]:
    started = time.perf_counter()
    runtime, error_response = _prepare_tool_call_runtime(app, payload, request, deps=deps, started=started)
    if error_response is not None or runtime is None:
        assert error_response is not None
        return error_response

    if cached_response := await _try_tool_call_idempotency_hit(app, request, deps=deps, runtime=runtime):
        return cached_response

    route, error_response = await _resolve_tool_call_route(app, request, deps=deps, runtime=runtime)
    if error_response is not None or route is None:
        assert error_response is not None
        return error_response

    if policy_response := await _enforce_tool_permission(app, request, deps=deps, runtime=runtime, route=route):
        return policy_response

    arguments = runtime.params.get("arguments", {})
    if invalid_args_response := await _validate_tool_call_arguments(
        app,
        request,
        deps=deps,
        runtime=runtime,
        route=route,
        arguments=arguments,
    ):
        return invalid_args_response

    arguments, plugin_context, plugin_error_response = await _apply_before_tool_plugins(
        app,
        request,
        deps=deps,
        runtime=runtime,
        route=route,
        arguments=arguments,
    )
    if plugin_error_response is not None or plugin_context is None:
        assert plugin_error_response is not None
        return plugin_error_response

    downstream_params = dict(runtime.params)
    downstream_params["name"] = route["original_name"]
    downstream_params["arguments"] = arguments
    service_id = str(route.get("service_id") or "")

    if resilience_response := await _check_tool_service_resilience(
        app,
        request,
        deps=deps,
        runtime=runtime,
        route=route,
        service_id=service_id,
    ):
        return resilience_response

    execution_state = DownstreamToolExecutionState()
    try:
        downstream_response = await _execute_downstream_tool_call(
            app,
            request,
            deps=deps,
            runtime=runtime,
            route=route,
            downstream_params=downstream_params,
            state=execution_state,
        )
        _ctx(app).inflight_downstream_calls.pop(str(runtime.request_id), None)
        result = downstream_response.get("result")
        if not isinstance(result, dict):
            raise DownstreamMcpError("downstream tools/call returned invalid result")
        try:
            result = await _ctx(app).plugins.after_tool_response(plugin_context, result)
        except PluginExecutionError as exc:
            if service_id:
                _ctx(app).circuit_breaker.record_success(service_id)
            return await _log_plugin_failure(
                app,
                request,
                deps=deps,
                exc=exc,
                request_id=runtime.request_id,
                request_log_id=runtime.request_log_id,
                session_id=runtime.session_id,
                protocol_version=runtime.protocol_version,
                exposed_name=runtime.exposed_name,
                route=route,
                started=runtime.started,
            )
        if service_id:
            _ctx(app).circuit_breaker.record_success(service_id)
        await _log_tool_call_invocation(
            app,
            request,
            deps=deps,
            runtime=runtime,
            route=route,
            status="success",
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
        response_payload = deps.jsonrpc_result(runtime.request_id, result)
        _ctx(app).idempotency_cache.set(runtime.idempotency_key, response_payload)
        return response_payload
    except DownstreamTimeoutError as exc:
        _ctx(app).inflight_downstream_calls.pop(str(runtime.request_id), None)
        if service_id:
            deps.record_downstream_failure(app, service_id)
        await _log_tool_call_invocation(
            app,
            request,
            deps=deps,
            runtime=runtime,
            route=route,
            status="timeout",
            error_code="downstream_timeout",
            error_message=str(exc),
        )
        return deps.jsonrpc_result(
            runtime.request_id,
            deps.tool_error_result("downstream_timeout", "Downstream tool call timed out", downstream_code=exc.code),
        )
    except DownstreamToolError as exc:
        _ctx(app).inflight_downstream_calls.pop(str(runtime.request_id), None)
        if service_id:
            _ctx(app).circuit_breaker.record_success(service_id)
        await _log_tool_call_invocation(
            app,
            request,
            deps=deps,
            runtime=runtime,
            route=route,
            status="error",
            error_code=exc.code,
            error_message=str(exc),
        )
        response_payload = deps.jsonrpc_result(runtime.request_id, deps.tool_error_result("downstream_error", str(exc), downstream_code=exc.code))
        _ctx(app).idempotency_cache.set(runtime.idempotency_key, response_payload)
        return response_payload
    except DownstreamMcpError as exc:
        _ctx(app).inflight_downstream_calls.pop(str(runtime.request_id), None)
        if service_id and exc.code != -32003:
            deps.record_downstream_failure(app, service_id)
        if exc.code == -32003:
            await _ctx(app).repos.audit.log_audit(
                action="ssrf.block",
                resource_type="mcp_service",
                resource_id=route.get("service_id"),
                metadata={"url": route["endpoint_url"], "reason": str(exc)},
                request_id=runtime.request_log_id,
                ip=deps.request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        await _log_tool_call_invocation(
            app,
            request,
            deps=deps,
            runtime=runtime,
            route=route,
            status="error",
            error_code=exc.code,
            error_message=str(exc),
        )
        return deps.jsonrpc_error(runtime.request_id, exc.code, str(exc))
    except UrlSafetyError as exc:
        _ctx(app).inflight_downstream_calls.pop(str(runtime.request_id), None)
        await _ctx(app).repos.audit.log_audit(
            action="ssrf.block",
            resource_type="mcp_service",
            resource_id=route.get("service_id"),
            metadata={"url": route["endpoint_url"], "reason": str(exc)},
            request_id=runtime.request_log_id,
            ip=deps.request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        await _log_tool_call_invocation(
            app,
            request,
            deps=deps,
            runtime=runtime,
            route=route,
            status="policy_denied",
            error_code="ssrf_block",
            error_message=str(exc),
        )
        return deps.jsonrpc_result(runtime.request_id, deps.tool_error_result("ssrf_block", "Downstream endpoint is blocked by CoreMCP policy"))
    finally:
        if service_id and execution_state.stdio_client_for_call is not None:
            await deps.persist_stdio_state(app, service_id, execution_state.stdio_client_for_call)
