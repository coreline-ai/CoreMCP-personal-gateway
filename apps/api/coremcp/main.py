from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from coremcp.auth import extract_bearer_token, verify_admin_bearer
from coremcp.db import Repository
from coremcp.mcp_gateway import SessionStore, negotiate_protocol_version
from coremcp.proxy import DownstreamMcpClient, DownstreamMcpError, DownstreamToolError
from coremcp.settings import Settings, get_settings

SERVER_CAPABILITIES = {"tools": {"listChanged": True}}
JSONRPC_VERSION = "2.0"


def jsonrpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def unauthorized_response() -> JSONResponse:
    return JSONResponse(
        {"error": "invalid_token"},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer realm="coremcp", error="invalid_token"'},
    )


def verify_request_bearer(request: Request) -> bool:
    token = extract_bearer_token(request.headers.get("Authorization"))
    return verify_admin_bearer(token, request.app.state.settings)


def tool_error_result(error_code: str, message: str, *, downstream_code: int | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"error_code": error_code}
    if downstream_code is not None:
        meta["downstream_code"] = downstream_code
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
        "_meta": {"coremcp": meta},
    }


def _is_future_protocol(value: str | None) -> bool:
    return bool(value and value > "2025-11-25")


def _normalize_downstream_tool(tool: dict[str, Any]) -> tuple[dict[str, Any], str]:
    original_name = str(tool.get("name", "")).strip()
    exposed_name = original_name if "." in original_name else f"fake.{original_name}"
    normalized = dict(tool)
    normalized["name"] = exposed_name
    return normalized, original_name


def _get_request_id(payload: dict[str, Any]) -> Any:
    return payload.get("id")


