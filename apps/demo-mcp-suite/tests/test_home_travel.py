from fastapi.testclient import TestClient

from demo_mcp_suite.main import app


def rpc(method: str, params: dict | None = None, request_id: int | str = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def call_tool(client: TestClient, server_slug: str, name: str, arguments: dict | None = None) -> dict:
    response = client.post(
        f"/{server_slug}/mcp",
        json=rpc("tools/call", {"name": name, "arguments": arguments or {}}),
    )
    assert response.status_code == 200
    body = response.json()
    assert "error" not in body
    result = body["result"]
    assert result["isError"] is False
    return result["structuredContent"]


def listed_tools(client: TestClient, server_slug: str) -> dict[str, dict]:
    response = client.post(f"/{server_slug}/mcp", json=rpc("tools/list"))
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    return {item["name"]: item for item in tools}


def reset_demo_state(client: TestClient) -> None:
    response = client.post("/_test/reset-state")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def assert_tool_annotations(tools: dict[str, dict], expected: dict[str, tuple[bool, bool]]) -> None:
    assert set(tools) == set(expected)
    for name, (read_only, destructive) in expected.items():
        annotations = tools[name]["annotations"]
        assert annotations["readOnlyHint"] is read_only
        assert annotations["destructiveHint"] is destructive
        assert annotations["openWorldHint"] is False
        if read_only:
            assert annotations["idempotentHint"] is True
        if destructive:
            assert annotations["readOnlyHint"] is False


def test_home_lab_tools_list_includes_annotations() -> None:
    client = TestClient(app)

    tools = listed_tools(client, "home-lab")

    assert_tool_annotations(
        tools,
        {
            "device_list": (True, False),
            "device_status": (True, False),
            "service_logs": (True, False),
            "maintenance_note_create": (False, False),
            "service_restart": (False, True),
        },
    )


def test_travel_planner_tools_list_includes_annotations() -> None:
    client = TestClient(app)

    tools = listed_tools(client, "travel-planner")

    assert_tool_annotations(
        tools,
        {
            "itinerary_list": (True, False),
            "place_search": (True, False),
            "itinerary_add_place": (False, False),
            "itinerary_remove_place": (False, True),
        },
    )


def test_home_lab_read_write_destructive_calls_and_reset_state() -> None:
    client = TestClient(app)
    reset_demo_state(client)

    inventory = call_tool(client, "home-lab", "device_list", {"include_services": False})
    assert inventory["summary"]["total"] == 3
    assert {device["id"] for device in inventory["devices"]} == {"nas-01", "router-01", "pi-01"}

    note = call_tool(
        client,
        "home-lab",
        "maintenance_note_create",
        {
            "device_id": "nas-01",
            "message": "Replace dust filter during the next maintenance window.",
            "severity": "info",
        },
    )
    assert note["note"]["id"] == "note-002"
    assert note["note"]["device_id"] == "nas-01"

    nas_status = call_tool(client, "home-lab", "device_status", {"device_id": "nas-01"})
    assert any("dust filter" in item["message"] for item in nas_status["maintenance_notes"])

    restart = call_tool(
        client,
        "home-lab",
        "service_restart",
        {"service_id": "home-assistant", "reason": "Apply demo configuration reload."},
    )
    assert restart["service"]["restart_count"] == 1
    assert restart["safety"] == {
        "simulated_only": True,
        "external_commands_executed": False,
        "credential_accessed": False,
    }

    logs = call_tool(client, "home-lab", "service_logs", {"service_id": "home-assistant", "limit": 5})
    assert any("Apply demo configuration reload" in entry["message"] for entry in logs["logs"])

    reset_demo_state(client)
    reset_status = call_tool(client, "home-lab", "device_status", {"device_id": "nas-01"})
    assert all("dust filter" not in item["message"] for item in reset_status["maintenance_notes"])
    reset_logs = call_tool(client, "home-lab", "service_logs", {"service_id": "home-assistant", "limit": 5})
    assert all("Apply demo configuration reload" not in entry["message"] for entry in reset_logs["logs"])
    reset_service = reset_logs["service"]
    assert reset_service["restart_count"] == 0


def test_travel_planner_read_write_destructive_calls_and_reset_state() -> None:
    client = TestClient(app)
    reset_demo_state(client)

    search = call_tool(client, "travel-planner", "place_search", {"query": "garden", "city": "Tokyo"})
    assert [place["id"] for place in search["places"]] == ["kiyosumi-garden"]

    added = call_tool(
        client,
        "travel-planner",
        "itinerary_add_place",
        {
            "itinerary_id": "tokyo-spring-2026",
            "place_id": "kappabashi-street",
            "day": 2,
            "note": "Add if the afternoon forecast turns rainy.",
        },
    )
    assert added["added_stop"]["place_id"] == "kappabashi-street"
    assert added["added_stop"]["day"] == 2

    expanded = call_tool(
        client,
        "travel-planner",
        "itinerary_list",
        {"destination": "Tokyo", "include_places": True},
    )
    tokyo_places = {
        stop["place_id"]
        for itinerary in expanded["itineraries"]
        if itinerary["id"] == "tokyo-spring-2026"
        for stop in itinerary["places"]
    }
    assert "kappabashi-street" in tokyo_places
    assert "sensoji-temple" in tokyo_places

    removed = call_tool(
        client,
        "travel-planner",
        "itinerary_remove_place",
        {"itinerary_id": "tokyo-spring-2026", "place_id": "sensoji-temple"},
    )
    assert removed["destructive_change"] is True
    assert removed["removed_stop"]["place_id"] == "sensoji-temple"

    reset_demo_state(client)
    reset_itineraries = call_tool(
        client,
        "travel-planner",
        "itinerary_list",
        {"destination": "Tokyo", "include_places": True},
    )
    reset_tokyo_places = {
        stop["place_id"]
        for itinerary in reset_itineraries["itineraries"]
        if itinerary["id"] == "tokyo-spring-2026"
        for stop in itinerary["places"]
    }
    assert "kappabashi-street" not in reset_tokyo_places
    assert "sensoji-temple" in reset_tokyo_places
