from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from coremcp.db import DEFAULT_TOOLBOX_ID

DEFAULT_SERVER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "tools": {"listChanged": True},
    "resources": {"listChanged": True, "subscribe": False},
    "prompts": {"listChanged": True},
}


def capability_present(capabilities: dict[str, Any], key: str) -> bool:
    value = capabilities.get(key)
    return isinstance(value, dict)


def summary_supports(summary: dict[str, Any], *keys: str) -> bool:
    catalog = summary.get("resource_prompt_catalog") if isinstance(summary.get("resource_prompt_catalog"), dict) else {}
    return any(bool(catalog.get(key)) for key in keys)


async def server_capabilities_for_default_toolbox(app: FastAPI) -> dict[str, Any]:
    items = [
        item
        for item in await app.state.repository.list_toolbox_items(DEFAULT_TOOLBOX_ID)
        if bool(item.get("enabled")) and item.get("service_status") == "active"
    ]
    if not items:
        return dict(DEFAULT_SERVER_CAPABILITIES)

    capabilities: dict[str, Any] = {"tools": {"listChanged": True}}
    resources_supported = False
    prompts_supported = False
    for item in items:
        service = await app.state.repository.get_mcp_service(str(item.get("service_id") or ""))
        if not service:
            continue
        downstream_capabilities = service.get("capabilities_json") if isinstance(service.get("capabilities_json"), dict) else {}
        summary = service.get("validation_summary") if isinstance(service.get("validation_summary"), dict) else {}
        resources_supported = resources_supported or capability_present(downstream_capabilities, "resources") or summary_supports(
            summary,
            "resources_supported",
            "resource_templates_supported",
            "resources_found",
            "resource_templates_found",
        )
        prompts_supported = prompts_supported or capability_present(downstream_capabilities, "prompts") or summary_supports(
            summary,
            "prompts_supported",
            "prompts_found",
        )

    if resources_supported:
        capabilities["resources"] = {"listChanged": True, "subscribe": False}
    if prompts_supported:
        capabilities["prompts"] = {"listChanged": True}
    return capabilities
