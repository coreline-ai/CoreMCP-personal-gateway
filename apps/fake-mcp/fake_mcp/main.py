from __future__ import annotations

import asyncio
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

SUPPORTED_PROTOCOL_VERSIONS = {"2025-06-18", "2025-11-25"}
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
LATEST_PROTOCOL_VERSION = "2025-11-25"

JSONRPC_VERSION = "2.0"

app = FastAPI(
    title="CoreMCP Fake Downstream MCP",
    summary="Test-only downstream MCP server for CoreMCP P0/P1 integration.",
    version="0.1.0",
)

# Test-only in-memory state. This intentionally stores only Authorization headers
# received by this fake downstream server so integration tests can verify token
# boundaries. Do not copy this pattern into production services.
app.state.authorization_headers = []
app.state.schema_version = 0


def _json_rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _json_rpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def _negotiate_protocol_version(params: dict[str, Any]) -> str:
    requested = params.get("protocolVersion")
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    if requested is None:
        return DEFAULT_PROTOCOL_VERSION
    return LATEST_PROTOCOL_VERSION


def _tool_content_text(text: str) -> list[dict[str, str]]:
    return [{"type": "text", "text": text}]


def _tools() -> list[dict[str, Any]]:
    app.state.schema_version += 1
    schema_version = app.state.schema_version
    return [
        {
            "name": "echo",
            "title": "Echo",
            "description": "Return the provided message for downstream proxy smoke tests.",
            "icons": [
                {
                    "src": "https://example.test/icons/echo.svg",
                    "mimeType": "image/svg+xml",
                    "sizes": ["48x48"],
                }
            ],
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to echo."}
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            "annotations": {
                "destructiveHint": False,
                "readOnlyHint": True,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        },
        {
            "name": "add",
            "title": "Add Numbers",
            "description": "Add two numbers and return the sum.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
                "additionalProperties": False,
            },
            "annotations": {
                "destructiveHint": False,
                "readOnlyHint": True,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        },
        {
            "name": "sleep",
            "title": "Sleep",
            "description": "Wait for a bounded number of seconds before returning.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 35,
                        "default": 0.1,
                    }
                },
                "additionalProperties": False,
            },
            "annotations": {
                "destructiveHint": False,
                "readOnlyHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        },
        {
            "name": "error",
            "title": "Error",
            "description": "Return a downstream JSON-RPC error for proxy error-path tests.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "default": "Fake downstream error"},
                    "code": {"type": "integer", "default": -32000},
                },
                "additionalProperties": False,
            },
            "annotations": {
                "destructiveHint": False,
                "readOnlyHint": True,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        },
        {
            "name": "cancellation",
            "title": "Cancellation Fixture",
            "description": "Sleep for up to 60 seconds so clients can test cancellation behavior.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "minimum": 0, "maximum": 60, "default": 60}
                },
                "additionalProperties": False,
            },
            "annotations": {
                "destructiveHint": False,
                "readOnlyHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        },
        {
            "name": "schema-change",
            "title": f"Schema Change v{schema_version}",
            "description": "Change schema metadata on each tools/list call for drift detection tests.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    f"value_v{schema_version}": {"type": "string"}
                },
                "additionalProperties": False,
            },
            "annotations": {
                "destructiveHint": False,
                "readOnlyHint": True,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        },
        {
            "name": "icons-rich",
            "title": "Rich Icon Fixture",
            "description": "Expose mixed icon metadata including an SVG data URL for sanitizer tests.",
            "icons": [
                {"src": "data:image/svg+xml,%3Csvg%20onload%3Dalert(1)%3E%3C/svg%3E", "mimeType": "image/svg+xml"},
                {"src": "https://example.test/icons/rich.png", "mimeType": "image/png", "sizes": ["64x64"]},
            ],
            "inputSchema": {"type": "object", "additionalProperties": False},
            "annotations": {
                "destructiveHint": False,
                "readOnlyHint": True,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        },
        {
            "name": "cimd-test",
            "title": "CIMD Fixture Marker",
            "description": "Marker tool used with /.well-known/oauth-client fixture endpoint.",
            "inputSchema": {"type": "object", "additionalProperties": False},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "dcr-test",
            "title": "DCR Fixture Marker",
            "description": "Marker tool used with /oauth/register fixture endpoint.",
            "inputSchema": {"type": "object", "additionalProperties": False},
            "annotations": {"readOnlyHint": True},
        },
    ]


async def _handle_initialize(request_id: Any, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    protocol_version = _negotiate_protocol_version(params)
    session_id = f"fake_mcp_{uuid.uuid4().hex}"
    return (
        _json_rpc_result(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "CoreMCP Fake MCP", "version": "0.1.0"},
            },
        ),
        {"Mcp-Session-Id": session_id},
    )


