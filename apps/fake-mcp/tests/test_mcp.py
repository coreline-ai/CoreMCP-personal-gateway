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
    assert {"echo", "add", "sleep", "error"}.issubset(names)
    assert len(tools) >= 3

    echo = next(tool for tool in tools if tool["name"] == "echo")
    assert echo["icons"] == [
        {
            "src": "https://example.test/icons/echo.svg",
            "mimeType": "image/svg+xml",
            "sizes": ["48x48"],
        }
    ]


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
