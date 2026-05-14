from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

JSONRPC_VERSION = "2.0"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-06-18", "2025-11-25"}
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
LATEST_PROTOCOL_VERSION = "2025-11-25"

ToolHandler = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]
ResetHandler = Callable[[], None]


@dataclass(frozen=True)
class DemoMcpServer:
    slug: str
    service_slug: str
    title: str
    description: str
    tools: list[dict[str, Any]]
    handlers: dict[str, ToolHandler]
    category: str = "demo"
    reset: ResetHandler | None = None

    def registration_payload(self, base_url: str) -> dict[str, Any]:
        return {
            "name": self.title,
            "slug": self.service_slug,
            "description": self.description,
            "endpoint_url": f"{base_url.rstrip('/')}/{self.slug}/mcp",
            "auth_type": "none",
            "category": self.category,
        }


def json_rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def json_rpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def text_result(text: str, structured: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured or {"text": text},
        "isError": False,
    }


def tool(
    *,
    name: str,
    title: str,
    description: str,
    input_schema: dict[str, Any],
    read_only: bool,
    destructive: bool = False,
    idempotent: bool = True,
    open_world: bool = False,
    icons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": input_schema,
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
            "openWorldHint": open_world,
        },
    }
    if icons:
        item["icons"] = icons
    return item


def object_schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _negotiate_protocol_version(params: dict[str, Any]) -> str:
    requested = params.get("protocolVersion")
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return str(requested)
    if requested is None:
        return DEFAULT_PROTOCOL_VERSION
    return LATEST_PROTOCOL_VERSION


async def _call_handler(handler: ToolHandler, arguments: dict[str, Any]) -> dict[str, Any]:
    result = handler(arguments)
    if inspect.isawaitable(result):
        return await result
    return result


async def dispatch(server: DemoMcpServer, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, str]]:
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if payload.get("jsonrpc") != JSONRPC_VERSION or not isinstance(method, str):
        return json_rpc_error(request_id, -32600, "Invalid Request"), {}
    if not isinstance(params, dict):
        return json_rpc_error(request_id, -32602, "Invalid params"), {}

    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None, {}

    if method == "initialize":
        protocol_version = _negotiate_protocol_version(params)
        return (
            json_rpc_result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": server.title, "version": "0.1.0"},
                },
            ),
            {"Mcp-Session-Id": f"demo_{server.slug}_{uuid.uuid4().hex}"},
        )

    if method == "tools/list":
        return json_rpc_result(request_id, {"tools": server.tools}), {}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return json_rpc_error(request_id, -32602, "Invalid params", {"reason": "name and arguments are required"}), {}
        handler = server.handlers.get(name)
        if handler is None:
            return json_rpc_error(request_id, -32602, "Unknown tool", {"tool": name}), {}
        return json_rpc_result(request_id, await _call_handler(handler, arguments)), {}

    if method == "ping":
        return json_rpc_result(request_id, {}), {}

    return json_rpc_error(request_id, -32601, "Method not found", {"method": method}), {}


def create_demo_app(servers: list[DemoMcpServer]) -> FastAPI:
    app = FastAPI(
        title="CoreMCP Demo MCP Suite",
        summary="Eight local demo MCP servers for CoreMCP personal gateway demos.",
        version="0.1.0",
    )
    server_map = {server.slug: server for server in servers}
    app.state.demo_servers = server_map

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "server_count": len(server_map),
            "servers": sorted(server_map),
        }

    @app.get("/demo-services")
    def demo_services(request: Request) -> dict[str, Any]:
        base_url = str(request.base_url).rstrip("/")
        return {
            "items": [
                server.registration_payload(base_url)
                for server in sorted(server_map.values(), key=lambda item: item.service_slug)
            ],
            "next_cursor": None,
        }

    @app.get("/{server_slug}/health")
    def server_health(server_slug: str) -> Response:
        server = server_map.get(server_slug)
        if server is None:
            return JSONResponse({"error": "server not found"}, status_code=404)
        return JSONResponse({"status": "ok", "server": server.slug, "title": server.title})

    @app.post("/{server_slug}/mcp")
    async def mcp(server_slug: str, request: Request, response: Response) -> Response:
        server = server_map.get(server_slug)
        if server is None:
            return JSONResponse(json_rpc_error(None, -32601, "Demo MCP server not found"), status_code=404)
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse(json_rpc_error(None, -32700, "Parse error"), status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse(json_rpc_error(None, -32600, "Invalid Request"), status_code=400)

        body, headers = await dispatch(server, payload)
        for name, value in headers.items():
            response.headers[name] = value
        if body is None:
            response.status_code = 202
            return response
        return JSONResponse(body, headers=headers)

    @app.post("/_test/reset-state")
    def reset_state() -> dict[str, Any]:
        reset = []
        for server in server_map.values():
            if server.reset is not None:
                server.reset()
                reset.append(server.slug)
        return {"ok": True, "reset": sorted(reset)}

    return app
