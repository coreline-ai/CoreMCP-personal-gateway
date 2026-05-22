from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from coremcp.api._schemas import PlaygroundToolList
from coremcp.proxy import DownstreamMcpError


def register_playground_routes(
    app: FastAPI,
    *,
    verify_admin_request: Callable[[Request], bool],
    unauthorized_response: Callable[..., JSONResponse],
    json_body: Callable[[Request], Awaitable[dict[str, Any] | JSONResponse]],
    api_error: Callable[..., JSONResponse],
    refresh_tools: Callable[..., Awaitable[dict[str, Any]]],
    handle_tools_call: Callable[[FastAPI, dict[str, Any], Request], Awaitable[dict[str, Any]]],
    correlation_id: Callable[[Request], str],
) -> None:
    @app.get("/v1/playground/tools/list", response_model=PlaygroundToolList)
    async def playground_tools_list(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        session_id = request.headers.get("Mcp-Session-Id")
        try:
            result = await refresh_tools(
                request.app,
                request_id="playground-tools-list",
                protocol_version=request.headers.get("MCP-Protocol-Version"),
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
            return JSONResponse({"items": result.get("tools", []), "next_cursor": result.get("nextCursor")})
        except DownstreamMcpError:
            return JSONResponse({"items": [], "next_cursor": None})

    @app.post("/v1/playground/tools/call")
    async def playground_tools_call(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await json_body(request)
        if isinstance(body, JSONResponse):
            return body
        exposed_name = body.get("exposed_name") or body.get("name")
        if not isinstance(exposed_name, str):
            return api_error("validation_failed", "exposed_name is required", status_code=422)
        payload = {
            "jsonrpc": "2.0",
            "id": body.get("request_id") or "playground-call",
            "method": "tools/call",
            "params": {
                "name": exposed_name,
                "arguments": body.get("arguments") if isinstance(body.get("arguments"), dict) else {},
            },
        }
        return JSONResponse(await handle_tools_call(request.app, payload, request))
