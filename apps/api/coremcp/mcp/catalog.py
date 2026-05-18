from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from coremcp.db import DEFAULT_TOOLBOX_ID
from coremcp.mcp.context import McpHandlerContext

CATALOG_VISIBLE_SERVICE_STATUSES = {"active", "validating"}


async def active_toolbox_services(app: FastAPI, toolbox_id: str = DEFAULT_TOOLBOX_ID) -> list[dict[str, Any]]:
    ctx = McpHandlerContext.from_app(app)
    services: list[dict[str, Any]] = []
    for item in await ctx.repos.toolbox.list_toolbox_items(toolbox_id):
        if not bool(item.get("enabled")) or item.get("service_status") not in CATALOG_VISIBLE_SERVICE_STATUSES:
            continue
        service = await ctx.repos.services.get_mcp_service(str(item["service_id"]))
        if service and service.get("status") in CATALOG_VISIBLE_SERVICE_STATUSES:
            services.append(service)
    return services


async def toolbox_unavailable_services(app: FastAPI, toolbox_id: str = DEFAULT_TOOLBOX_ID) -> list[dict[str, Any]]:
    ctx = McpHandlerContext.from_app(app)
    unavailable: list[dict[str, Any]] = []
    for item in await ctx.repos.toolbox.list_toolbox_items(toolbox_id):
        if not bool(item.get("enabled")):
            continue
        service_id = str(item.get("service_id") or "")
        service_status = str(item.get("service_status") or "unknown")
        if service_status != "active":
            unavailable.append(
                {
                    "service_id": service_id,
                    "service_slug": item.get("service_slug"),
                    "status": service_status,
                    "reason": "service_not_active",
                }
            )
            continue
        if service_id:
            snapshot = ctx.circuit_breaker.snapshot(service_id)
            if snapshot.state == "open":
                unavailable.append(
                    {
                        "service_id": service_id,
                        "service_slug": item.get("service_slug"),
                        "status": "circuit_open",
                        "reason": "circuit_open",
                        "retry_after_seconds": snapshot.retry_after_seconds,
                    }
                )
    return unavailable
