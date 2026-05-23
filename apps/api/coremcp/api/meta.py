from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response

from coremcp.api._schemas import HealthResponse
from coremcp.api.dependencies import get_repository, get_repos, get_settings, get_vault
from coremcp.db.repository import Repository
from coremcp.db.repository_facade import RepositoryFacades
from coremcp.settings import Settings


def register_meta_routes(
    app: FastAPI,
    *,
    prometheus_metrics: Callable[[dict[str, int]], str],
) -> None:
    """Register health, readiness, and metrics endpoints.

    These routes are intentionally kept dependency-light so they can be split
    before the larger MCP/OAuth/admin routers without changing response shapes.
    """

    @app.get("/health", response_model=HealthResponse)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/live", response_model=HealthResponse)
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/ready", response_model=HealthResponse)
    async def ready(
        repository: Repository = Depends(get_repository),
        vault: Any = Depends(get_vault),
    ) -> dict[str, str]:
        db_ok = await repository.healthcheck()
        vault_ok = await vault.is_ready()
        return {"status": "ready" if db_ok and vault_ok else "degraded"}

    @app.get("/metrics")
    async def metrics(
        request: Request,
        settings: Settings = Depends(get_settings),
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not settings.metrics_enabled:
            return Response(status_code=404)
        snapshot = await repos.audit.metrics_snapshot()
        snapshot["active_mcp_sessions"] = request.app.state.sessions.count_active()
        return Response(
            prometheus_metrics(snapshot),
            media_type="text/plain; version=0.0.4; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
