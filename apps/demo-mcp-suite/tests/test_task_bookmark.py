from fastapi.testclient import TestClient

from demo_mcp_suite.main import app


def rpc(method: str, params: dict | None = None, request_id: int | str = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def reset_state(client: TestClient) -> None:
    response = client.post("/_test/reset-state")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def list_tools(client: TestClient, server_slug: str) -> dict[str, dict]:
    response = client.post(f"/{server_slug}/mcp", json=rpc("tools/list"))
    assert response.status_code == 200
    return {item["name"]: item for item in response.json()["result"]["tools"]}


def call_tool(client: TestClient, server_slug: str, name: str, arguments: dict) -> dict:
    response = client.post(
        f"/{server_slug}/mcp",
        json=rpc("tools/call", {"name": name, "arguments": arguments}),
    )
    assert response.status_code == 200
    payload = response.json()
    assert "error" not in payload
    result = payload["result"]
    assert result["isError"] is False
    return result


def assert_annotation(tool: dict, *, read_only: bool, destructive: bool) -> None:
    annotations = tool["annotations"]
    assert annotations["readOnlyHint"] is read_only
    assert annotations["destructiveHint"] is destructive
    assert "idempotentHint" in annotations
    assert "openWorldHint" in annotations


def test_task_board_tools_list_includes_annotations() -> None:
    client = TestClient(app)
    reset_state(client)

    tools = list_tools(client, "task-board")

    assert set(tools) == {"task_list", "task_get", "task_create", "task_update_status", "task_archive"}
    assert_annotation(tools["task_list"], read_only=True, destructive=False)
    assert_annotation(tools["task_get"], read_only=True, destructive=False)
    assert_annotation(tools["task_create"], read_only=False, destructive=False)
    assert_annotation(tools["task_update_status"], read_only=False, destructive=False)
    assert_annotation(tools["task_archive"], read_only=False, destructive=True)


def test_task_board_read_write_destructive_calls_and_reset_are_stable() -> None:
    client = TestClient(app)
    reset_state(client)

    listed = call_tool(client, "task-board", "task_list", {"status": "todo"})
    assert listed["structuredContent"]["count"] >= 1

    created = call_tool(
        client,
        "task-board",
        "task_create",
        {
            "title": "Write focused demo task",
            "owner": "worker-b",
            "priority": "high",
            "tags": ["demo", "task-board"],
            "due_date": "2026-05-20",
        },
    )["structuredContent"]["task"]
    assert created["id"] == "task-004"
    assert created["status"] == "todo"

    updated = call_tool(
        client,
        "task-board",
        "task_update_status",
        {"task_id": created["id"], "status": "done", "note": "Verified in Worker B test"},
    )["structuredContent"]
    assert updated["changed"] is True
    assert updated["task"]["status"] == "done"

    archived = call_tool(
        client,
        "task-board",
        "task_archive",
        {"task_id": created["id"], "reason": "demo cleanup"},
    )["structuredContent"]
    assert archived["archived"] is True
    assert archived["task"]["status"] == "archived"

    hidden_after_archive = call_tool(client, "task-board", "task_list", {"tag": "task-board"})
    assert created["id"] not in {task["id"] for task in hidden_after_archive["structuredContent"]["tasks"]}

    archived_list = call_tool(client, "task-board", "task_list", {"status": "archived"})
    assert created["id"] in {task["id"] for task in archived_list["structuredContent"]["tasks"]}

    reset_state(client)
    recreated = call_tool(client, "task-board", "task_create", {"title": "Write focused demo task"})[
        "structuredContent"
    ]["task"]
    assert recreated["id"] == "task-004"


def test_bookmark_research_tools_list_includes_annotations() -> None:
    client = TestClient(app)
    reset_state(client)

    tools = list_tools(client, "bookmark-research")

    assert set(tools) == {
        "bookmark_search",
        "bookmark_list_by_tag",
        "bookmark_summarize_stub",
        "bookmark_create",
        "bookmark_delete",
    }
    assert_annotation(tools["bookmark_search"], read_only=True, destructive=False)
    assert_annotation(tools["bookmark_list_by_tag"], read_only=True, destructive=False)
    assert_annotation(tools["bookmark_summarize_stub"], read_only=True, destructive=False)
    assert_annotation(tools["bookmark_create"], read_only=False, destructive=False)
    assert_annotation(tools["bookmark_delete"], read_only=False, destructive=True)


def test_bookmark_research_read_write_destructive_calls_and_reset_are_stable() -> None:
    client = TestClient(app)
    reset_state(client)

    search = call_tool(client, "bookmark-research", "bookmark_search", {"query": "mcp"})
    assert search["structuredContent"]["count"] >= 2

    by_tag = call_tool(client, "bookmark-research", "bookmark_list_by_tag", {"tag": "safety"})
    assert by_tag["structuredContent"]["count"] == 1

    summary = call_tool(
        client,
        "bookmark-research",
        "bookmark_summarize_stub",
        {"bookmark_id": "bookmark-001", "style": "bullets"},
    )["structuredContent"]
    assert summary["stub_notice"].startswith("Static fixture summary")

    created = call_tool(
        client,
        "bookmark-research",
        "bookmark_create",
        {
            "title": "Worker B deterministic bookmark",
            "url": "https://fixtures.example.test/worker-b/deterministic-bookmark",
            "tags": ["worker-b", "demo"],
            "summary": "A static bookmark created by the demo test.",
        },
    )["structuredContent"]["bookmark"]
    assert created["id"] == "bookmark-004"

    deleted = call_tool(
        client,
        "bookmark-research",
        "bookmark_delete",
        {"bookmark_id": created["id"], "reason": "demo cleanup"},
    )["structuredContent"]
    assert deleted["deleted"] is True
    assert deleted["already_deleted"] is False

    after_delete = call_tool(client, "bookmark-research", "bookmark_search", {"query": "deterministic"})
    assert created["id"] not in {bookmark["id"] for bookmark in after_delete["structuredContent"]["bookmarks"]}

    reset_state(client)
    recreated = call_tool(
        client,
        "bookmark-research",
        "bookmark_create",
        {"title": "Worker B deterministic bookmark", "url": "https://fixtures.example.test/worker-b/reset"},
    )["structuredContent"]["bookmark"]
    assert recreated["id"] == "bookmark-004"
