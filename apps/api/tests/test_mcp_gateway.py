from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import coremcp.main as main_module
from coremcp.credentials import FernetBackend, KeychainBackend
from coremcp.logging import REDACTED, redact_sensitive_data
from coremcp.main import _run_service_health_probe_once, create_app
from coremcp.plugins import PluginRegistry, ToolCallContext
from coremcp.proxy import DownstreamMcpClient, DownstreamMcpError, DownstreamTimeoutError, UrlSafetyChecker, UrlSafetyError
from coremcp.registry.catalog import normalize_downstream_tools
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
        if rpc_method == "resources/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "resources": [
                            {"uri": "memory://note/1", "name": "Note 1", "mimeType": "text/plain"},
                            {"uri": "memory://large", "name": "Large note", "mimeType": "text/plain"},
                        ],
                        "nextCursor": None,
                    },
                },
            )
        if rpc_method == "resources/templates/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "resourceTemplates": [
                            {"uriTemplate": "memory://note/{id}", "name": "Note template", "mimeType": "text/plain"}
                        ],
                        "nextCursor": None,
                    },
                },
            )
        if rpc_method == "resources/read":
            uri = body.get("params", {}).get("uri")
            if uri == "memory://note/1":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "hello resource"}]},
                    },
                )
            if uri == "memory://large":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "contents": [
                                {"uri": uri, "mimeType": "text/plain", "text": "x" * 25_000}
                            ]
                        },
                    },
                )
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Unknown resource"}})
        if rpc_method == "prompts/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"prompts": [{"name": "summarize", "description": "Summarize text"}], "nextCursor": None},
                },
            )
        if rpc_method == "prompts/get":
            name = body.get("params", {}).get("name")
            if name == "summarize":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"description": "Summarize text", "messages": [{"role": "user", "content": {"type": "text", "text": "Summarize this."}}]},
                    },
                )
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Unknown prompt"}})
        if rpc_method == "notifications/cancelled":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            },
        )


class PresetPolicyRecorder(DownstreamRecorder):
    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        self.requests.append({"method": request.method, "url": str(request.url), "headers": dict(request.headers), "body": body})
        rpc_method = body.get("method")
        request_id = body.get("id")
        if rpc_method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": body.get("params", {}).get("protocolVersion")}},
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
                                "name": "search",
                                "title": "Search",
                                "description": "Read-only lookup.",
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": True},
                            },
                            {
                                "name": "create_note",
                                "title": "Create note",
                                "description": "Creates a local note.",
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": False},
                            },
                            {
                                "name": "delete_note",
                                "title": "Delete note",
                                "description": "Deletes a local note.",
                                "inputSchema": {"type": "object"},
                                "annotations": {"destructiveHint": True},
                            },
                        ],
                        "nextCursor": None,
                    },
                },
            )
        return await super().__call__(request)


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
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "secrets.json",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, recorder, app
    await downstream_client.aclose()


@pytest.fixture
async def oauth_app_client(tmp_path: Path):
    recorder = DownstreamRecorder()
    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    app = create_app(
        settings=Settings(
            AUTH_MODE="oauth",
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "oauth.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "oauth-secrets.json",
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


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def initialize(client: httpx.AsyncClient, protocol_version: str | None = "2025-06-18", token: str = TOKEN) -> httpx.Response:
    params: dict[str, Any] = {"capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}}
    if protocol_version is not None:
        params["protocolVersion"] = protocol_version
    return await client.post(
        "/mcp",
        headers=auth_headers(token),
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
async def test_request_body_size_limit_rejects_large_payload(app_client):
    client, _, _ = app_client
    response = await client.post(
        "/mcp",
        headers={**auth_headers(), "Content-Type": "application/json"},
        content=b"{}" + (b" " * (1024 * 1024)),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


@pytest.mark.asyncio
async def test_request_body_streaming_limit_rejects_chunked_payload(app_client):
    client, _, _ = app_client

    async def oversized_stream():
        yield b'{"jsonrpc":"2.0","id":1,"method":"ping"}'
        yield b" " * (1024 * 1024)

    response = await client.post(
        "/mcp",
        headers={**auth_headers(), "Content-Type": "application/json"},
        content=oversized_stream(),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


@pytest.mark.asyncio
async def test_initialize_returns_session_and_protocol(app_client):
    client, recorder, _ = app_client
    response = await initialize(client, "2025-06-18")
    assert response.status_code == 200
    assert response.headers.get("Mcp-Session-Id")
    assert response.json()["result"]["protocolVersion"] == "2025-06-18"
    assert recorder.requests[0]["body"]["method"] == "initialize"


@pytest.mark.asyncio
async def test_initialize_uses_short_best_effort_downstream_timeout(tmp_path: Path):
    observed_timeout: dict[str, float] = {}

    async def transport(request: httpx.Request) -> httpx.Response:
        observed_timeout.update(request.extensions["timeout"])
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {"protocolVersion": body.get("params", {}).get("protocolVersion")},
            },
        )

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "init-timeout.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "init-secrets.json",
            COREMCP_INITIALIZE_DOWNSTREAM_TIMEOUT_SECONDS=0.25,
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await initialize(client)
            assert response.status_code == 200
            assert response.headers["Mcp-Session-Id"]
    await downstream_client.aclose()
    assert observed_timeout["read"] == pytest.approx(0.25)
    assert observed_timeout["write"] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_protocol_negotiation_missing_and_future(app_client):
    client, _, _ = app_client

    missing = await initialize(client, None)
    assert missing.json()["result"]["protocolVersion"] == "2025-06-18"

    latest = await initialize(client, "2025-11-25")
    assert latest.json()["result"]["protocolVersion"] == "2025-11-25"

    future = await initialize(client, "2099-01-01")
    assert future.json()["result"]["protocolVersion"] == "2025-11-25"
    assert future.json()["result"]["_coremcp"]["code"] == "future_protocol_downgraded"

    unsupported = await initialize(client, "2025-01-01")
    assert unsupported.json()["result"]["protocolVersion"] == "2025-11-25"
    assert unsupported.json()["result"]["_coremcp"]["code"] == "unsupported_protocol_downgraded"


@pytest.mark.asyncio
async def test_jsonrpc_batch_mixed_requests_and_notifications(app_client):
    client, _, _ = app_client
    response = await client.post(
        "/mcp",
        headers=auth_headers(),
        json=[
            {"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": "ping", "method": "ping"},
            "invalid item",
        ],
    )

    assert response.status_code == 200
    assert response.headers.get("Mcp-Session-Id")
    payload = response.json()
    assert [item.get("id") for item in payload] == ["init", "ping", None]
    assert payload[0]["result"]["protocolVersion"] == "2025-06-18"
    assert payload[1]["result"] == {}
    assert payload[2]["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_jsonrpc_batch_notification_only_returns_accepted(app_client):
    client, _, _ = app_client
    response = await client.post(
        "/mcp",
        headers=auth_headers(),
        json=[
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "missing"}},
        ],
    )
    assert response.status_code == 202
    assert response.content == b""


@pytest.mark.asyncio
async def test_jsonrpc_batch_empty_array_is_invalid(app_client):
    client, _, _ = app_client
    response = await client.post("/mcp", headers=auth_headers(), json=[])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600


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
async def test_request_id_is_returned_logged_and_forwarded(app_client):
    client, recorder, app = app_client
    request_id = "req-test-correlation"

    init = await client.post(
        "/mcp",
        headers={**auth_headers(), "X-Request-ID": request_id},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}},
        },
    )

    assert init.status_code == 200
    assert init.headers["x-request-id"] == request_id
    assert recorder.requests[-1]["headers"]["x-request-id"] == request_id
    session_id = init.headers["Mcp-Session-Id"]

    listed = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id, "X-Request-ID": request_id},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )

    assert listed.status_code == 200
    assert listed.headers["x-request-id"] == request_id
    assert recorder.requests[-1]["headers"]["x-request-id"] == request_id
    recent = await app.state.repository.recent_invocations(limit=10)
    assert any(item["method"] == "tools/list" and item["request_id"] == request_id for item in recent)
    assert all("arguments" not in item and "result" not in item for item in recent)


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
        json={"jsonrpc": "2.0", "id": 9, "method": "unsupported/method", "params": {}},
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

    sse = await client.get("/mcp?max_events=0", headers={**auth_headers(), "Accept": "text/event-stream"})
    assert sse.status_code == 200
    assert "text/event-stream" in sse.headers["content-type"]
    assert "CoreMCP SSE keepalive" in sse.text

    deleted = await client.delete("/mcp", headers={**auth_headers(), "Mcp-Session-Id": session_id})
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_sse_emits_list_changed_on_toolbox_catalog_change(app_client):
    client, _, _ = app_client
    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "SSE Fake", "slug": "sse-fake", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]
    assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200

    sse_task = asyncio.create_task(
        client.get("/mcp?max_events=1", headers={**auth_headers(), "Accept": "text/event-stream"})
    )
    await asyncio.sleep(0.05)
    added = await client.post(
        "/v1/toolboxes/tbx_default/items",
        headers=auth_headers(),
        json={"service_id": service_id, "enabled": True},
    )
    assert added.status_code == 201

    sse = await asyncio.wait_for(sse_task, timeout=2)
    assert sse.status_code == 200
    assert "event: listChanged" in sse.text
    assert "notifications/tools/list_changed" in sse.text
    assert "notifications/resources/list_changed" not in sse.text
    assert "notifications/prompts/list_changed" not in sse.text


@pytest.mark.asyncio
async def test_sse_service_catalog_change_emits_tools_resources_and_prompts(app_client):
    client, _, _ = app_client
    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "SSE Catalog", "slug": "sse-catalog", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]

    sse_task = asyncio.create_task(
        client.get("/mcp?max_events=3", headers={**auth_headers(), "Accept": "text/event-stream"})
    )
    await asyncio.sleep(0.05)
    validated = await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())
    assert validated.status_code == 200

    sse = await asyncio.wait_for(sse_task, timeout=2)
    assert sse.status_code == 200
    assert "notifications/tools/list_changed" in sse.text
    assert "notifications/resources/list_changed" in sse.text
    assert "notifications/prompts/list_changed" in sse.text


@pytest.mark.asyncio
async def test_sse_replays_events_after_last_event_id(app_client):
    client, _, app = app_client
    first = await app.state.list_changed_bus.publish_list_changed(
        category="tools",
        reason="test.first",
        resource_id="first",
    )
    second = await app.state.list_changed_bus.publish_list_changed(
        category="resources",
        reason="test.second",
        resource_id="second",
    )
    third = await app.state.list_changed_bus.publish_list_changed(
        category="prompts",
        reason="test.third",
        resource_id="third",
    )

    sse = await client.get(
        "/mcp?max_events=2",
        headers={
            **auth_headers(),
            "Accept": "text/event-stream",
            "Last-Event-Id": str(first.id),
        },
    )

    assert sse.status_code == 200
    assert f"id: {first.id}\n" not in sse.text
    assert f"id: {second.id}\n" in sse.text
    assert f"id: {third.id}\n" in sse.text
    assert "notifications/resources/list_changed" in sse.text
    assert "notifications/prompts/list_changed" in sse.text


