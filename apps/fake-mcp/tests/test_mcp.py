from fastapi.testclient import TestClient

from fake_mcp.main import app


def rpc(method: str, params: dict | None = None, request_id: int | str = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def test_initialize_echoes_supported_protocol_and_returns_tools_capability() -> None:
    client = TestClient(app)

    response = client.post(
        "/mcp",
        json=rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}}),
    )

    assert response.status_code == 200
    assert response.headers["Mcp-Session-Id"].startswith("fake_mcp_")
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert body["result"]["protocolVersion"] == "2025-11-25"
    assert body["result"]["capabilities"] == {"tools": {"listChanged": True}}


def test_tools_list_returns_test_tools_with_2025_11_25_icon_shape() -> None:
    client = TestClient(app)

    response = client.post("/mcp", json=rpc("tools/list"))

    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"echo", "add", "sleep", "error", "cancellation", "schema-change", "icons-rich", "cimd-test", "dcr-test"}.issubset(names)
    assert len(tools) >= 9

    echo = next(tool for tool in tools if tool["name"] == "echo")
    assert echo["icons"] == [
        {
            "src": "https://example.test/icons/echo.svg",
            "mimeType": "image/svg+xml",
            "sizes": ["48x48"],
        }
    ]

    icons_rich = next(tool for tool in tools if tool["name"] == "icons-rich")
    assert icons_rich["icons"][0]["src"].startswith("data:image/svg+xml")


def test_production_fixture_tools_are_explicitly_covered() -> None:
    client = TestClient(app)
    client.post("/_test/reset-state")

    response = client.post("/mcp", json=rpc("tools/list"))

    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    assert set(tools) >= {
        "cancellation",
        "schema-change",
        "icons-rich",
        "cimd-test",
        "dcr-test",
    }

    cancellation = tools["cancellation"]
    assert cancellation["inputSchema"]["properties"]["seconds"] == {
        "type": "number",
        "minimum": 0,
        "maximum": 60,
        "default": 60,
    }
    assert cancellation["annotations"]["idempotentHint"] is False

    schema_change = tools["schema-change"]
    assert schema_change["title"] == "Schema Change v1"
    assert schema_change["inputSchema"]["properties"] == {
        "value_v1": {"type": "string"}
    }

    icons_rich = tools["icons-rich"]
    assert icons_rich["icons"] == [
        {
            "src": "data:image/svg+xml,%3Csvg%20onload%3Dalert(1)%3E%3C/svg%3E",
            "mimeType": "image/svg+xml",
        },
        {
            "src": "https://example.test/icons/rich.png",
            "mimeType": "image/png",
            "sizes": ["64x64"],
        },
    ]
    assert "url" not in icons_rich["icons"][0]

    assert tools["cimd-test"]["annotations"] == {"readOnlyHint": True}
    assert tools["dcr-test"]["annotations"] == {"readOnlyHint": True}


def test_schema_change_fixture_changes_schema_between_list_calls() -> None:
    client = TestClient(app)
    client.post("/_test/reset-state")

    first = client.post("/mcp", json=rpc("tools/list")).json()["result"]["tools"]
    second = client.post("/mcp", json=rpc("tools/list")).json()["result"]["tools"]

    first_schema = next(tool for tool in first if tool["name"] == "schema-change")["inputSchema"]
    second_schema = next(tool for tool in second if tool["name"] == "schema-change")["inputSchema"]
    assert first_schema != second_schema


def test_schema_change_fixture_changes_title_and_property_name_between_list_calls() -> None:
    client = TestClient(app)
    client.post("/_test/reset-state")

    first = client.post("/mcp", json=rpc("tools/list")).json()["result"]["tools"]
    second = client.post("/mcp", json=rpc("tools/list")).json()["result"]["tools"]

    first_tool = next(tool for tool in first if tool["name"] == "schema-change")
    second_tool = next(tool for tool in second if tool["name"] == "schema-change")
    assert first_tool["title"] == "Schema Change v1"
    assert second_tool["title"] == "Schema Change v2"
    assert list(first_tool["inputSchema"]["properties"]) == ["value_v1"]
    assert list(second_tool["inputSchema"]["properties"]) == ["value_v2"]


def test_cimd_and_dcr_fixture_endpoints() -> None:
    client = TestClient(app, base_url="https://client.example")

    cimd = client.get("/.well-known/oauth-client")
    assert cimd.status_code == 200
    assert cimd.json()["client_id"] == "https://client.example/.well-known/oauth-client"

    dcr = client.post("/oauth/register", json={"client_name": "DCR Test", "redirect_uris": ["http://localhost/callback"]})
    assert dcr.status_code == 201
    assert dcr.json()["client_id"].startswith("fake_dcr_")


def test_tools_call_echo() -> None:
    client = TestClient(app)

    response = client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "echo", "arguments": {"message": "hello"}}),
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["content"] == [{"type": "text", "text": "hello"}]
    assert result["structuredContent"] == {"message": "hello"}


def test_tools_call_add() -> None:
    client = TestClient(app)

    response = client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "add", "arguments": {"a": 2, "b": 3.5}}),
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["content"] == [{"type": "text", "text": "5.5"}]
    assert result["structuredContent"] == {"sum": 5.5}


def test_cancellation_fixture_can_complete_quickly_for_gateway_timeout_tests() -> None:
    client = TestClient(app)

    response = client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "cancellation", "arguments": {"seconds": 0}}),
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["content"] == [
        {"type": "text", "text": "Cancellation fixture completed after 0 seconds"}
    ]
    assert result["structuredContent"] == {"seconds": 0.0}


def test_marker_fixture_tools_are_callable() -> None:
    client = TestClient(app)

    for name in ("schema-change", "icons-rich", "cimd-test", "dcr-test"):
        response = client.post(
            "/mcp",
            json=rpc("tools/call", {"name": name, "arguments": {}}, request_id=name),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == name
        assert body["result"] == {
            "content": [{"type": "text", "text": f"{name} ok"}],
            "structuredContent": {"tool": name},
            "isError": False,
        }


def test_unsupported_method_returns_method_not_found() -> None:
    client = TestClient(app)

    response = client.post("/mcp", json=rpc("resources/list"))

    assert response.status_code == 200
    body = response.json()
    assert body["error"]["code"] == -32601
    assert body["error"]["message"] == "Method not found"
    assert body["error"]["data"] == {"method": "resources/list"}


def test_authorization_header_state_is_recorded_for_token_boundary_tests() -> None:
    client = TestClient(app)
    client.post("/_test/reset-state")

    response = client.post(
        "/mcp",
        json=rpc("ping"),
        headers={"Authorization": "Bearer downstream-token"},
    )
    state = client.get("/_test/authorization")

    assert response.status_code == 200
    assert response.json()["result"] == {}
    assert state.status_code == 200
    assert state.json() == {
        "lastAuthorization": "Bearer downstream-token",
        "authorizationHeaders": ["Bearer downstream-token"],
        "requestCount": 1,
    }
