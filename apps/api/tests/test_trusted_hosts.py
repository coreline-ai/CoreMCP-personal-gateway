from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest

from coremcp.main import create_app
from coremcp.settings import Settings

TOKEN = "cmcp_admin_trusted_hosts_testtoken"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "COREMCP_ADMIN_TOKEN_VALUE": TOKEN,
        "COREMCP_ADMIN_TOKEN_FILE": tmp_path / "missing-admin-token",
        "COREMCP_DB_PATH": tmp_path / "coremcp.sqlite3",
        "COREMCP_SECRET_BACKEND": "fernet",
        "COREMCP_SECRETS_FILE": tmp_path / "secrets.json",
        "COREMCP_SERVICE_HEALTH_PROBE_ENABLED": False,
    }
    values.update(overrides)
    return Settings(**values)


@asynccontextmanager
async def _client(settings: Settings, *, base_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
            yield client


@pytest.mark.asyncio
async def test_default_allowed_host_allows_testserver_health(tmp_path: Path) -> None:
    async with _client(_settings(tmp_path), base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_untrusted_host_returns_invalid_host_header(tmp_path: Path) -> None:
    async with _client(_settings(tmp_path), base_url="http://evil.example") as client:
        response = await client.get("/health")

    assert response.status_code == 400
    assert response.text == "Invalid host header"


@pytest.mark.asyncio
async def test_custom_allowed_hosts_allows_configured_host(tmp_path: Path) -> None:
    settings = _settings(tmp_path, COREMCP_ALLOWED_HOSTS="coremcp.local")

    async with _client(settings, base_url="http://coremcp.local") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