@pytest.mark.asyncio
async def test_downstream_sse_progress_and_resource_updated_notifications_fan_out(tmp_path: Path):
    async def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        method = body.get("method")
        request_id = body.get("id")
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}],
                        "nextCursor": None,
                    },
                },
            )
        if method == "tools/call":
            events = [
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"progressToken": "p1", "progress": 0.5, "total": 1},
                },
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/resources/updated",
                    "params": {"uri": "memory://note/1"},
                },
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/tools/list_changed",
                    "params": {},
                },
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": "done"}]},
                },
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join(f"data: {json.dumps(event)}\n\n" for event in events),
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "result": {}})

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "progress.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "progress-secrets.json",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            sse_task = asyncio.create_task(
                client.get("/mcp?max_events=3", headers={**auth_headers(), "Accept": "text/event-stream"})
            )
            await asyncio.sleep(0.05)
            called = await client.post(
                "/mcp",
                headers=auth_headers(),
                json={
                    "jsonrpc": "2.0",
                    "id": "call-progress",
                    "method": "tools/call",
                    "params": {"name": "fake.echo", "arguments": {}},
                },
            )
            assert called.status_code == 200
            assert called.json()["result"]["content"][0]["text"] == "done"
            sse = await asyncio.wait_for(sse_task, timeout=2)
            assert "notifications/progress" in sse.text
            assert "notifications/resources/updated" in sse.text
            assert "notifications/tools/list_changed" in sse.text
            assert "memory://note/1" in sse.text
    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_minimal_v1_admin_endpoints(app_client):
    client, _, _ = app_client

    settings = await client.get("/v1/settings", headers=auth_headers())
    dashboard = await client.get("/v1/dashboard/summary", headers=auth_headers())
    toolboxes = await client.get("/v1/toolboxes", headers=auth_headers())
    services = await client.get("/v1/mcp-services", headers=auth_headers())
    clients = await client.get("/v1/external-connections", headers=auth_headers())
    invocations = await client.get("/v1/tool-invocations", headers=auth_headers())

    assert settings.status_code == 200
    assert settings.json()["auth_mode"] == "static_bearer"
    assert dashboard.status_code == 200
    assert "metrics" in dashboard.json()
    assert "calls_24h" in dashboard.json()
    assert "service_status_counts" in dashboard.json()
    assert toolboxes.json()["items"][0]["id"] == "tbx_default"
    assert services.json() == {"items": [], "next_cursor": None}
    assert clients.json() == {"items": [], "next_cursor": None}
    assert invocations.status_code == 200


@pytest.mark.asyncio
async def test_client_token_issue_verify_and_revoke(app_client):
    client, _, _ = app_client

    connection = await client.post(
        "/v1/external-connections",
        headers=auth_headers(),
        json={"client_type": "claude_code", "client_name": "Claude Code (MacBook)"},
    )
    assert connection.status_code == 201
    connection_id = connection.json()["id"]

    issued = await client.post(
        "/v1/settings/client-tokens",
        headers=auth_headers(),
        json={"external_connection_id": connection_id, "scopes": ["mcp:tools.read", "mcp:tools.call"]},
    )
    assert issued.status_code == 201
    token_payload = issued.json()
    assert token_payload["token"].startswith("cmcp_client_")
    assert "token_hash" not in token_payload

    ping = await client.post(
        "/mcp",
        headers=auth_headers(token_payload["token"]),
        json={"jsonrpc": "2.0", "id": 99, "method": "ping"},
    )
    assert ping.status_code == 200
    assert ping.json()["result"] == {}

    admin_only = await client.get("/v1/settings", headers=auth_headers(token_payload["token"]))
    assert admin_only.status_code == 401

    revoked = await client.delete(f"/v1/settings/client-tokens/{token_payload['id']}", headers=auth_headers())
    assert revoked.status_code == 202

    ping_after_revoke = await client.post(
        "/mcp",
        headers=auth_headers(token_payload["token"]),
        json={"jsonrpc": "2.0", "id": 100, "method": "ping"},
    )
    assert ping_after_revoke.status_code == 401


@pytest.mark.asyncio
async def test_codex_cli_external_connection_can_issue_client_token(app_client):
    client, _, _ = app_client

    connection = await client.post(
        "/v1/external-connections",
        headers=auth_headers(),
        json={"client_type": "codex_cli", "client_name": "Codex CLI exec (local)"},
    )
    assert connection.status_code == 201
    assert connection.json()["client_type"] == "codex_cli"

    issued = await client.post(
        "/v1/settings/client-tokens",
        headers=auth_headers(),
        json={
            "external_connection_id": connection.json()["id"],
            "scopes": ["mcp:tools.read", "mcp:tools.call"],
        },
    )
    assert issued.status_code == 201
    assert issued.json()["token"].startswith("cmcp_client_")


@pytest.mark.asyncio
async def test_client_token_scopes_are_validated_and_enforced(app_client):
    client, recorder, app = app_client

    connection = await client.post(
        "/v1/external-connections",
        headers=auth_headers(),
        json={"client_type": "claude_code", "client_name": "Read Only Client"},
    )
    connection_id = connection.json()["id"]

    invalid = await client.post(
        "/v1/settings/client-tokens",
        headers=auth_headers(),
        json={"external_connection_id": connection_id, "scopes": ["mcp:tools.delete"]},
    )
    assert invalid.status_code == 422

    issued = await client.post(
        "/v1/settings/client-tokens",
        headers=auth_headers(),
        json={"external_connection_id": connection_id, "scopes": ["mcp:tools.read"]},
    )
    assert issued.status_code == 201
    read_only_token = issued.json()["token"]

    init = await initialize(client, token=read_only_token)
    session_id = init.headers["Mcp-Session-Id"]
    listed = await client.post(
        "/mcp",
        headers={**auth_headers(read_only_token), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 311, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200
    assert "tools" in listed.json()["result"]

    before_calls = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])
    denied = await client.post(
        "/mcp",
        headers={**auth_headers(read_only_token), "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": 312,
            "method": "tools/call",
            "params": {"name": "fake.echo", "arguments": {"text": "blocked-by-scope"}},
        },
    )
    after_calls = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])
    assert denied.status_code == 200
    assert denied.json()["error"]["code"] == -32001
    assert denied.json()["error"]["data"]["required_scope"] == "mcp:tools.call"
    assert after_calls == before_calls

    audit = await app.state.repository.recent_audit_logs(limit=5, action="policy.deny")
    assert audit and audit[0]["metadata"]["required_scope"] == "mcp:tools.call"


@pytest.mark.asyncio
async def test_service_validation_toolbox_catalog_and_db_backed_call(app_client):
    client, recorder, app = app_client

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Fake MCP", "slug": "fake", "endpoint_url": "http://fake.local/mcp"},
    )
    assert created.status_code == 201
    service_id = created.json()["id"]

    validation_request_id = "req-validate-service"
    validation = await client.post(
        f"/v1/mcp-services/{service_id}/validate",
        headers={**auth_headers(), "X-Request-ID": validation_request_id},
    )
    assert validation.status_code == 200
    assert validation.json()["status"] == "success"
    assert validation.json()["tools_found"] == 2
    assert validation.json()["schema_drift"]["changed_tool_count"] == 2
    assert {item["name"] for item in validation.json()["schema_diff"]["added"]} == {"echo", "error"}
    assert validation.json()["resource_prompt_catalog"] == {
        "resources_found": 2,
        "resource_templates_found": 1,
        "prompts_found": 1,
        "resources_supported": True,
        "resource_templates_supported": True,
        "prompts_supported": True,
    }
    assert all(request["headers"].get("x-request-id") == validation_request_id for request in recorder.requests[-2:])
    audit = await app.state.repository.recent_audit_logs(limit=5, action="service.validate.success")
    assert audit and audit[0]["request_id"] == validation_request_id

    second_validation = await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())
    assert second_validation.status_code == 200
    assert second_validation.json()["schema_drift"]["changed_tool_count"] == 0
    assert second_validation.json()["schema_diff"] == {"added": [], "removed": [], "changed": []}

    tools = await client.get(f"/v1/mcp-services/{service_id}/tools", headers=auth_headers())
    assert tools.status_code == 200
    assert {tool["exposed_name"] for tool in tools.json()["items"]} == {"fake.echo", "fake.error"}

    added = await client.post(
        "/v1/toolboxes/tbx_default/items",
        headers=auth_headers(),
        json={"service_id": service_id, "enabled": True},
    )
    assert added.status_code == 201

    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]
    assert "resources" in init.json()["result"]["capabilities"]
    assert "prompts" in init.json()["result"]["capabilities"]
    listed = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 200, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200
    assert [tool["name"] for tool in listed.json()["result"]["tools"]] == ["fake.echo", "fake.error"]

    called = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": 201,
            "method": "tools/call",
            "params": {"name": "fake.echo", "arguments": {"text": "from-db"}},
        },
    )
    assert called.status_code == 200
    assert called.json()["result"]["content"][0]["text"] == "echo:from-db"
    resources = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 202, "method": "resources/list", "params": {}},
    )
    assert resources.status_code == 200
    assert {item["uri"] for item in resources.json()["result"]["resources"]} == {"memory://note/1", "memory://large"}
    resource_list_calls = len([request for request in recorder.requests if request["body"]["method"] == "resources/list"])
    assert resource_list_calls == 2
    read = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 203, "method": "resources/read", "params": {"uri": "memory://note/1"}},
    )
    assert read.status_code == 200
    assert read.json()["result"]["contents"][0]["text"] == "hello resource"
    large_read = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 206, "method": "resources/read", "params": {"uri": "memory://large"}},
    )
    assert large_read.status_code == 200
    large_payload = large_read.json()["result"]
    assert large_payload["_meta"]["coremcp"]["truncated"] is True
    assert large_payload["contents"][0]["_meta"]["coremcp"]["originalLength"] == 25_000
    assert len(large_payload["contents"][0]["text"]) < 25_000
    prompt_list = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 204, "method": "prompts/list", "params": {}},
    )
    assert prompt_list.status_code == 200
    assert prompt_list.json()["result"]["prompts"][0]["name"] == "fake.summarize"
    prompt_list_calls = len([request for request in recorder.requests if request["body"]["method"] == "prompts/list"])
    assert prompt_list_calls == 2
    prompt = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 205, "method": "prompts/get", "params": {"name": "fake.summarize"}},
    )
    assert prompt.status_code == 200
    assert prompt.json()["result"]["messages"][0]["content"]["text"] == "Summarize this."
    assert await app.state.repository.count_invocations() >= 2


