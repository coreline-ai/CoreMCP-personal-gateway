from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

McpDispatchCallable = Callable[[FastAPI, dict[str, Any], Request], Awaitable[tuple[dict[str, Any] | None, str | None]]]
McpBatchDispatchCallable = Callable[[FastAPI, list[Any], Request], Awaitable[tuple[list[dict[str, Any]] | None, str | None]]]


def register_mcp_routes(
    app: FastAPI,
    *,
    verify_mcp_request: Callable[[Request], Awaitable[bool]],
    unauthorized_response: Callable[[Request | None], JSONResponse],
    jsonrpc_error: Callable[[Any, int, str], dict[str, Any]],
    dispatch_mcp: McpDispatchCallable,
    dispatch_mcp_batch: McpBatchDispatchCallable,
    parse_last_event_id: Callable[[str | None], int | None],
    correlation_id: Callable[[Request], str],
    request_ip: Callable[[Request], str | None],
    session_idle_reap_seconds: int,
) -> None:
    @app.post("/mcp")
    async def mcp(request: Request) -> Response:
        if not await verify_mcp_request(request):
            await request.app.state.repos.audit.log_audit(
                action="auth.failure",
                resource_type="mcp",
                request_id=correlation_id(request),
                ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            return unauthorized_response(request)

        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse(jsonrpc_error(None, -32700, "Parse error"), status_code=400)
        if isinstance(payload, list) and not payload:
            return JSONResponse(jsonrpc_error(None, -32600, "Invalid Request"), status_code=400)
        if not isinstance(payload, (dict, list)):
            return JSONResponse(jsonrpc_error(None, -32600, "Invalid Request"), status_code=400)

        request.app.state.sessions.touch(request.headers.get("Mcp-Session-Id"))
        request.app.state.sessions.reap_idle(session_idle_reap_seconds)
        if isinstance(payload, list):
            response_payload, new_session_id = await dispatch_mcp_batch(request.app, payload, request)
        else:
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
    async def mcp_sse(request: Request, max_events: int | None = None, heartbeat_seconds: float = 15.0) -> Response:
        if not await verify_mcp_request(request):
            return unauthorized_response(request)
        last_event_id = parse_last_event_id(request.headers.get("Last-Event-Id"))

        async def events():
            subscription = await request.app.state.list_changed_bus.subscribe(last_event_id=last_event_id)
            try:
                yield ": CoreMCP SSE keepalive\n\n"
                if max_events == 0:
                    return
                emitted = 0
                heartbeat = max(0.1, min(heartbeat_seconds, 60.0))
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(subscription.get(), timeout=heartbeat)
                    except TimeoutError:
                        yield ": CoreMCP SSE keepalive\n\n"
                        continue
                    emitted += 1
                    yield (
                        f"id: {event.id}\n"
                        f"event: {event.event}\n"
                        f"data: {json.dumps(event.data, ensure_ascii=False, separators=(',', ':'))}\n\n"
                    )
                    if max_events is not None and emitted >= max_events:
                        return
            finally:
                await subscription.close()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.delete("/mcp")
    async def mcp_delete(request: Request) -> Response:
        if not await verify_mcp_request(request):
            return unauthorized_response(request)
        request.app.state.sessions.delete(request.headers.get("Mcp-Session-Id"))
        return Response(status_code=204)