async def _refresh_tools(
    app: FastAPI,
    *,
    request_id: Any,
    protocol_version: str | None,
    session_id: str | None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    downstream: DownstreamMcpClient = app.state.downstream
    response = await downstream.request(
        method="tools/list",
        params=params or {},
        request_id=request_id,
        protocol_version=protocol_version,
        session_id=session_id,
    )
    result = response.get("result")
    if not isinstance(result, dict):
        raise DownstreamMcpError("downstream tools/list returned invalid result")

    transformed_tools: list[dict[str, Any]] = []
    registry: dict[str, str] = {}
    for tool in result.get("tools", []):
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        transformed, original_name = _normalize_downstream_tool(tool)
        transformed_tools.append(transformed)
        registry[transformed["name"]] = original_name

    app.state.tool_registry = registry
    result = dict(result)
    result["tools"] = transformed_tools
    result.setdefault("nextCursor", None)
    return result


async def _handle_initialize(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
) -> tuple[dict[str, Any], str]:
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    requested = params.get("protocolVersion") or request.headers.get("MCP-Protocol-Version")
    protocol_version = negotiate_protocol_version(requested)
    session = app.state.sessions.create(protocol_version)

    downstream_params = dict(params)
    downstream_params["protocolVersion"] = protocol_version
    try:
        await app.state.downstream.request(
            method="initialize",
            params=downstream_params,
            request_id=_get_request_id(payload),
            protocol_version=protocol_version,
            session_id=session.id,
        )
    except DownstreamMcpError:
        # P0 keeps CoreMCP usable even if the fake downstream is not started yet.
        # tools/list and tools/call still surface downstream failures explicitly.
        pass

    result = {
        "protocolVersion": protocol_version,
        "capabilities": SERVER_CAPABILITIES,
        "serverInfo": {"name": "CoreMCP", "version": app.state.settings.app_version},
    }
    if _is_future_protocol(requested):
        result["_coremcp"] = {"warning": "future protocol downgraded to latest supported version"}
    return jsonrpc_result(_get_request_id(payload), result), session.id


async def _handle_tools_list(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    started = time.perf_counter()
    session_id = request.headers.get("Mcp-Session-Id")
    session = app.state.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    try:
        result = await _refresh_tools(
            app,
            request_id=_get_request_id(payload),
            protocol_version=protocol_version,
            session_id=session_id,
            params=payload.get("params") if isinstance(payload.get("params"), dict) else {},
        )
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/list",
            tool_name=None,
            status="success",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return jsonrpc_result(_get_request_id(payload), result)
    except DownstreamMcpError as exc:
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/list",
            tool_name=None,
            status="error",
            error_code=exc.code,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return jsonrpc_error(_get_request_id(payload), exc.code, str(exc))


async def _handle_tools_call(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    started = time.perf_counter()
    request_id = _get_request_id(payload)
    params = payload.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        return jsonrpc_error(request_id, -32602, "Invalid params")

    session_id = request.headers.get("Mcp-Session-Id")
    session = app.state.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    exposed_name = params["name"]

    registry: dict[str, str] = getattr(app.state, "tool_registry", {})
    if exposed_name not in registry:
        try:
            await _refresh_tools(
                app,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=session_id,
            )
            registry = app.state.tool_registry
        except DownstreamMcpError as exc:
            await app.state.repository.log_invocation(
                session_id=session_id,
                method="tools/call",
                tool_name=exposed_name,
                status="error",
                error_code=exc.code,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            return jsonrpc_error(request_id, exc.code, str(exc))

    if exposed_name not in registry:
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="error",
            error_code=-32602,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return jsonrpc_error(request_id, -32602, "Unknown tool")

    downstream_params = dict(params)
    downstream_params["name"] = registry[exposed_name]
    try:
        downstream_response = await app.state.downstream.request(
            method="tools/call",
            params=downstream_params,
            request_id=request_id,
            protocol_version=protocol_version,
            session_id=session_id,
        )
        result = downstream_response.get("result")
        if not isinstance(result, dict):
            raise DownstreamMcpError("downstream tools/call returned invalid result")
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="success",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return jsonrpc_result(request_id, result)
    except DownstreamToolError as exc:
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="error",
            error_code=exc.code,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return jsonrpc_result(
            request_id,
            tool_error_result("downstream_error", str(exc), downstream_code=exc.code),
        )
    except DownstreamMcpError as exc:
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="error",
            error_code=exc.code,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return jsonrpc_error(request_id, exc.code, str(exc))


async def dispatch_mcp(app: FastAPI, payload: dict[str, Any], request: Request) -> tuple[dict[str, Any] | None, str | None]:
    request_id = _get_request_id(payload)
    if payload.get("jsonrpc") != JSONRPC_VERSION or not isinstance(payload.get("method"), str):
        return jsonrpc_error(request_id, -32600, "Invalid Request"), None

    method = payload["method"]
    if method == "initialize":
        return await _handle_initialize(app, payload, request)
    if method == "notifications/initialized":
        app.state.sessions.mark_initialized(request.headers.get("Mcp-Session-Id"))
        return None, None
    if method == "ping":
        return jsonrpc_result(request_id, {}), None
    if method == "tools/list":
        return await _handle_tools_list(app, payload, request), None
    if method == "tools/call":
        return await _handle_tools_call(app, payload, request), None
    return jsonrpc_error(request_id, -32601, "Method not found"), None


def create_app(settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    owns_http_client = http_client is None
    http_client = http_client or httpx.AsyncClient(timeout=settings.downstream_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.sessions = SessionStore()
        app.state.repository = Repository(settings.resolved_database_path)
        app.state.http_client = http_client
        app.state.downstream = DownstreamMcpClient(settings.fake_mcp_url, http_client)
        app.state.tool_registry = {}
        await app.state.repository.connect()
        try:
            yield
        finally:
            await app.state.repository.close()
            if owns_http_client:
                await http_client.aclose()

    app = FastAPI(title="CoreMCP API", version=settings.app_version, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/ready")
    async def ready(request: Request) -> dict[str, str]:
        # Accessing db asserts startup bootstrap completed.
        _ = request.app.state.repository.db
        return {"status": "ready"}

    @app.post("/mcp")
    async def mcp(request: Request) -> Response:
        if not verify_request_bearer(request):
            return unauthorized_response()

        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse(jsonrpc_error(None, -32700, "Parse error"), status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse(jsonrpc_error(None, -32600, "Invalid Request"), status_code=400)

        response_payload, new_session_id = await dispatch_mcp(request.app, payload, request)
        if response_payload is None:
            return Response(status_code=202)

        headers = {}
        if new_session_id:
            headers["Mcp-Session-Id"] = new_session_id
        elif request.headers.get("Mcp-Session-Id"):
            headers["Mcp-Session-Id"] = request.headers["Mcp-Session-Id"]
        return JSONResponse(response_payload, headers=headers)

    @app.get("/mcp")
    async def mcp_sse(request: Request) -> Response:
        if not verify_request_bearer(request):
            return unauthorized_response()

        async def events():
            yield ': CoreMCP SSE keepalive\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.delete("/mcp")
    async def mcp_delete(request: Request) -> Response:
        if not verify_request_bearer(request):
            return unauthorized_response()
        request.app.state.sessions.delete(request.headers.get("Mcp-Session-Id"))
        return Response(status_code=204)

    @app.get("/v1/settings")
    async def settings_endpoint(request: Request) -> Response:
        if not verify_request_bearer(request):
            return unauthorized_response()
        settings_obj: Settings = request.app.state.settings
        return JSONResponse(
            {
                "admin_token_masked": "cmcp_admin_••••",
                "client_token_count": 0,
                "auth_mode": "static_bearer",
                "oauth_enabled": False,
                "secret_backend": "keychain",
                "tailscale_enabled": False,
                "cache_backend": "memory",
                "app_version": settings_obj.app_version,
            }
        )

    @app.get("/v1/mcp-services")
    async def list_services(request: Request) -> Response:
        if not verify_request_bearer(request):
            return unauthorized_response()
        return JSONResponse({"items": [], "next_cursor": None})

    @app.get("/v1/toolboxes")
    async def list_toolboxes(request: Request) -> Response:
        if not verify_request_bearer(request):
            return unauthorized_response()
        return JSONResponse(
            {"items": [{"id": "tbx_default", "name": "Default", "is_default": True, "item_count": 0}], "next_cursor": None}
        )

    @app.get("/v1/external-connections")
    async def list_external_connections(request: Request) -> Response:
        if not verify_request_bearer(request):
            return unauthorized_response()
        return JSONResponse({"items": [], "next_cursor": None})

    @app.get("/v1/playground/tools/list")
    async def playground_tools_list(request: Request) -> Response:
        if not verify_request_bearer(request):
            return unauthorized_response()
        session_id = request.headers.get("Mcp-Session-Id")
        try:
            result = await _refresh_tools(
                request.app,
                request_id="playground-tools-list",
                protocol_version=request.headers.get("MCP-Protocol-Version"),
                session_id=session_id,
            )
            return JSONResponse({"items": result.get("tools", []), "next_cursor": result.get("nextCursor")})
        except DownstreamMcpError:
            return JSONResponse({"items": [], "next_cursor": None})

    @app.get("/v1/tool-invocations")
    async def list_tool_invocations(request: Request, limit: int = 20) -> Response:
        if not verify_request_bearer(request):
            return unauthorized_response()
        items = await request.app.state.repository.recent_invocations(limit=max(1, min(limit, 100)))
        return JSONResponse({"items": items, "next_cursor": None})

    return app


app = create_app()