@pytest.mark.asyncio
async def test_resources_read_catalog_miss_with_active_service_does_not_broadcast(app_client):
    client, recorder, _ = app_client
    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Strict Resource Fake", "slug": "strict-resource", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]
    assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
    assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201
    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]

    before = len([request for request in recorder.requests if request["body"]["method"] == "resources/read"])
    missing = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 207, "method": "resources/read", "params": {"uri": "memory://missing"}},
    )
    after = len([request for request in recorder.requests if request["body"]["method"] == "resources/read"])

    assert missing.status_code == 200
    assert missing.json()["error"]["code"] == -32602
    assert missing.json()["error"]["message"] == "Unknown resource"
    assert after == before


@pytest.mark.asyncio
async def test_duplicate_resource_uri_uses_shadow_policy(app_client):
    client, recorder, app = app_client
    service_ids: list[str] = []
    for suffix in ("a", "b"):
        created = await client.post(
            "/v1/mcp-services",
            headers=auth_headers(),
            json={"name": f"Duplicate Resource {suffix}", "slug": f"dup-resource-{suffix}", "endpoint_url": "http://fake.local/mcp"},
        )
        service_id = created.json()["id"]
        service_ids.append(service_id)
        assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
        assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201

    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]
    before = len([request for request in recorder.requests if request["body"]["method"] == "resources/read"])
    collided = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 208, "method": "resources/read", "params": {"uri": "memory://note/1"}},
    )
    after = len([request for request in recorder.requests if request["body"]["method"] == "resources/read"])

    assert len(service_ids) == 2
    assert collided.status_code == 200
    assert collided.json()["result"]["contents"][0]["text"] == "hello resource"
    assert after == before + 1
    resources = await app.state.repository.list_catalog_resources()
    assert [row["uri"] for row in resources].count("memory://note/1") == 1
    audit = await app.state.repository.recent_audit_logs(limit=5, action="resource.shadow")
    assert audit and audit[0]["metadata"]["active_service_id"] == service_ids[-1]


@pytest.mark.asyncio
async def test_http_downstream_session_id_is_mapped_after_initialize(tmp_path: Path):
    records: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        records.append({"headers": dict(request.headers), "body": body})
        method = body.get("method")
        request_id = body.get("id")
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "downstream-session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"protocolVersion": body.get("params", {}).get("protocolVersion")},
                },
            )
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [{"name": "echo", "inputSchema": {"type": "object"}}],
                        "nextCursor": None,
                    },
                },
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "result": {"resources": [], "prompts": []}})

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "downstream-session.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "downstream-session-secrets.json",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            created = await client.post(
                "/v1/mcp-services",
                headers=auth_headers(),
                json={"name": "Session MCP", "slug": "session-mcp", "endpoint_url": "http://fake.local/mcp"},
            )
            service_id = created.json()["id"]
            validated = await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())
            assert validated.status_code == 200

    tools_list_request = next(record for record in records if record["body"]["method"] == "tools/list")
    assert records[0]["body"]["method"] == "initialize"
    assert records[0]["headers"].get("mcp-session-id") is None
    assert tools_list_request["headers"].get("mcp-session-id") == "downstream-session-1"
    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_downstream_session_ttl_expires_before_forwarding(tmp_path: Path):
    records: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        records.append({"headers": dict(request.headers), "body": body})
        method = body.get("method")
        request_id = body.get("id")
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "downstream-session-ttl"},
                json={"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": body.get("params", {}).get("protocolVersion")}},
            )
        if method == "tools/list":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}], "nextCursor": None}},
            )
        if method == "tools/call":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "ttl-ok"}]}},
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "downstream-session-ttl.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "downstream-session-ttl-secrets.json",
            COREMCP_DOWNSTREAM_SESSION_TTL_SECONDS=1,
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            created = await client.post(
                "/v1/mcp-services",
                headers=auth_headers(),
                json={"name": "TTL MCP", "slug": "ttl-mcp", "endpoint_url": "http://fake.local/mcp"},
            )
            service_id = created.json()["id"]
            assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
            app.state.downstream_sessions[service_id]["expires_at"] = time.time() - 1
            assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201
            init = await initialize(client)
            called = await client.post(
                "/mcp",
                headers={**auth_headers(), "Mcp-Session-Id": init.headers["Mcp-Session-Id"]},
                json={"jsonrpc": "2.0", "id": "ttl-call", "method": "tools/call", "params": {"name": "ttl-mcp.echo", "arguments": {}}},
            )
            assert called.status_code == 200

    tool_call = next(record for record in records if record["body"]["method"] == "tools/call")
    assert tool_call["headers"].get("mcp-session-id") is None
    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_circuit_open_invalidates_downstream_session(app_client):
    client, _, app = app_client
    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Circuit Session Fake", "slug": "circuit-session", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]
    app.state.downstream_sessions[service_id] = {
        "session_id": "stale-session",
        "updated_at": time.time(),
        "expires_at": time.time() + 3600,
    }
    for _ in range(app.state.circuit_breaker.failure_threshold):
        main_module._record_downstream_failure(app, service_id)

    assert service_id not in app.state.downstream_sessions


@pytest.mark.asyncio
async def test_credential_changes_invalidate_downstream_session(app_client):
    client, _, app = app_client
    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Credential Session Fake", "slug": "credential-session", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]

    app.state.downstream_sessions[service_id] = {
        "session_id": "credential-session-1",
        "updated_at": time.time(),
        "expires_at": time.time() + 3600,
    }
    put = await client.put(
        f"/v1/mcp-services/{service_id}/credential",
        headers=auth_headers(),
        json={"secret": "first-secret", "credential_type": "bearer_token"},
    )
    assert put.status_code == 200
    assert service_id not in app.state.downstream_sessions

    app.state.downstream_sessions[service_id] = {
        "session_id": "credential-session-2",
        "updated_at": time.time(),
        "expires_at": time.time() + 3600,
    }
    rotated = await client.post(
        f"/v1/mcp-services/{service_id}/credential/rotate",
        headers=auth_headers(),
        json={"secret": "second-secret", "credential_type": "bearer_token"},
    )
    assert rotated.status_code == 200
    assert service_id not in app.state.downstream_sessions

    app.state.downstream_sessions[service_id] = {
        "session_id": "credential-session-3",
        "updated_at": time.time(),
        "expires_at": time.time() + 3600,
    }
    deleted = await client.delete(f"/v1/mcp-services/{service_id}/credential", headers=auth_headers())
    assert deleted.status_code == 202
    assert service_id not in app.state.downstream_sessions

    app.state.downstream_sessions[service_id] = {
        "session_id": "credential-session-4",
        "updated_at": time.time(),
        "expires_at": time.time() + 3600,
    }
    patched = await client.patch(
        f"/v1/mcp-services/{service_id}",
        headers=auth_headers(),
        json={"auth_type": "none"},
    )
    assert patched.status_code == 200
    assert service_id not in app.state.downstream_sessions


@pytest.mark.asyncio
async def test_dynamic_capabilities_omit_resources_and_prompts_when_services_do_not_support_them(tmp_path: Path):
    async def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        method = body.get("method")
        request_id = body.get("id")
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": body.get("params", {}).get("protocolVersion"),
                        "capabilities": {"tools": {"listChanged": True}},
                    },
                },
            )
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}], "nextCursor": None},
                },
            )
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}},
        )

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "dynamic-caps.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "dynamic-caps-secrets.json",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            created = await client.post(
                "/v1/mcp-services",
                headers=auth_headers(),
                json={"name": "Tools Only", "slug": "tools-only", "endpoint_url": "http://fake.local/mcp"},
            )
            service_id = created.json()["id"]
            validated = await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())
            assert validated.status_code == 200
            assert validated.json()["resource_prompt_catalog"]["resources_supported"] is False
            assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201

            init = await initialize(client)
            caps = init.json()["result"]["capabilities"]
            assert "tools" in caps
            assert "resources" not in caps
            assert "prompts" not in caps
    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_tool_args_schema_validation_blocks_downstream_call(app_client):
    client, recorder, app = app_client
    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Schema Fake", "slug": "schema-fake", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]
    assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
    assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201
    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]

    before = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])
    invalid = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": "invalid-args",
            "method": "tools/call",
            "params": {"name": "schema-fake.echo", "arguments": {}},
        },
    )
    after = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])

    assert invalid.status_code == 200
    assert invalid.json()["error"]["code"] == -32602
    assert "text" in invalid.json()["error"]["data"]["details"]
    assert after == before
    audit = await app.state.repository.recent_audit_logs(limit=5, action="policy.invalid_args")
    assert audit and audit[0]["resource_type"] == "service_tool"


@pytest.mark.asyncio
async def test_plugin_before_failure_returns_tool_error_without_downstream_call(app_client):
    class _FailBeforePlugin:
        name = "fail-before"

        async def before_tool_call(self, context: ToolCallContext, arguments: Any) -> Any:
            raise RuntimeError("before boom")

        async def after_tool_response(self, context: ToolCallContext, result: dict[str, Any]) -> dict[str, Any]:
            return result

    client, recorder, app = app_client
    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Plugin Before Fake", "slug": "plugin-before", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]
    assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
    assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201
    app.state.plugins = PluginRegistry([_FailBeforePlugin()])

    init = await initialize(client)
    before = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])
    response = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": init.headers["Mcp-Session-Id"]},
        json={
            "jsonrpc": "2.0",
            "id": "plugin-before",
            "method": "tools/call",
            "params": {"name": "plugin-before.echo", "arguments": {"text": "blocked"}},
        },
    )
    after = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert response.json()["result"]["_meta"]["coremcp"]["error_code"] == "plugin_error"
    assert after == before
    audit = await app.state.repository.recent_audit_logs(limit=5, action="plugin.error")
    assert audit[0]["metadata"] == {
        "tool": "plugin-before.echo",
        "plugin_name": "fail-before",
        "stage": "before_tool_call",
        "error_type": "RuntimeError",
    }


