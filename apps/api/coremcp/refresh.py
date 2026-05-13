from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from coremcp.main import create_app, validate_service
from coremcp.settings import get_settings


def _refresh_statuses() -> set[str]:
    raw = os.getenv("COREMCP_REFRESH_STATUSES", "active")
    return {item.strip() for item in raw.split(",") if item.strip()}


async def run_refresh_once() -> dict[str, Any]:
    """Validate registered services on a local schedule.

    This is intentionally a small single-process maintenance entry point for the
    personal gateway. It reuses the app lifespan so repository, vault, settings,
    HTTP timeouts, SSRF guard, and downstream response sanitizer stay identical
    to the API runtime.
    """

    settings = get_settings()
    statuses = _refresh_statuses()
    started_at = time.time()
    app = create_app(settings=settings)
    results: list[dict[str, Any]] = []

    async with app.router.lifespan_context(app):
        services = await app.state.repository.list_mcp_services(limit=500)
        candidates = [service for service in services if str(service.get("status") or "") in statuses]
        for service in candidates:
            service_id = str(service["id"])
            try:
                report = await validate_service(
                    app,
                    service_id,
                    correlation_id_value=f"scheduled-refresh-{service_id}-{int(started_at)}",
                )
                results.append(
                    {
                        "service_id": service_id,
                        "slug": service.get("slug"),
                        "status": report.get("status", "success"),
                        "tools_found": report.get("tools_found", 0),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - scheduled job must continue through all services.
                results.append(
                    {
                        "service_id": service_id,
                        "slug": service.get("slug"),
                        "status": "failed",
                        "error": str(exc),
                    }
                )

    failed = sum(1 for item in results if item.get("status") == "failed")
    return {
        "status": "completed",
        "candidate_statuses": sorted(statuses),
        "services_checked": len(results),
        "failed": failed,
        "duration_ms": round((time.time() - started_at) * 1000),
        "results": results,
    }


async def main() -> int:
    print(json.dumps(await run_refresh_once(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