async def _handle_tools_call(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _json_rpc_error(request_id, -32602, "Invalid params", {"reason": "arguments must be an object"})

    if name == "echo":
        message = str(arguments.get("message", arguments.get("text", "")))
        return _json_rpc_result(
            request_id,
            {
                "content": _tool_content_text(message),
                "structuredContent": {"message": message},
                "isError": False,
            },
        )

    if name == "add":
        try:
            a = arguments["a"]
            b = arguments["b"]
            total = a + b
        except KeyError as exc:
            return _json_rpc_error(request_id, -32602, "Invalid params", {"missing": str(exc)})
        except TypeError:
            return _json_rpc_error(request_id, -32602, "Invalid params", {"reason": "a and b must be numbers"})
        return _json_rpc_result(
            request_id,
            {
                "content": _tool_content_text(str(total)),
                "structuredContent": {"sum": total},
                "isError": False,
            },
        )

    if name == "sleep":
        seconds = float(arguments.get("seconds", 0.1))
        seconds = max(0.0, min(seconds, 35.0))
        await asyncio.sleep(seconds)
        return _json_rpc_result(
            request_id,
            {
                "content": _tool_content_text(f"Slept for {seconds:g} seconds"),
                "structuredContent": {"seconds": seconds},
                "isError": False,
            },
        )

    if name == "error":
        message = str(arguments.get("message", "Fake downstream error"))
        code = int(arguments.get("code", -32000))
        return _json_rpc_error(request_id, code, message, {"tool": "error"})

    if name == "cancellation":
        seconds = float(arguments.get("seconds", 60))
        seconds = max(0.0, min(seconds, 60.0))
        await asyncio.sleep(seconds)
        return _json_rpc_result(
            request_id,
            {
                "content": _tool_content_text(f"Cancellation fixture completed after {seconds:g} seconds"),
                "structuredContent": {"seconds": seconds},
                "isError": False,
            },
        )

    if name in {"schema-change", "icons-rich", "cimd-test", "dcr-test"}:
        return _json_rpc_result(
            request_id,
            {
                "content": _tool_content_text(f"{name} ok"),
                "structuredContent": {"tool": name},
                "isError": False,
            },
        )

    return _json_rpc_error(request_id, -32602, "Unknown tool", {"tool": name})


async def _dispatch(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, str]]:
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if payload.get("jsonrpc") != JSONRPC_VERSION or not isinstance(method, str):
        return _json_rpc_error(request_id, -32600, "Invalid Request"), {}
    if not isinstance(params, dict):
        return _json_rpc_error(request_id, -32602, "Invalid params"), {}

    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None, {}
    if method == "initialize":
        return await _handle_initialize(request_id, params)
    if method == "tools/list":
        return _json_rpc_result(request_id, {"tools": _tools()}), {}
    if method == "tools/call":
        return await _handle_tools_call(request_id, params), {}
    if method == "ping":
        return _json_rpc_result(request_id, {}), {}

    return _json_rpc_error(request_id, -32601, "Method not found", {"method": method}), {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/.well-known/oauth-client")
def cimd_metadata(request: Request) -> dict[str, Any]:
    client_id = str(request.url)
    origin = str(request.base_url).rstrip("/")
    return {
        "client_id": client_id,
        "client_name": "CoreMCP Fake CIMD Client",
        "redirect_uris": [f"{origin}/oauth/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "mcp:tools.read mcp:tools.call",
    }


@app.post("/oauth/register")
async def dcr_register(request: Request) -> JSONResponse:
    metadata = await request.json()
    redirect_uris = metadata.get("redirect_uris", ["http://localhost/callback"]) if isinstance(metadata, dict) else ["http://localhost/callback"]
    return JSONResponse(
        {
            "client_id": f"fake_dcr_{uuid.uuid4().hex}",
            "client_id_issued_at": 0,
            "client_name": metadata.get("client_name", "CoreMCP Fake DCR Client") if isinstance(metadata, dict) else "CoreMCP Fake DCR Client",
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        status_code=201,
    )


@app.post("/mcp")
async def mcp(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Response:
    app.state.authorization_headers.append(authorization)

    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(_json_rpc_error(None, -32700, "Parse error"), status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse(_json_rpc_error(None, -32600, "Invalid Request"), status_code=400)

    body, headers = await _dispatch(payload)
    for name, value in headers.items():
        response.headers[name] = value

    if body is None:
        response.status_code = 202
        return response

    return JSONResponse(body, headers=headers)


@app.get("/_test/authorization")
def get_authorization_state() -> dict[str, Any]:
    headers: list[str | None] = app.state.authorization_headers
    return {
        "lastAuthorization": headers[-1] if headers else None,
        "authorizationHeaders": headers,
        "requestCount": len(headers),
    }


@app.post("/_test/reset-state")
def reset_test_state() -> dict[str, Any]:
    app.state.authorization_headers = []
    app.state.schema_version = 0
    return {"ok": True}


def run() -> None:
    uvicorn.run("fake_mcp.main:app", host="127.0.0.1", port=8790, reload=False)


if __name__ == "__main__":
    run()