@pytest.mark.asyncio
async def test_plugin_after_failure_returns_tool_error_after_downstream_success(app_client):
    class _FailAfterPlugin:
        name = "fail-after"

        async def before_tool_call(self, context: ToolCallContext, arguments: Any) -> Any:
            return arguments

        async def after_tool_response(self, context: ToolCallContext, result: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("after boom")

    client, recorder, app = app_client
    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Plugin After Fake", "slug": "plugin-after", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]
    assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
    assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201
    app.state.plugins = PluginRegistry([_FailAfterPlugin()])

    init = await initialize(client)
    before = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])
    response = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": init.headers["Mcp-Session-Id"]},
        json={
            "jsonrpc": "2.0",
            "id": "plugin-after",
            "method": "tools/call",
            "params": {"name": "plugin-after.echo", "arguments": {"text": "redact-me"}},
        },
    )
    after = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert response.json()["result"]["_meta"]["coremcp"]["error_code"] == "plugin_error"
    assert after == before + 1
    audit = await app.state.repository.recent_audit_logs(limit=5, action="plugin.error")
    assert audit[0]["metadata"] == {
        "tool": "plugin-after.echo",
        "plugin_name": "fail-after",
        "stage": "after_tool_response",
        "error_type": "RuntimeError",
    }


@pytest.mark.asyncio
async def test_per_service_rate_limit_blocks_repeated_tool_calls(tmp_path: Path):
    recorder = DownstreamRecorder()
    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "service-rate.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "service-rate-secrets.json",
            COREMCP_SERVICE_RATE_LIMIT_PER_MINUTE=1,
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            created = await client.post(
                "/v1/mcp-services",
                headers=auth_headers(),
                json={"name": "Rate Fake", "slug": "rate-fake", "endpoint_url": "http://fake.local/mcp"},
            )
            service_id = created.json()["id"]
            assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
            assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201
            init = await initialize(client)
            session_id = init.headers["Mcp-Session-Id"]
            payload = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "rate-fake.echo", "arguments": {"text": "limited"}},
            }
            first = await client.post("/mcp", headers={**auth_headers(), "Mcp-Session-Id": session_id}, json={**payload, "id": "rate-1"})
            second = await client.post("/mcp", headers={**auth_headers(), "Mcp-Session-Id": session_id}, json={**payload, "id": "rate-2"})

            assert first.status_code == 200
            assert first.json()["result"]["content"][0]["text"] == "echo:limited"
            assert second.status_code == 200
            assert second.json()["result"]["isError"] is True
            assert second.json()["result"]["_meta"]["coremcp"]["error_code"] == "rate_limited"
    downstream_calls = [request for request in recorder.requests if request["body"]["method"] == "tools/call"]
    assert len(downstream_calls) == 1
    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_tools_list_includes_unavailable_service_metadata(app_client):
    client, _, app = app_client
    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Unavailable Fake", "slug": "unavailable", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]
    assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
    assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201
    for _ in range(app.state.circuit_breaker.failure_threshold):
        app.state.circuit_breaker.record_failure(service_id)

    init = await initialize(client)
    listed = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": init.headers["Mcp-Session-Id"]},
        json={"jsonrpc": "2.0", "id": "unavailable-list", "method": "tools/list", "params": {}},
    )

    assert listed.status_code == 200
    unavailable = listed.json()["result"]["_meta"]["coremcp"]["unavailable_services"]
    assert unavailable[0]["service_id"] == service_id
    assert unavailable[0]["status"] == "circuit_open"


@pytest.mark.asyncio
async def test_health_probe_detects_tool_schema_drift_and_refreshes_catalog(tmp_path: Path):
    version = {"value": 1}

    async def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        method = body.get("method")
        request_id = body.get("id")
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": body.get("params", {}).get("protocolVersion"), "capabilities": {"tools": {}}}},
            )
        if method == "tools/list":
            tools = [{"name": "echo", "inputSchema": {"type": "object"}}]
            if version["value"] == 2:
                tools.append({"name": "new_tool", "inputSchema": {"type": "object"}})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools, "nextCursor": None}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "health-drift.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "health-drift-secrets.json",
            COREMCP_SERVICE_HEALTH_PROBE_ENABLED=False,
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            created = await client.post(
                "/v1/mcp-services",
                headers=auth_headers(),
                json={"name": "Drift MCP", "slug": "drift", "endpoint_url": "http://fake.local/mcp"},
            )
            service_id = created.json()["id"]
            assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
            version["value"] = 2
            probe = await _run_service_health_probe_once(app)
            assert probe == {"checked": 1, "failed": 0}
            tools = await client.get(f"/v1/mcp-services/{service_id}/tools", headers=auth_headers())
            exposed = {item["exposed_name"] for item in tools.json()["items"]}
            assert exposed == {"drift.echo", "drift.new_tool"}
    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_service_health_probe_updates_service_health_fields(app_client):
    client, recorder, app = app_client

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Health Probe Fake", "slug": "health-probe", "endpoint_url": "http://fake.local/mcp", "auth_type": "none"},
    )
    assert created.status_code == 201
    service_id = created.json()["id"]
    assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200

    summary = await _run_service_health_probe_once(app)

    assert summary["checked"] >= 1
    assert summary["failed"] == 0
    service = await app.state.repository.get_mcp_service(service_id)
    assert service is not None
    assert service["last_health_check_at"] is not None
    assert int(service["consecutive_failures"]) == 0
    assert any(item["body"]["method"] == "initialize" and str(item["body"]["id"]).startswith("health-") for item in recorder.requests)


@pytest.mark.asyncio
async def test_service_private_metadata_create_list_and_patch(app_client):
    client, _, _ = app_client

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={
            "name": "Docs MCP",
            "slug": "docs",
            "endpoint_url": "http://fake.local/mcp",
            "category": "knowledge",
            "homepage_url": "https://example.com",
            "documentation_url": "https://example.com/docs",
            "logo_url": "https://example.com/icon.png",
        },
    )
    assert created.status_code == 201
    service_id = created.json()["id"]
    assert created.json()["category"] == "knowledge"
    assert created.json()["documentation_url"] == "https://example.com/docs"

    listed = await client.get("/v1/mcp-services", headers=auth_headers())
    service = next(item for item in listed.json()["items"] if item["id"] == service_id)
    assert service["category"] == "knowledge"
    assert service["homepage_url"] == "https://example.com"

    patched = await client.patch(
        f"/v1/mcp-services/{service_id}",
        headers=auth_headers(),
        json={"category": "personal-docs", "logo_url": None},
    )
    assert patched.status_code == 200
    assert patched.json()["category"] == "personal-docs"
    assert patched.json()["logo_url"] is None


@pytest.mark.asyncio
async def test_stdio_service_metadata_create_filters_sensitive_env(app_client):
    client, _, _ = app_client

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={
            "name": "Local Stdio MCP",
            "slug": "local-stdio",
            "transport_type": "stdio",
            "stdio_command": "/usr/bin/python3",
            "stdio_args": ["server.py"],
            "stdio_env": {
                "SAFE_ENV": "visible",
                "COREMCP_CLIENT_TOKEN": "must-not-store",
                "COREMCP_CLIENT_TOKEN_VALUE": "must-not-store-either",
                "Authorization": "Bearer must-not-store",
            },
            "stdio_idle_timeout_seconds": 60,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["transport_type"] == "stdio"
    assert body["endpoint_url"] == "stdio://local-stdio"
    assert body["stdio_command"] == "/usr/bin/python3"
    assert body["stdio_args"] == ["server.py"]
    assert body["stdio_env"] == {"SAFE_ENV": "visible"}
    assert body["stdio_idle_timeout_seconds"] == 60

    patched = await client.patch(
        f"/v1/mcp-services/{body['id']}",
        headers=auth_headers(),
        json={"stdio_env": {"COREMCP_ADMIN_TOKEN": "blocked", "NEXT_PUBLIC_SAFE": "ok"}},
    )
    assert patched.status_code == 200
    assert patched.json()["stdio_env"] == {"NEXT_PUBLIC_SAFE": "ok"}


@pytest.mark.asyncio
async def test_stdio_service_rejects_disallowed_command_and_audits(app_client):
    client, _, app = app_client

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={
            "name": "Shell Stdio MCP",
            "slug": "shell-stdio",
            "transport_type": "stdio",
            "stdio_command": "/bin/sh",
            "stdio_args": ["-c", "echo unsafe"],
        },
    )

    assert created.status_code == 422
    assert created.json()["error"]["code"] == "validation_failed"
    assert "not allowed" in created.json()["error"]["message"]
    assert "/bin/sh" not in json.dumps(created.json())

    audit = await app.state.repository.recent_audit_logs(limit=5, action="service.stdio_command_rejected")
    assert audit
    assert audit[0]["metadata"]["command_basename"] == "sh"
    assert "/bin/sh" not in json.dumps(audit[0], default=str)


@pytest.mark.asyncio
async def test_stdio_service_patch_rejects_disallowed_command_and_keeps_existing(app_client):
    client, _, app = app_client

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={
            "name": "Patch Safe Stdio MCP",
            "slug": "patch-safe-stdio",
            "transport_type": "stdio",
            "stdio_command": sys.executable,
        },
    )
    assert created.status_code == 201
    service_id = created.json()["id"]

    patched = await client.patch(
        f"/v1/mcp-services/{service_id}",
        headers=auth_headers(),
        json={"stdio_command": "/bin/sh"},
    )

    assert patched.status_code == 422
    assert "not allowed" in patched.json()["error"]["message"]
    service = await app.state.repository.get_mcp_service(service_id)
    assert service is not None
    assert service["stdio_command"] == sys.executable

    audit = await app.state.repository.recent_audit_logs(limit=5, action="service.stdio_command_rejected")
    assert audit and audit[0]["resource_id"] == service_id


