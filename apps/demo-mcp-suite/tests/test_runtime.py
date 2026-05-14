from fastapi.testclient import TestClient

from demo_mcp_suite.main import app
from demo_mcp_suite.registry import SERVERS


def rpc(method: str, params: dict | None = None, request_id: int | str = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def test_health_and_demo_services_list_all_eight_servers() -> None:
    client = TestClient(app, base_url="http://127.0.0.1:8791")

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["server_count"] == 8

    services = client.get("/demo-services")
    assert services.status_code == 200
    items = services.json()["items"]
    assert len(items) == 8
    assert {item["auth_type"] for item in items} == {"none"}
    assert all(item["endpoint_url"].startswith("http://127.0.0.1:8791/") for item in items)


def test_initialize_ping_and_notifications() -> None:
    client = TestClient(app)

    initialized = client.post("/personal-ops/mcp", json=rpc("initialize", {"protocolVersion": "2025-11-25"}))
    assert initialized.status_code == 200
    assert initialized.headers["Mcp-Session-Id"].startswith("demo_personal-ops_")
    assert initialized.json()["result"]["protocolVersion"] == "2025-11-25"

    ping = client.post("/personal-ops/mcp", json=rpc("ping"))
    assert ping.status_code == 200
    assert ping.json()["result"] == {}

    notification = client.post("/personal-ops/mcp", json=rpc("notifications/initialized"))
    assert notification.status_code == 202


def test_every_demo_server_has_policy_preset_coverage() -> None:
    for server in SERVERS:
        annotations = [tool["annotations"] for tool in server.tools]
        assert annotations, server.slug
        assert any(annotation.get("readOnlyHint") is True for annotation in annotations), server.slug
        assert any(
            annotation.get("readOnlyHint") is False and annotation.get("destructiveHint") is False
            for annotation in annotations
        ), server.slug
        assert any(annotation.get("destructiveHint") is True for annotation in annotations), server.slug
        assert {tool["name"] for tool in server.tools} == set(server.handlers), server.slug


def test_unknown_server_and_unknown_tool_return_json_rpc_errors() -> None:
    client = TestClient(app)

    missing_server = client.post("/missing/mcp", json=rpc("tools/list"))
    assert missing_server.status_code == 404
    assert missing_server.json()["error"]["message"] == "Demo MCP server not found"

    unknown_tool = client.post(
        "/personal-ops/mcp",
        json=rpc("tools/call", {"name": "missing_tool", "arguments": {}}),
    )
    assert unknown_tool.status_code == 200
    assert unknown_tool.json()["error"]["code"] == -32602
