from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from demo_mcp_suite.main import app


def rpc(method: str, params: dict[str, Any] | None = None, request_id: int | str = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def call_tool(client: TestClient, server: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(
        f"/{server}/mcp",
        json=rpc("tools/call", {"name": name, "arguments": arguments or {}}),
    )
    assert response.status_code == 200
    body = response.json()
    assert "error" not in body
    return assert_tool_result(body["result"])


def list_tools(client: TestClient, server: str) -> dict[str, dict[str, Any]]:
    response = client.post(f"/{server}/mcp", json=rpc("tools/list"))
    assert response.status_code == 200
    body = response.json()
    assert "error" not in body
    return {tool["name"]: tool for tool in body["result"]["tools"]}


def assert_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    assert set(result) == {"content", "structuredContent", "isError"}
    assert result["isError"] is False
    assert isinstance(result["content"], list)
    assert result["content"]
    assert result["content"][0]["type"] == "text"
    assert isinstance(result["structuredContent"], dict)
    return result


def assert_annotations(
    tools: dict[str, dict[str, Any]],
    *,
    read_only: set[str],
    write: set[str],
    destructive: set[str],
) -> None:
    assert set(tools) == read_only | write | destructive

    for name in read_only:
        annotations = tools[name]["annotations"]
        assert annotations["readOnlyHint"] is True
        assert annotations["destructiveHint"] is False

    for name in write:
        annotations = tools[name]["annotations"]
        assert annotations["readOnlyHint"] is False
        assert annotations["destructiveHint"] is False

    for name in destructive:
        annotations = tools[name]["annotations"]
        assert annotations["readOnlyHint"] is False
        assert annotations["destructiveHint"] is True


def reset_state(client: TestClient) -> None:
    response = client.post("/_test/reset-state")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_personal_ops_tools_list_includes_expected_annotations() -> None:
    client = TestClient(app)

    tools = list_tools(client, "personal-ops")

    assert_annotations(
        tools,
        read_only={"ops_status", "ops_checklist", "incident_list"},
        write={"note_create", "backup_run"},
        destructive={"service_restart"},
    )


def test_personal_ops_read_write_destructive_calls_are_stateful_after_reset() -> None:
    client = TestClient(app)
    reset_state(client)

    status = call_tool(client, "personal-ops", "ops_status")
    assert status["structuredContent"]["overall_status"] == "warning"
    assert status["structuredContent"]["active_incident_count"] == 1

    note = call_tool(
        client,
        "personal-ops",
        "note_create",
        {
            "title": "Demo readiness",
            "body": "Worker A verified the personal ops tool contract.",
            "area": "CoreMCP",
            "tags": ["demo", "ops"],
        },
    )
    assert note["structuredContent"]["note"]["id"] == "ops_note_001"
    assert note["structuredContent"]["note"]["tags"] == ["demo", "ops"]

    backup = call_tool(client, "personal-ops", "backup_run", {"target": "photos", "mode": "incremental"})
    assert backup["structuredContent"]["backup"]["id"] == "backup_001"
    assert backup["structuredContent"]["backup"]["target"] == "photos"

    restart = call_tool(
        client,
        "personal-ops",
        "service_restart",
        {"service": "backup-agent", "reason": "demo recovery verification", "confirm": True},
    )
    assert restart["structuredContent"]["restart"]["id"] == "restart_001"
    assert restart["structuredContent"]["service"]["status"] == "healthy"
    assert restart["structuredContent"]["service"]["uptime"] == "0m"


def test_knowledge_vault_tools_list_includes_expected_annotations() -> None:
    client = TestClient(app)

    tools = list_tools(client, "knowledge-vault")

    assert_annotations(
        tools,
        read_only={"note_search", "note_get"},
        write={"note_create", "note_tag"},
        destructive={"note_delete"},
    )


def test_knowledge_vault_search_create_tag_delete_is_stable_after_reset() -> None:
    client = TestClient(app)
    reset_state(client)

    search = call_tool(client, "knowledge-vault", "note_search", {"query": "CoreMCP"})
    assert search["structuredContent"]["count"] >= 1
    assert search["structuredContent"]["results"][0]["id"] == "kv_note_001"

    created = call_tool(
        client,
        "knowledge-vault",
        "note_create",
        {
            "title": "Worker A demo note",
            "body": "This note verifies create, tag, get, and delete flows.",
            "tags": ["demo"],
            "source": "pytest",
        },
    )
    note_id = created["structuredContent"]["note"]["id"]
    assert note_id == "kv_note_004"

    tagged = call_tool(
        client,
        "knowledge-vault",
        "note_tag",
        {"note_id": note_id, "tags": ["worker-a", "coremcp"], "mode": "add"},
    )
    assert tagged["structuredContent"]["after"] == ["demo", "worker-a", "coremcp"]

    fetched = call_tool(client, "knowledge-vault", "note_get", {"note_id": note_id})
    assert fetched["structuredContent"]["status"] == "found"
    assert fetched["structuredContent"]["note"]["tags"] == ["demo", "worker-a", "coremcp"]

    deleted = call_tool(client, "knowledge-vault", "note_delete", {"note_id": note_id, "confirm": True})
    assert deleted["structuredContent"]["status"] == "deleted"
    assert deleted["structuredContent"]["deleted"]["note_id"] == note_id

    missing = call_tool(client, "knowledge-vault", "note_get", {"note_id": note_id})
    assert missing["structuredContent"]["status"] == "not_found"

    after_delete_search = call_tool(client, "knowledge-vault", "note_search", {"tags": ["worker-a"]})
    assert after_delete_search["structuredContent"]["count"] == 0