@pytest.mark.asyncio
async def test_stdio_service_validate_and_call_through_gateway(app_client, tmp_path: Path):
    client, recorder, app = app_client
    script = tmp_path / "stdio_gateway_fixture.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import sys

            def send(message):
                print(json.dumps(message), flush=True)

            for line in sys.stdin:
                request = json.loads(line)
                method = request.get("method")
                request_id = request.get("id")
                params = request.get("params") or {}
                if method == "initialize":
                    send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": params.get("protocolVersion")}})
                elif method == "tools/list":
                    send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": "echo", "description": "Echo text", "inputSchema": {"type": "object"}}]}})
                elif method == "tools/call":
                    text = (params.get("arguments") or {}).get("text", "")
                    send({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "stdio:" + text}]}})
                else:
                    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "not found"}})
            """
        ),
        encoding="utf-8",
    )

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={
            "name": "Gateway Stdio",
            "slug": "gateway-stdio",
            "transport_type": "stdio",
            "stdio_command": sys.executable,
            "stdio_args": [str(script)],
        },
    )
    assert created.status_code == 201
    service_id = created.json()["id"]

    validated = await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())
    assert validated.status_code == 200
    assert validated.json()["tools_found"] == 1
    assert [request["body"]["method"] for request in recorder.requests] == []
    service_after_validate = await app.state.repository.get_mcp_service(service_id)
    assert service_after_validate["last_stdio_started_at"] is not None
    assert service_after_validate["stdio_restart_count"] == 0
    added = await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})
    assert added.status_code == 201

    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]
    tools = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    exposed_name = tools.json()["result"]["tools"][0]["name"]
    called = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": exposed_name, "arguments": {"text": "ok"}}},
    )
    assert called.status_code == 200
    assert called.json()["result"]["content"] == [{"type": "text", "text": "stdio:ok"}]
    service_after_call = await app.state.repository.get_mcp_service(service_id)
    assert service_after_call["last_stdio_used_at"] is not None
    assert service_after_call["last_stdio_error"] is None


@pytest.mark.asyncio
async def test_stdio_crash_state_is_persisted(app_client, tmp_path: Path):
    client, _, app = app_client
    script = tmp_path / "stdio_crash_fixture.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import sys

            def send(message):
                print(json.dumps(message), flush=True)

            for line in sys.stdin:
                request = json.loads(line)
                method = request.get("method")
                request_id = request.get("id")
                params = request.get("params") or {}
                if method == "initialize":
                    send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": params.get("protocolVersion")}})
                elif method == "tools/list":
                    sys.stderr.write("boom from stdio fixture\\n")
                    sys.stderr.flush()
                    sys.exit(42)
            """
        ),
        encoding="utf-8",
    )

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={
            "name": "Crash Stdio",
            "slug": "crash-stdio",
            "transport_type": "stdio",
            "stdio_command": sys.executable,
            "stdio_args": [str(script)],
        },
    )
    assert created.status_code == 201
    service_id = created.json()["id"]

    validated = await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())
    assert validated.status_code == 400
    service = await app.state.repository.get_mcp_service(service_id)
    assert service["last_stdio_exit_code"] == 42
    assert "exit code 42" in service["last_stdio_error"]
    assert "boom from stdio fixture" in service["last_stdio_stderr_tail"]


@pytest.mark.asyncio
async def test_tool_override_hides_and_denies_call_without_downstream(app_client):
    client, recorder, app = app_client

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Policy Fake", "slug": "policy", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]
    assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
    assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201

    tools = await client.get(f"/v1/mcp-services/{service_id}/tools", headers=auth_headers())
    echo_tool = next(item for item in tools.json()["items"] if item["exposed_name"] == "policy.echo")
    override = await client.put(
        f"/v1/mcp-services/{service_id}/tool-overrides/{echo_tool['id']}",
        headers=auth_headers(),
        json={"enabled": False, "permission_level": "hidden"},
    )
    assert override.status_code == 200
    assert override.json()["enabled"] is False

    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]
    listed = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 220, "method": "tools/list", "params": {}},
    )
    assert "policy.echo" not in [tool["name"] for tool in listed.json()["result"]["tools"]]

    before_calls = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])
    deny_request_id = "req-tool-policy-deny"
    denied = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id, "X-Request-ID": deny_request_id},
        json={
            "jsonrpc": "2.0",
            "id": 221,
            "method": "tools/call",
            "params": {"name": "policy.echo", "arguments": {"text": "blocked"}},
        },
    )
    after_calls = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])
    assert denied.status_code == 200
    assert denied.json()["result"]["isError"] is True
    assert denied.json()["result"]["_meta"]["coremcp"]["error_code"] == "policy_denied"
    assert denied.json()["result"]["_meta"]["coremcp"]["reason"] == "tool_disabled"
    assert after_calls == before_calls
    audit = await app.state.repository.recent_audit_logs(limit=5, action="policy.deny")
    assert audit and audit[0]["metadata"]["reason"] == "tool_disabled"
    assert audit[0]["request_id"] == deny_request_id


@pytest.mark.asyncio
async def test_tool_override_visible_only_lists_but_denies_call(app_client):
    client, _, _ = app_client

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Visible Only", "slug": "visible", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]
    assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
    assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201

    tools = await client.get(f"/v1/mcp-services/{service_id}/tools", headers=auth_headers())
    echo_tool = next(item for item in tools.json()["items"] if item["exposed_name"] == "visible.echo")
    override = await client.put(
        f"/v1/mcp-services/{service_id}/tool-overrides/{echo_tool['id']}",
        headers=auth_headers(),
        json={"enabled": True, "permission_level": "visible_only"},
    )
    assert override.status_code == 200

    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]
    listed = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 230, "method": "tools/list", "params": {}},
    )
    assert "visible.echo" in [tool["name"] for tool in listed.json()["result"]["tools"]]

    denied = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": 231,
            "method": "tools/call",
            "params": {"name": "visible.echo", "arguments": {"text": "blocked"}},
        },
    )
    assert denied.status_code == 200
    assert denied.json()["result"]["_meta"]["coremcp"]["error_code"] == "policy_denied"
    assert denied.json()["result"]["_meta"]["coremcp"]["reason"] == "tool_permission_visible_only"


@pytest.mark.asyncio
async def test_tool_policy_presets_apply_readonly_dangerous_off_and_full_access(tmp_path: Path):
    recorder = PresetPolicyRecorder()
    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "preset.sqlite3",
            FAKE_MCP_URL="http://preset.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="preset.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "preset-secrets.json",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            created = await client.post(
                "/v1/mcp-services",
                headers=auth_headers(),
                json={"name": "Preset", "slug": "preset", "endpoint_url": "http://preset.local/mcp"},
            )
            service_id = created.json()["id"]
            assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
            assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201

            readonly = await client.post(
                f"/v1/mcp-services/{service_id}/tool-overrides/preset",
                headers=auth_headers(),
                json={"preset": "readonly"},
            )
            assert readonly.status_code == 200
            readonly_map = {item["exposed_name"]: item["permission_level"] for item in readonly.json()["items"]}
            assert readonly_map == {
                "preset.create_note": "hidden",
                "preset.delete_note": "hidden",
                "preset.search": "callable",
            }

            init = await initialize(client)
            session_id = init.headers["Mcp-Session-Id"]
            listed = await client.post(
                "/mcp",
                headers={**auth_headers(), "Mcp-Session-Id": session_id},
                json={"jsonrpc": "2.0", "id": 240, "method": "tools/list", "params": {}},
            )
            assert [tool["name"] for tool in listed.json()["result"]["tools"]] == ["preset.search"]

            dangerous_off = await client.post(
                f"/v1/mcp-services/{service_id}/tool-overrides/preset",
                headers=auth_headers(),
                json={"preset": "dangerous_off"},
            )
            assert dangerous_off.status_code == 200
            dangerous_map = {item["exposed_name"]: item["permission_level"] for item in dangerous_off.json()["items"]}
            assert dangerous_map["preset.delete_note"] == "hidden"
            assert dangerous_map["preset.create_note"] == "callable"
            assert dangerous_map["preset.search"] == "callable"

            full_access = await client.post(
                f"/v1/mcp-services/{service_id}/tool-overrides/preset",
                headers=auth_headers(),
                json={"preset": "full_access"},
            )
            assert full_access.status_code == 200
            assert {item["permission_level"] for item in full_access.json()["items"]} == {"callable"}

    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_service_validation_blocks_metadata_endpoint_and_audits(app_client):
    client, _, _ = app_client

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Blocked", "slug": "blocked", "endpoint_url": "http://169.254.169.254/latest"},
    )
    assert created.status_code == 201

    validation = await client.post(f"/v1/mcp-services/{created.json()['id']}/validate", headers=auth_headers())
    assert validation.status_code == 400
    assert validation.json()["error"]["code"] == "unsafe_endpoint"

    audit = await client.get("/v1/audit-logs", headers=auth_headers())
    assert audit.status_code == 200
    assert any(item["action"] == "ssrf.block" for item in audit.json()["items"])


@pytest.mark.asyncio
async def test_service_slug_partial_unique_allows_recreate_after_soft_delete(app_client):
    client, _, _ = app_client

    first = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Recreate", "slug": "recreate", "endpoint_url": "http://fake.local/mcp"},
    )
    assert first.status_code == 201

    duplicate = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Recreate 2", "slug": "recreate", "endpoint_url": "http://fake.local/mcp"},
    )
    assert duplicate.status_code == 409

    deleted = await client.delete(f"/v1/mcp-services/{first.json()['id']}", headers=auth_headers())
    assert deleted.status_code == 202

    second = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Recreate 2", "slug": "recreate", "endpoint_url": "http://fake.local/mcp"},
    )
    assert second.status_code == 201


@pytest.mark.asyncio
async def test_credential_vault_masks_response_and_keeps_plaintext_out_of_db(app_client):
    client, _, app = app_client
    secret = "super-secret-token"

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Needs Auth", "slug": "needs-auth", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]

    put = await client.put(
        f"/v1/mcp-services/{service_id}/credential",
        headers=auth_headers(),
        json={"credential_type": "bearer_token", "secret": secret},
    )
    assert put.status_code == 200
    assert secret not in put.text
    assert put.json()["masked"] == "supe••••oken"

    credential = await app.state.repository.get_service_credential(service_id)
    assert credential is not None
    assert secret not in credential["secret_ref"]
    assert await app.state.vault.get(credential["secret_ref"]) == secret
    vault_blob = app.state.settings.resolved_secrets_file.read_text(encoding="utf-8")
    assert secret not in vault_blob
    assert base64.urlsafe_b64encode(secret.encode("utf-8")).decode("ascii") not in vault_blob
    assert app.state.settings.resolved_fernet_key_file.exists()


@pytest.mark.asyncio
async def test_fernet_backend_reads_legacy_base64_and_writes_ciphertext(tmp_path: Path):
    vault = FernetBackend(tmp_path / "secrets.json", tmp_path / "fernet.key")
    assert await vault.is_ready()

    legacy_ref = "fernet:coremcp:svc_legacy:legacy"
    legacy_secret = "legacy-secret-token"
    (tmp_path / "secrets.json").write_text(
        json.dumps({legacy_ref: base64.urlsafe_b64encode(legacy_secret.encode("utf-8")).decode("ascii")}),
        encoding="utf-8",
    )
    assert await vault.get(legacy_ref) == legacy_secret

    secret_ref = await vault.put(service_id="svc_modern", secret="modern-secret-token")
    blob = (tmp_path / "secrets.json").read_text(encoding="utf-8")
    assert "modern-secret-token" not in blob
    assert '"ciphertext"' in blob
    assert await vault.get(secret_ref) == "modern-secret-token"


