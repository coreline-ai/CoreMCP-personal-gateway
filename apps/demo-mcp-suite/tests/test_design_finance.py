from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from demo_mcp_suite.main import app


def rpc(method: str, params: dict[str, Any] | None = None, request_id: int | str = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        reset = test_client.post("/_test/reset-state")
        assert reset.status_code == 200
        yield test_client
        test_client.post("/_test/reset-state")


def list_tools(client: TestClient, server_slug: str) -> dict[str, dict[str, Any]]:
    response = client.post(f"/{server_slug}/mcp", json=rpc("tools/list"))
    assert response.status_code == 200
    body = response.json()
    assert "result" in body
    return {item["name"]: item for item in body["result"]["tools"]}


def call_tool(client: TestClient, server_slug: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/{server_slug}/mcp",
        json=rpc("tools/call", {"name": name, "arguments": arguments}),
    )
    assert response.status_code == 200
    body = response.json()
    assert "result" in body, body
    result = body["result"]
    assert result["isError"] is False, result
    return result


def assert_annotations(
    tools: dict[str, dict[str, Any]],
    expected: dict[str, tuple[bool, bool]],
) -> None:
    assert set(expected).issubset(tools)
    for name, (read_only, destructive) in expected.items():
        annotations = tools[name]["annotations"]
        assert annotations["readOnlyHint"] is read_only
        assert annotations["destructiveHint"] is destructive
        assert annotations["openWorldHint"] is False


def test_design_assets_tools_list_includes_expected_annotations(client: TestClient) -> None:
    tools = list_tools(client, "design-assets")

    assert_annotations(
        tools,
        {
            "asset_search": (True, False),
            "color_tokens": (True, False),
            "component_get": (True, False),
            "asset_register": (False, False),
            "asset_deprecate": (False, True),
        },
    )


def test_finance_ledger_tools_list_includes_expected_annotations(client: TestClient) -> None:
    tools = list_tools(client, "finance-ledger")

    assert_annotations(
        tools,
        {
            "ledger_summary": (True, False),
            "transaction_search": (True, False),
            "transaction_create": (False, False),
            "transaction_categorize": (False, False),
            "transaction_delete": (False, True),
        },
    )


def test_design_assets_read_write_and_destructive_calls(client: TestClient) -> None:
    search = call_tool(client, "design-assets", "asset_search", {"query": "button"})
    search_content = search["structuredContent"]
    assert search_content["count"] >= 1
    button = search_content["items"][0]
    assert button["asset_id"] == "asset_button_primary"

    component = call_tool(
        client,
        "design-assets",
        "component_get",
        {"component_id": button["component_id"]},
    )
    assert component["structuredContent"]["component"]["tokens"]["background"] == "color.action.primary"

    colors = call_tool(client, "design-assets", "color_tokens", {"theme": "dark"})
    assert colors["structuredContent"]["count"] >= 2
    assert {item["theme"] for item in colors["structuredContent"]["items"]}.issubset({"dark", "all"})

    created = call_tool(
        client,
        "design-assets",
        "asset_register",
        {
            "name": "Worker C Demo Badge",
            "type": "component",
            "description": "Badge component created by the design/finance demo worker test.",
            "tags": ["badge", "worker-c"],
            "component_spec": {
                "variants": [{"name": "success", "state": "enabled"}],
                "tokens": {"background": "color.feedback.warning"},
            },
        },
    )
    asset = created["structuredContent"]["asset"]
    assert asset["asset_id"] == "asset_demo_001"
    assert asset["component_id"] == "component_demo_001"

    registered_search = call_tool(
        client,
        "design-assets",
        "asset_search",
        {"query": "worker-c", "type": "component"},
    )
    assert registered_search["structuredContent"]["items"][0]["asset_id"] == asset["asset_id"]

    deprecated = call_tool(
        client,
        "design-assets",
        "asset_deprecate",
        {"asset_id": asset["asset_id"], "reason": "Worker C destructive demo"},
    )
    assert deprecated["structuredContent"]["asset"]["status"] == "deprecated"

    hidden_after_deprecate = call_tool(
        client,
        "design-assets",
        "asset_search",
        {"query": "worker-c"},
    )
    assert hidden_after_deprecate["structuredContent"]["count"] == 0

    visible_with_deprecated = call_tool(
        client,
        "design-assets",
        "asset_search",
        {"query": "worker-c", "include_deprecated": True},
    )
    assert visible_with_deprecated["structuredContent"]["items"][0]["status"] == "deprecated"


def test_finance_ledger_read_write_and_destructive_calls(client: TestClient) -> None:
    summary = call_tool(client, "finance-ledger", "ledger_summary", {"month": "2026-05"})
    summary_content = summary["structuredContent"]
    assert summary_content["transaction_count"] == 4
    assert summary_content["income_cents"] == 420000
    assert summary_content["expense_cents"] == 12114
    assert summary_content["net_cents"] == 407886

    created = call_tool(
        client,
        "finance-ledger",
        "transaction_create",
        {
            "date": "2026-05-10",
            "description": "Worker C demo receipt",
            "amount_cents": -1234,
            "category": "Office",
            "account": "Demo Credit Card",
            "merchant": "Demo Stationery",
        },
    )
    transaction = created["structuredContent"]["transaction"]
    assert transaction["transaction_id"] == "txn_demo_001"
    assert transaction["type"] == "expense"

    updated_summary = call_tool(client, "finance-ledger", "ledger_summary", {"month": "2026-05"})
    assert updated_summary["structuredContent"]["expense_cents"] == 13348

    categorized = call_tool(
        client,
        "finance-ledger",
        "transaction_categorize",
        {"transaction_id": transaction["transaction_id"], "category": "Software"},
    )
    assert categorized["structuredContent"]["old_category"] == "Office"
    assert categorized["structuredContent"]["new_category"] == "Software"

    software_search = call_tool(
        client,
        "finance-ledger",
        "transaction_search",
        {"month": "2026-05", "category": "Software"},
    )
    assert transaction["transaction_id"] in {
        item["transaction_id"] for item in software_search["structuredContent"]["items"]
    }

    deleted = call_tool(
        client,
        "finance-ledger",
        "transaction_delete",
        {"transaction_id": transaction["transaction_id"]},
    )
    assert deleted["structuredContent"]["deleted"]["transaction_id"] == transaction["transaction_id"]

    empty_search = call_tool(
        client,
        "finance-ledger",
        "transaction_search",
        {"query": transaction["transaction_id"]},
    )
    assert empty_search["structuredContent"]["count"] == 0

    restored_summary = call_tool(client, "finance-ledger", "ledger_summary", {"month": "2026-05"})
    assert restored_summary["structuredContent"]["expense_cents"] == 12114


def test_reset_state_restores_design_and_finance_mutations(client: TestClient) -> None:
    design_created = call_tool(
        client,
        "design-assets",
        "asset_register",
        {"name": "Reset Marker Asset", "type": "icon", "tags": ["reset-marker"]},
    )
    finance_created = call_tool(
        client,
        "finance-ledger",
        "transaction_create",
        {
            "date": "2026-05-12",
            "description": "Reset Marker Transaction",
            "amount_cents": -777,
            "category": "Demo",
        },
    )

    assert design_created["structuredContent"]["asset"]["asset_id"] == "asset_demo_001"
    assert finance_created["structuredContent"]["transaction"]["transaction_id"] == "txn_demo_001"
    assert call_tool(client, "design-assets", "asset_search", {"query": "reset-marker"})["structuredContent"][
        "count"
    ] == 1
    assert call_tool(
        client,
        "finance-ledger",
        "transaction_search",
        {"query": "Reset Marker Transaction"},
    )["structuredContent"]["count"] == 1

    reset = client.post("/_test/reset-state")
    assert reset.status_code == 200
    assert {"design-assets", "finance-ledger"}.issubset(set(reset.json()["reset"]))

    assert call_tool(client, "design-assets", "asset_search", {"query": "reset-marker"})["structuredContent"][
        "count"
    ] == 0
    assert call_tool(
        client,
        "finance-ledger",
        "transaction_search",
        {"query": "Reset Marker Transaction"},
    )["structuredContent"]["count"] == 0

    finance_after_reset = call_tool(client, "finance-ledger", "ledger_summary", {"month": "2026-05"})
    assert finance_after_reset["structuredContent"]["transaction_count"] == 4
    assert finance_after_reset["structuredContent"]["expense_cents"] == 12114
