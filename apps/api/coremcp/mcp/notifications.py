from __future__ import annotations

from typing import Any

from coremcp.mcp_gateway import ListChangedCategory

LIST_CHANGED_METHOD_CATEGORIES: dict[str, ListChangedCategory] = {
    "notifications/tools/list_changed": "tools",
    "notifications/resources/list_changed": "resources",
    "notifications/prompts/list_changed": "prompts",
}

DOWNSTREAM_NOTIFICATION_METHODS = {
    "notifications/progress",
    "notifications/resources/updated",
    *LIST_CHANGED_METHOD_CATEGORIES.keys(),
}


def list_changed_category_for_method(method: Any) -> ListChangedCategory | None:
    return LIST_CHANGED_METHOD_CATEGORIES.get(method)


def is_downstream_notification_method(method: Any) -> bool:
    return isinstance(method, str) and method in DOWNSTREAM_NOTIFICATION_METHODS


def notification_params(notification: dict[str, Any]) -> dict[str, Any]:
    params = notification.get("params")
    return params if isinstance(params, dict) else {}