@pytest.mark.asyncio
async def test_keychain_backend_does_not_put_secret_in_process_args(tmp_path: Path, monkeypatch):
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(args: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("coremcp.credentials.vault.subprocess.run", fake_run)
    fallback = FernetBackend(tmp_path / "secrets.json", tmp_path / "fernet.key")
    vault = KeychainBackend(fallback)
    vault.available = True

    secret_ref = await vault.put(service_id="svc_keychain", secret="super-secret-token")

    assert secret_ref.startswith("keychain:coremcp:")
    args, kwargs = calls[0]
    assert "super-secret-token" not in args
    assert args[-1] == "-w"
    assert kwargs["input"] == "super-secret-token\n"


@pytest.mark.asyncio
async def test_credential_rotate_replaces_secret_and_deletes_previous(app_client):
    client, _, app = app_client

    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Rotate Auth", "slug": "rotate-auth", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]

    put = await client.put(
        f"/v1/mcp-services/{service_id}/credential",
        headers=auth_headers(),
        json={"credential_type": "bearer_token", "secret": "old-secret-token"},
    )
    assert put.status_code == 200
    previous = await app.state.repository.get_service_credential(service_id)
    assert previous is not None

    rotated = await client.post(
        f"/v1/mcp-services/{service_id}/credential/rotate",
        headers=auth_headers(),
        json={"secret": "new-secret-token"},
    )
    assert rotated.status_code == 200
    assert "new-secret-token" not in rotated.text

    current = await app.state.repository.get_service_credential(service_id)
    assert current is not None
    assert current["secret_ref"] != previous["secret_ref"]
    assert await app.state.vault.get(previous["secret_ref"]) is None
    assert await app.state.vault.get(current["secret_ref"]) == "new-secret-token"


@pytest.mark.asyncio
async def test_idempotency_key_reuses_tools_call_result(app_client):
    client, recorder, _ = app_client
    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Idempotent Fake", "slug": "idem", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]
    await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())
    await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})
    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]
    call_headers = {**auth_headers(), "Mcp-Session-Id": session_id, "Idempotency-Key": "idem-key-1"}

    first = await client.post(
        "/mcp",
        headers=call_headers,
        json={
            "jsonrpc": "2.0",
            "id": 301,
            "method": "tools/call",
            "params": {"name": "idem.echo", "arguments": {"text": "first"}},
        },
    )
    second = await client.post(
        "/mcp",
        headers=call_headers,
        json={
            "jsonrpc": "2.0",
            "id": 302,
            "method": "tools/call",
            "params": {"name": "idem.echo", "arguments": {"text": "second"}},
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["result"]["content"][0]["text"] == "echo:first"
    assert second.json()["id"] == 302
    assert second.json()["result"]["content"][0]["text"] == "echo:first"
    downstream_tool_calls = [request for request in recorder.requests if request["body"]["method"] == "tools/call"]
    assert len(downstream_tool_calls) == 1
    assert downstream_tool_calls[0]["headers"].get("idempotency-key") == "idem-key-1"


@pytest.mark.asyncio
async def test_idempotency_cache_is_invalidated_by_tool_policy_change(app_client):
    client, _, _ = app_client
    created = await client.post(
        "/v1/mcp-services",
        headers=auth_headers(),
        json={"name": "Policy Cached Fake", "slug": "idem-policy", "endpoint_url": "http://fake.local/mcp"},
    )
    service_id = created.json()["id"]
    await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())
    await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})
    tools = await client.get(f"/v1/mcp-services/{service_id}/tools", headers=auth_headers())
    echo_tool = next(item for item in tools.json()["items"] if item["exposed_name"] == "idem-policy.echo")

    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]
    call_headers = {**auth_headers(), "Mcp-Session-Id": session_id, "Idempotency-Key": "idem-policy-key"}
    first = await client.post(
        "/mcp",
        headers=call_headers,
        json={
            "jsonrpc": "2.0",
            "id": 311,
            "method": "tools/call",
            "params": {"name": "idem-policy.echo", "arguments": {"text": "allowed"}},
        },
    )
    assert first.status_code == 200
    assert first.json()["result"]["content"][0]["text"] == "echo:allowed"

    override = await client.put(
        f"/v1/mcp-services/{service_id}/tool-overrides/{echo_tool['id']}",
        headers=auth_headers(),
        json={"enabled": True, "permission_level": "visible_only"},
    )
    assert override.status_code == 200

    second = await client.post(
        "/mcp",
        headers=call_headers,
        json={
            "jsonrpc": "2.0",
            "id": 312,
            "method": "tools/call",
            "params": {"name": "idem-policy.echo", "arguments": {"text": "should-not-replay"}},
        },
    )
    assert second.status_code == 200
    assert second.json()["result"]["isError"] is True
    assert second.json()["result"]["_meta"]["coremcp"]["reason"] == "tool_permission_visible_only"


@pytest.mark.asyncio
async def test_cancel_notification_is_accepted_and_logged(app_client):
    client, recorder, app = app_client
    init = await initialize(client)
    session_id = init.headers["Mcp-Session-Id"]

    cancelled = await client.post(
        "/mcp",
        headers={**auth_headers(), "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "req-long-running", "reason": "user stopped"},
        },
    )

    assert cancelled.status_code == 202
    assert any(request["body"]["method"] == "notifications/cancelled" for request in recorder.requests)
    recent = await app.state.repository.recent_invocations(limit=5)
    assert any(item["status"] == "cancelled" and item["method"] == "notifications/cancelled" for item in recent)


@pytest.mark.asyncio
async def test_one_time_connection_token_exchange_and_reuse(app_client):
    client, _, _ = app_client

    issued_otk = await client.post(
        "/v1/external-connections/one-time-token",
        headers=auth_headers(),
        json={"client_type": "openclaw", "requested_scopes": ["mcp:tools.read", "mcp:tools.call"]},
    )
    assert issued_otk.status_code == 201
    token_payload = issued_otk.json()
    one_time_token = token_payload["token"]
    assert one_time_token.startswith("cmcp_otk_")
    assert token_payload["connection_prompt"].find(one_time_token) >= 0

    exchange = await client.post(
        "/v1/external-connections/exchange",
        json={
            "one_time_token": one_time_token,
            "client_type": "openclaw",
            "client_name": "OpenClaw Test",
            "protocol_version": "2025-11-25",
        },
    )
    assert exchange.status_code == 201
    access_token = exchange.json()["access_token"]
    assert access_token.startswith("cmcp_client_")

    ping = await client.post(
        "/mcp",
        headers=auth_headers(access_token),
        json={"jsonrpc": "2.0", "id": "client-ping", "method": "ping"},
    )
    assert ping.status_code == 200
    assert ping.json()["result"] == {}

    reused = await client.post(
        "/v1/external-connections/exchange",
        json={"one_time_token": one_time_token, "client_type": "openclaw"},
    )
    assert reused.status_code == 401


@pytest.mark.asyncio
async def test_metrics_endpoint_is_opt_in(app_client, tmp_path: Path):
    client, _, _ = app_client
    assert (await client.get("/metrics")).status_code == 404

    recorder = DownstreamRecorder()
    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "metrics.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            METRICS_ENABLED=True,
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as metrics_client:
            metrics = await metrics_client.get("/metrics")
    await downstream_client.aclose()

    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers["content-type"]
    assert "coremcp_tool_invocations_total" in metrics.text
    assert "coremcp_mcp_requests_total" in metrics.text
    assert "coremcp_active_mcp_sessions" in metrics.text
    assert "coremcp_mcp_services_health_failing" in metrics.text
    assert "coremcp_mcp_services_circuit_open" in metrics.text


@pytest.mark.asyncio
async def test_auth_failure_is_audited(app_client):
    client, _, app = app_client
    response = await client.post(
        "/mcp",
        headers=auth_headers("wrong-token"),
        json={"jsonrpc": "2.0", "id": 404, "method": "ping"},
    )
    assert response.status_code == 401
    audit = await app.state.repository.recent_audit_logs(limit=5, action="auth.failure")
    assert audit and audit[0]["request_id"].startswith("req_")


