from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from coremcp.main import create_app
from coremcp.settings import Settings

TOKEN = "cmcp_admin_testtoken"


class DownstreamRecorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": body,
            }
        )
        rpc_method = body.get("method")
        request_id = body.get("id")
        if rpc_method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"protocolVersion": body.get("params", {}).get("protocolVersion")},
                },
            )
        if rpc_method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "title": "Echo",
                                "description": "Echo input text.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                    "required": ["text"],
                                },
                                "annotations": {"readOnlyHint": True},
                            },
                            {
                                "name": "error",
                                "title": "Error",
                                "description": "Return a JSON-RPC error.",
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": True},
                            }
                        ],
                        "nextCursor": None,
                    },
                },
            )
        if rpc_method == "tools/call":
            params = body.get("params", {})
            if params.get("name") == "error":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32001, "message": "fake downstream exploded"},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"echo:{params.get('arguments', {}).get('text', '')}",
                            }
                        ]
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            },
        )


@pytest.fixture
async def app_client(tmp_path: Path):
    recorder = DownstreamRecorder()
    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "coremcp.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, recorder, app
    await downstream_client.aclose()


def auth_headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def initialize(client: httpx.AsyncClient, protocol_version: str | None = "2025-06-18") -> httpx.Response:
    params: dict[str, Any] = {"capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}}
    if protocol_version is not None:
        params["protocolVersion"] = protocol_version
    return await client.post(
        "/mcp",
        headers=auth_headers(),
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params},
    )


@pytest.mark.asyncio
async def test_health_ready_live(app_client):
    client, _, _ = app_client
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/live")).json() == {"status": "alive"}
    assert (await client.get("/ready")).json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_auth_failure(app_client):
    client, _, _ = app_client
    response = await client.post(
        "/mcp",
        headers=auth_headers("wrong-token"),
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith('Bearer realm="coremcp"')


@pytest.mark.asyncio
async def test_initialize_returns_session_and_protocol(app_client):
    client, recorder, _ = app_client
    response = await initialize(client, "2025-06-18")
    assert response.status_code == 200
    assert response.headers.get("Mcp-Session-Id")
    assert response.json()["result"]["protocolVersion"] == "2025-06-18"
    assert recorder.requests[0]["body"]["method"] == "initialize"


@pytest.mark.asyncio
async def test_protocol_negotiation_missing_and_future(app_client):
    client, _, _ = app_client

    missing = await initialize(client, None)
    assert missing.json()["result"]["protocolVersion"] == "2025-06-18"

    latest = await initialize(client, "2025-11-25")
    assert latest.json()["result"]["protocolVersion"] == "2025-11-25"

    future = await initialize(client, "2099-01-01")
    assert future.json()["result"]["protocolVersion"] == "2025-11-25"


@pytest.mark.asyncio
async def test_tools_list_proxies_and_prefixes_fake_tools(app_client):
    client, _, _ = app_client
    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]

    response = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )

    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert tools[0]["name"] == "fake.echo"
    assert response.json()["result"]["nextCursor"] is None


@pytest.mark.asyncio
async def test_tools_call_proxies_to_downstream_and_logs_invocation(app_client):
    client, _, app = app_client
    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]
    await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )

    response = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "fake.echo", "arguments": {"text": "hello"}},
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["content"][0]["text"] == "echo:hello"
    assert await app.state.repository.count_invocations() >= 2


@pytest.mark.asyncio
async def test_token_boundary_never_forwards_coremcp_authorization(app_client):
    client, recorder, _ = app_client
    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]
    await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "fake.echo", "arguments": {"text": "safe"}},
        },
    )

    assert recorder.requests
    assert all("authorization" not in request["headers"] for request in recorder.requests)


@pytest.mark.asyncio
async def test_unknown_tool_returns_invalid_params(app_client):
    client, _, _ = app_client
    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]

    response = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "fake.missing", "arguments": {}},
        },
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_unsupported_method_returns_method_not_found(app_client):
    client, _, _ = app_client
    response = await client.post(
        "/mcp",
        headers=auth_headers(),
        json={"jsonrpc": "2.0", "id": 9, "method": "resources/list", "params": {}},
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_admin_token_file_has_priority_over_env(tmp_path: Path):
    token_file = tmp_path / "admin-token"
    token_file.write_text("file-token", encoding="utf-8")
    recorder = DownstreamRecorder()
    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE="env-token",
            COREMCP_ADMIN_TOKEN_FILE=token_file,
            COREMCP_DB_PATH=tmp_path / "priority.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            env_response = await client.post(
                "/mcp",
                headers=auth_headers("env-token"),
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            file_response = await client.post(
                "/mcp",
                headers=auth_headers("file-token"),
                json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
            )
    await downstream_client.aclose()

    assert env_response.status_code == 401
    assert file_response.status_code == 200


@pytest.mark.asyncio
async def test_downstream_jsonrpc_error_becomes_tool_error_result(app_client):
    client, _, _ = app_client
    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]
    await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )

    response = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "fake.error", "arguments": {}},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "error" not in body
    assert body["result"]["isError"] is True
    assert body["result"]["_meta"]["coremcp"]["error_code"] == "downstream_error"
    assert body["result"]["_meta"]["coremcp"]["downstream_code"] == -32001


@pytest.mark.asyncio
async def test_sse_get_and_delete_session(app_client):
    client, _, _ = app_client
    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]

    sse = await client.get("/mcp", headers={**auth_headers(), "Accept": "text/event-stream"})
    assert sse.status_code == 200
    assert "text/event-stream" in sse.headers["content-type"]
    assert "CoreMCP SSE keepalive" in sse.text

    deleted = await client.delete("/mcp", headers={**auth_headers(), "Mcp-Session-Id": session_id})
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_minimal_v1_admin_endpoints(app_client):
    client, _, _ = app_client

    settings = await client.get("/v1/settings", headers=auth_headers())
    toolboxes = await client.get("/v1/toolboxes", headers=auth_headers())
    services = await client.get("/v1/mcp-services", headers=auth_headers())
    clients = await client.get("/v1/external-connections", headers=auth_headers())
    invocations = await client.get("/v1/tool-invocations", headers=auth_headers())

    assert settings.status_code == 200
    assert settings.json()["auth_mode"] == "static_bearer"
    assert toolboxes.json()["items"][0]["id"] == "tbx_default"
    assert services.json() == {"items": [], "next_cursor": None}
    assert clients.json() == {"items": [], "next_cursor": None}
    assert invocations.status_code == 200