@pytest.mark.asyncio
async def test_downstream_redirect_is_rejected_without_following(tmp_path: Path):
    async def redirect_transport(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest"})

    settings = Settings(
        COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
        COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
        COREMCP_DB_PATH=tmp_path / "redirect.sqlite3",
        COREMCP_SSRF_ALLOW_HOSTS="redirect.local",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(redirect_transport))
    downstream = DownstreamMcpClient("http://redirect.local/mcp", client)

    with pytest.raises(DownstreamMcpError, match="redirect"):
        await downstream.request(
            method="tools/list",
            url_safety_checker=UrlSafetyChecker(settings),
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_downstream_rejects_non_json_content_type_and_oversized_response():
    async def charset_transport(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            content=b'{"jsonrpc":"2.0","id":1,"result":{}}',
        )

    charset_client = httpx.AsyncClient(transport=httpx.MockTransport(charset_transport))
    downstream = DownstreamMcpClient("https://charset.example/mcp", charset_client)
    assert (await downstream.request(method="tools/list"))["result"] == {}
    await charset_client.aclose()

    async def non_json_transport(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b'{"jsonrpc":"2.0","id":1,"result":{}}',
        )

    non_json_client = httpx.AsyncClient(transport=httpx.MockTransport(non_json_transport))
    downstream = DownstreamMcpClient("https://content-type.example/mcp", non_json_client)
    with pytest.raises(DownstreamMcpError, match="non-JSON content-type"):
        await downstream.request(method="tools/list")
    await non_json_client.aclose()

    async def large_transport(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            content=b'{"jsonrpc":"2.0","id":1,"result":{"payload":"' + (b"x" * 256) + b'"}}',
        )

    large_client = httpx.AsyncClient(transport=httpx.MockTransport(large_transport))
    downstream = DownstreamMcpClient("https://large.example/mcp", large_client, max_response_bytes=64)
    with pytest.raises(DownstreamMcpError, match="exceeds 64 bytes"):
        await downstream.request(method="tools/list")
    await large_client.aclose()


def test_url_safety_blocks_cgnat_and_fragments(tmp_path: Path):
    checker = UrlSafetyChecker(Settings(COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token"))
    with pytest.raises(UrlSafetyError):
        checker.assert_safe("http://100.64.0.1/mcp")
    with pytest.raises(UrlSafetyError):
        checker.assert_safe("https://example.com/mcp#token")

    tailscale_checker = UrlSafetyChecker(
        Settings(COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token", ALLOW_TAILSCALE_DOWNSTREAM=True)
    )
    assert tailscale_checker.assert_safe("http://100.64.0.1/mcp").allowed_by == "public_dns"


def test_logging_redacts_sensitive_fields():
    redacted = redact_sensitive_data(
        None,
        "info",
        {
            "authorization": "Bearer cmcp_admin_secret",
            "nested": {"api_key": "key-123", "refresh_token": "rt-123"},
            "safe": "visible",
        },
    )
    assert redacted["authorization"] == REDACTED
    assert redacted["nested"]["api_key"] == REDACTED
    assert redacted["nested"]["refresh_token"] == REDACTED
    assert redacted["safe"] == "visible"


@pytest.mark.asyncio
async def test_downstream_timeout_maps_to_downstream_error():
    async def timeout_transport(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    downstream = DownstreamMcpClient("https://timeout.example/mcp", httpx.AsyncClient(transport=httpx.MockTransport(timeout_transport)))
    with pytest.raises(DownstreamTimeoutError, match="downstream request timed out"):
        await downstream.request(method="tools/list")
    await downstream.client.aclose()


@pytest.mark.asyncio
async def test_tools_call_timeout_returns_tool_error_and_logs_timeout(tmp_path: Path):
    recorder = DownstreamRecorder()

    async def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if body.get("method") == "tools/call":
            raise httpx.ReadTimeout("read timed out", request=request)
        return await recorder(request)

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "timeout.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "timeout-secrets.json",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            created = await client.post(
                "/v1/mcp-services",
                headers=auth_headers(),
                json={"name": "Timeout MCP", "slug": "timeout", "endpoint_url": "http://fake.local/mcp"},
            )
            service_id = created.json()["id"]
            assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
            assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201

            init = await initialize(client)
            session_id = init.headers["Mcp-Session-Id"]
            response = await client.post(
                "/mcp",
                headers={**auth_headers(), "Mcp-Session-Id": session_id, "X-Request-ID": "req-timeout"},
                json={
                    "jsonrpc": "2.0",
                    "id": 900,
                    "method": "tools/call",
                    "params": {"name": "timeout.echo", "arguments": {"text": "slow"}},
                },
            )

            assert response.status_code == 200
            payload = response.json()
            assert payload["result"]["isError"] is True
            assert payload["result"]["_meta"]["coremcp"]["error_code"] == "downstream_timeout"

            recent = await app.state.repository.recent_invocations(limit=5)
            assert any(
                item["request_id"] == "req-timeout"
                and item["status"] == "timeout"
                and item["error_code"] == "downstream_timeout"
                for item in recent
            )
    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_repository_marks_stuck_jobs_failed(app_client):
    _, _, app = app_client
    job = await app.state.repository.create_job(kind="service_validation", payload={"service_id": "svc_stuck"})
    await app.state.repository.update_job(job["id"], status="running", progress=0.2)

    marked = await app.state.repository.mark_stuck_jobs_failed(max_age_seconds=1, now_epoch=time.time() + 3600)
    assert marked == 1
    reloaded = await app.state.repository.get_job(job["id"])
    assert reloaded is not None
    assert reloaded["status"] == "failed"
    assert reloaded["error"]["code"] == "stuck_job_reaped"


@pytest.mark.asyncio
async def test_inflight_entries_include_reaper_timestamps_on_timeout(tmp_path: Path):
    recorder = DownstreamRecorder()
    observed_inflight: dict[str, Any] = {}

    async def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if body.get("method") == "tools/call":
            observed_inflight.update(app.state.inflight_downstream_calls.get(str(body.get("id")), {}))
            raise httpx.ReadTimeout("read timed out", request=request)
        return await recorder(request)

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "inflight-timeout.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "inflight-timeout-secrets.json",
            DOWNSTREAM_TIMEOUT_SECONDS=5,
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            created = await client.post(
                "/v1/mcp-services",
                headers=auth_headers(),
                json={"name": "Inflight MCP", "slug": "inflight", "endpoint_url": "http://fake.local/mcp"},
            )
            service_id = created.json()["id"]
            assert (await client.post(f"/v1/mcp-services/{service_id}/validate", headers=auth_headers())).status_code == 200
            assert (await client.post("/v1/toolboxes/tbx_default/items", headers=auth_headers(), json={"service_id": service_id})).status_code == 201
            init = await initialize(client)
            session_id = init.headers["Mcp-Session-Id"]
            await client.post(
                "/mcp",
                headers={**auth_headers(), "Mcp-Session-Id": session_id},
                json={"jsonrpc": "2.0", "id": "inflight-1", "method": "tools/call", "params": {"name": "inflight.echo", "arguments": {"text": "slow"}}},
            )

    assert observed_inflight["method"] == "tools/call"
    assert observed_inflight["transport_type"] == "http"
    assert observed_inflight["timeout_at"] > observed_inflight["started_at"]
    await downstream_client.aclose()


def test_svg_icons_are_blocked_by_default_but_png_icons_survive(tmp_path: Path):
    tools, warnings = normalize_downstream_tools(
        [
            {
                "name": "icons-rich",
                "inputSchema": {"type": "object"},
                "icons": [
                    {"src": "data:image/svg+xml,<svg><script>alert(1)</script></svg>", "mimeType": "image/svg+xml"},
                    {"src": "data:image/png;base64,AAAA", "mimeType": "image/png", "sizes": ["64x64"]},
                ],
            }
        ],
        service_slug="icons",
        settings=Settings(
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            ICON_SVG_ENABLED=False,
        ),
    )

    assert tools[0]["icons_json"] == [
        {"src": "data:image/png;base64,AAAA", "mimeType": "image/png", "sizes": ["64x64"]}
    ]
    assert any(warning["code"] == "icon_svg_blocked" for warning in warnings)


def test_downstream_tool_names_with_dots_are_still_service_namespaced(tmp_path: Path):
    tools, warnings = normalize_downstream_tools(
        [{"name": "admin.echo", "inputSchema": {"type": "object"}}],
        service_slug="safe",
        settings=Settings(COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token"),
    )

    assert warnings == []
    assert tools[0]["original_name"] == "admin.echo"
    assert tools[0]["exposed_name"] == "safe.admin.echo"


@pytest.mark.asyncio
async def test_static_mode_keeps_oauth_endpoints_hidden(app_client):
    client, _, _ = app_client
    assert (await client.get("/.well-known/oauth-protected-resource")).status_code == 404
    assert (await client.get("/.well-known/oauth-authorization-server")).status_code == 404
    assert (await client.get("/.well-known/jwks.json")).status_code == 404
    assert (await client.post("/oauth/register", json={})).status_code == 404


@pytest.mark.asyncio
async def test_oauth_form_body_rejects_duplicate_fields(oauth_app_client):
    client, _, _ = oauth_app_client
    response = await client.post(
        "/oauth/introspect",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        content=b"token=first&token=second",
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "duplicate form field: token" in response.json()["error_description"]


@pytest.mark.asyncio
async def test_oauth_authorization_code_pkce_jwt_revoke_flow(oauth_app_client):
    client, _, _ = oauth_app_client
    verifier = "a" * 64
    resource = "http://testserver/mcp"

    registered = await client.post(
        "/oauth/register",
        json={
            "client_name": "Local OAuth Client",
            "redirect_uris": ["http://localhost/callback"],
            "scope": "mcp:tools.read mcp:tools.call",
        },
    )
    assert registered.status_code == 201
    client_id = registered.json()["client_id"]

    authorize = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost/callback",
            "resource": resource,
            "scope": "mcp:tools.read mcp:tools.call",
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "state": "state-1",
        },
        follow_redirects=False,
    )
    assert authorize.status_code == 302
    redirect = urlparse(authorize.headers["location"])
    query = parse_qs(redirect.query)
    code = query["code"][0]
    assert query["state"] == ["state-1"]

    token = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": "http://localhost/callback",
            "resource": resource,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200
    token_body = token.json()
    assert token_body["token_type"] == "Bearer"
    assert token_body["access_token"].count(".") == 2
    assert token_body["refresh_token"].startswith("cmcp_refresh_")

    jwks = await client.get("/.well-known/jwks.json")
    assert jwks.status_code == 200
    assert jwks.json()["keys"][0]["alg"] == "RS256"

    ping = await client.post(
        "/mcp",
        headers=auth_headers(token_body["access_token"]),
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert ping.status_code == 200

    introspected = await client.post("/oauth/introspect", data={"token": token_body["access_token"]})
    assert introspected.status_code == 200
    assert introspected.json()["active"] is True
    assert introspected.json()["aud"] == resource

    revoked = await client.post("/oauth/revoke", data={"token": token_body["access_token"]})
    assert revoked.status_code == 200
    ping_after_revoke = await client.post(
        "/mcp",
        headers=auth_headers(token_body["access_token"]),
        json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
    )
    assert ping_after_revoke.status_code == 401
    assert "resource_metadata=" in ping_after_revoke.headers["www-authenticate"]


@pytest.mark.asyncio
async def test_oauth_scope_is_enforced_for_tools_call(oauth_app_client):
    client, recorder, app = oauth_app_client
    verifier = "b" * 64
    resource = "http://testserver/mcp"

    registered = await client.post(
        "/oauth/register",
        json={
            "client_name": "Read Only OAuth Client",
            "redirect_uris": ["http://localhost/callback"],
            "scope": "mcp:tools.read",
        },
    )
    assert registered.status_code == 201
    client_id = registered.json()["client_id"]

    authorize = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost/callback",
            "resource": resource,
            "scope": "mcp:tools.read",
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]
    token = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": "http://localhost/callback",
            "resource": resource,
            "code_verifier": verifier,
        },
    )
    access_token = token.json()["access_token"]

    init = await initialize(client, token=access_token)
    session_id = init.headers["Mcp-Session-Id"]
    listed = await client.post(
        "/mcp",
        headers={**auth_headers(access_token), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 401, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200

    before_calls = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])
    denied = await client.post(
        "/mcp",
        headers={**auth_headers(access_token), "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": 402,
            "method": "tools/call",
            "params": {"name": "fake.echo", "arguments": {"text": "blocked-by-oauth-scope"}},
        },
    )
    after_calls = len([request for request in recorder.requests if request["body"]["method"] == "tools/call"])
    assert denied.status_code == 200
    assert denied.json()["error"]["data"]["required_scope"] == "mcp:tools.call"
    assert after_calls == before_calls

    audit = await app.state.repository.recent_audit_logs(limit=5, action="policy.deny")
    assert audit and audit[0]["metadata"]["auth_kind"] == "oauth"


@pytest.mark.asyncio
async def test_oauth_refresh_token_rotates(oauth_app_client):
    client, _, _ = oauth_app_client
    verifier = "b" * 64
    resource = "http://testserver/mcp"
    registered = await client.post(
        "/oauth/register",
        json={"client_name": "Refresh Client", "redirect_uris": ["http://localhost/refresh"]},
    )
    client_id = registered.json()["client_id"]
    authorize = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost/refresh",
            "resource": resource,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]
    token = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": "http://localhost/refresh",
            "resource": resource,
            "code_verifier": verifier,
        },
    )
    refresh_token = token.json()["refresh_token"]

    refreshed = await client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "resource": resource,
        },
    )
    assert refreshed.status_code == 200
    rotated_refresh_token = refreshed.json()["refresh_token"]
    assert rotated_refresh_token != refresh_token

    reused = await client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "resource": resource,
        },
    )
    assert reused.status_code == 400
    assert reused.json()["error"] == "invalid_grant"

    family_revoked = await client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": rotated_refresh_token,
            "client_id": client_id,
            "resource": resource,
        },
    )
    assert family_revoked.status_code == 400
    assert family_revoked.json()["error"] == "invalid_grant"

    audit = await client.get("/v1/audit-logs", headers=auth_headers())
    assert any(item["action"] == "oauth.refresh_token.reuse_detected" for item in audit.json()["items"])


@pytest.mark.asyncio
async def test_oauth_rejects_invalid_pkce(oauth_app_client):
    client, _, _ = oauth_app_client
    resource = "http://testserver/mcp"
    registered = await client.post(
        "/oauth/register",
        json={"client_name": "PKCE Client", "redirect_uris": ["http://localhost/pkce"]},
    )
    client_id = registered.json()["client_id"]
    authorize = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost/pkce",
            "resource": resource,
            "code_challenge": pkce_challenge("c" * 64),
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]
    token = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": "http://localhost/pkce",
            "resource": resource,
            "code_verifier": "wrong" * 12,
        },
    )
    assert token.status_code == 400
    assert token.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_oauth_rejects_resource_mismatch_and_invalid_dcr(oauth_app_client):
    client, _, _ = oauth_app_client
    verifier = "e" * 64
    registered = await client.post(
        "/oauth/register",
        json={"client_name": "Resource Client", "redirect_uris": ["http://localhost/resource"]},
    )
    client_id = registered.json()["client_id"]

    authorize = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost/resource",
            "resource": "http://testserver/not-mcp",
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        },
    )
    assert authorize.status_code == 400
    assert authorize.json()["error"] == "invalid_target"

    bad_scope = await client.post(
        "/oauth/register",
        json={"client_name": "Bad Scope", "redirect_uris": ["http://localhost/bad"], "scope": "mcp:admin"},
    )
    assert bad_scope.status_code == 400
    assert bad_scope.json()["error"] == "invalid_scope"

    bad_redirect = await client.post(
        "/oauth/register",
        json={"client_name": "Bad Redirect", "redirect_uris": ["http://example.com/callback"]},
    )
    assert bad_redirect.status_code == 400
    assert bad_redirect.json()["error"] == "invalid_redirect_uri"


@pytest.mark.asyncio
async def test_oauth_cimd_rejects_unsafe_client_id(oauth_app_client):
    client, _, _ = oauth_app_client
    authorize = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "https://127.0.0.1/.well-known/oauth-client",
            "redirect_uri": "https://127.0.0.1/callback",
            "resource": "http://testserver/mcp",
            "code_challenge": pkce_challenge("f" * 64),
            "code_challenge_method": "S256",
        },
    )
    assert authorize.status_code == 400
    assert authorize.json()["error"] == "unsafe_client_id"


@pytest.mark.asyncio
async def test_oauth_cimd_rejects_redirect_content_type_size_and_mismatch(tmp_path: Path):
    async def transport(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/redirect"):
            return httpx.Response(302, headers={"location": "https://client.example/other"})
        if url.endswith("/content-type"):
            return httpx.Response(200, headers={"content-type": "text/plain"}, json={"client_id": url})
        if url.endswith("/large"):
            return httpx.Response(200, headers={"content-type": "application/json"}, content=b"x" * (32 * 1024 + 1))
        if url.endswith("/mismatch"):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"client_id": "https://client.example/other", "redirect_uris": ["https://client.example/callback"]},
            )
        if url.endswith("/host-mismatch"):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"client_id": url, "redirect_uris": ["https://other.example/callback"]},
            )
        return httpx.Response(404)

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    app = create_app(
        settings=Settings(
            AUTH_MODE="oauth",
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "cimd-reject.sqlite3",
            COREMCP_SSRF_ALLOW_HOSTS="client.example",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "cimd-reject-secrets.json",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            for path, expected_error in {
                "redirect": "invalid_client_metadata",
                "content-type": "invalid_client_metadata",
                "large": "invalid_client_metadata",
                "mismatch": "client_id_mismatch",
                "host-mismatch": "invalid_client_metadata",
            }.items():
                authorize = await client.get(
                    "/oauth/authorize",
                    params={
                        "response_type": "code",
                        "client_id": f"https://client.example/{path}",
                        "redirect_uri": "https://client.example/callback",
                        "resource": "http://testserver/mcp",
                        "code_challenge": pkce_challenge("g" * 64),
                        "code_challenge_method": "S256",
                    },
                )
                assert authorize.status_code == 400
                assert authorize.json()["error"] == expected_error
    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_oauth_cimd_client_id_metadata_document_flow(tmp_path: Path):
    async def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and str(request.url) == "https://client.example/.well-known/oauth-client":
            return httpx.Response(
                200,
                headers={"content-type": "Application/JSON; Charset=UTF-8"},
                json={
                    "client_id": "https://client.example/.well-known/oauth-client",
                    "client_name": "CIMD Client",
                    "redirect_uris": ["https://client.example/callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                    "scope": "mcp:tools.read mcp:tools.call",
                },
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    app = create_app(
        settings=Settings(
            AUTH_MODE="oauth",
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "cimd.sqlite3",
            COREMCP_SSRF_ALLOW_HOSTS="client.example",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "cimd-secrets.json",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            verifier = "d" * 64
            client_id = "https://client.example/.well-known/oauth-client"
            authorize = await client.get(
                "/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": "https://client.example/callback",
                    "resource": "http://testserver/mcp",
                    "code_challenge": pkce_challenge(verifier),
                    "code_challenge_method": "S256",
                },
                follow_redirects=False,
            )
            assert authorize.status_code == 302
            code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]
            token = await client.post(
                "/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                    "redirect_uri": "https://client.example/callback",
                    "resource": "http://testserver/mcp",
                    "code_verifier": verifier,
                },
            )
            assert token.status_code == 200
            assert token.json()["access_token"].count(".") == 2
    await downstream_client.aclose()


class FakeStdioClient:
    instances: list["FakeStdioClient"] = []

    def __init__(
        self,
        command,
        *,
        cwd=None,
        env=None,
        timeout=30.0,
        idle_timeout_seconds=None,
        max_response_bytes=1024 * 1024,
        allowed_basenames=None,
    ) -> None:
        self.command = list(command)
        self.cwd = cwd
        self.env = dict(env or {})
        self.timeout = timeout
        self.idle_timeout_seconds = idle_timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.closed = False
        self.started_at = float(len(FakeStdioClient.instances) + 1)
        self.last_used_at = self.started_at
        self.pending_requests = 0
        FakeStdioClient.instances.append(self)

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "last_used_at": self.last_used_at,
            "pending_requests": self.pending_requests,
            "is_running": not self.closed,
        }

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_stdio_client_capacity_evicts_lru_idle_and_uses_default_timeout(tmp_path: Path, monkeypatch):
    FakeStdioClient.instances = []
    monkeypatch.setattr(main_module, "StdioMcpClient", FakeStdioClient)
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=Settings(
                COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
                COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
                COREMCP_DB_PATH=tmp_path / "stdio-limit.sqlite3",
                COREMCP_STDIO_MAX_CONCURRENT_PROCESSES=2,
                COREMCP_STDIO_DEFAULT_IDLE_TIMEOUT_SECONDS=123,
            ),
            stdio_clients={},
            stdio_clients_lock=asyncio.Lock(),
        )
    )

    first = await main_module._stdio_client_for_config(
        app,
        {"service_id": "svc-1", "stdio_command": sys.executable},
    )
    first.last_used_at = 10.0
    second = await main_module._stdio_client_for_config(
        app,
        {"service_id": "svc-2", "stdio_command": sys.executable, "stdio_idle_timeout_seconds": 456},
    )
    second.last_used_at = 20.0
    third = await main_module._stdio_client_for_config(
        app,
        {"service_id": "svc-3", "stdio_command": sys.executable},
    )

    assert first.closed is True
    assert second.closed is False
    assert third.closed is False
    assert set(app.state.stdio_clients) == {"svc-2", "svc-3"}
    assert first.idle_timeout_seconds == 123
    assert second.idle_timeout_seconds == 456


@pytest.mark.asyncio
async def test_stdio_client_capacity_rejects_when_no_idle_client(tmp_path: Path, monkeypatch):
    FakeStdioClient.instances = []
    monkeypatch.setattr(main_module, "StdioMcpClient", FakeStdioClient)
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=Settings(
                COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
                COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
                COREMCP_DB_PATH=tmp_path / "stdio-busy.sqlite3",
                COREMCP_STDIO_MAX_CONCURRENT_PROCESSES=1,
            ),
            stdio_clients={},
            stdio_clients_lock=asyncio.Lock(),
        )
    )

    first = await main_module._stdio_client_for_config(app, {"service_id": "svc-1", "stdio_command": sys.executable})
    first.pending_requests = 1

    with pytest.raises(DownstreamMcpError) as exc_info:
        await main_module._stdio_client_for_config(app, {"service_id": "svc-2", "stdio_command": sys.executable})

    assert exc_info.value.code == -32010
    assert "capacity exceeded" in str(exc_info.value)
    assert app.state.stdio_clients["svc-1"][1] is first


@pytest.mark.asyncio
async def test_admin_rate_limit_rejects_without_token_leak(tmp_path: Path):
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "admin-rate.sqlite3",
            COREMCP_AUTH_RATE_LIMIT_PER_MINUTE=1,
            COREMCP_MCP_RATE_LIMIT_PER_MINUTE=0,
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "admin-rate-secrets.json",
        )
    )
    leaked_token = "cmcp_admin_this_token_must_not_leak"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            first = await client.get("/v1/me", headers=auth_headers(leaked_token))
            limited = await client.get("/v1/me", headers=auth_headers(leaked_token))

    assert first.status_code == 401
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert leaked_token not in limited.text
    assert "Retry-After" in limited.headers


@pytest.mark.asyncio
async def test_mcp_rate_limit_rejects_jsonrpc_style_without_token_leak(tmp_path: Path):
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "mcp-rate.sqlite3",
            COREMCP_AUTH_RATE_LIMIT_PER_MINUTE=0,
            COREMCP_MCP_RATE_LIMIT_PER_MINUTE=1,
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "mcp-rate-secrets.json",
        )
    )
    leaked_token = "wrong-token-that-must-not-leak"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            first = await client.post(
                "/mcp",
                headers=auth_headers(leaked_token),
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            limited = await client.post(
                "/mcp",
                headers=auth_headers(leaked_token),
                json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
            )
        audit = await app.state.repository.recent_audit_logs(limit=5, action="auth.failure")

    assert first.status_code == 401
    assert limited.status_code == 429
    body = limited.json()
    assert body["jsonrpc"] == "2.0"
    assert body["error"]["code"] == -32029
    assert leaked_token not in limited.text
    assert all(leaked_token not in json.dumps(item, default=str) for item in audit)
