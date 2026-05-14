from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest

from coremcp.main import create_app
from coremcp.settings import Settings

TOKEN = "cmcp_admin_cors_testtoken"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "COREMCP_ADMIN_TOKEN_VALUE": TOKEN,
        "COREMCP_ADMIN_TOKEN_FILE": tmp_path / "missing-admin-token",
        "COREMCP_DB_PATH": tmp_path / "coremcp.sqlite3",
        "COREMCP_SECRET_BACKEND": "fernet",
        "COREMCP_SECRETS_FILE": tmp_path / "secrets.json",
    }
    values.update(overrides)
    return Settings(**values)


@asynccontextmanager
async def _client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


def _preflight_headers(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization",
    }


@pytest.mark.asyncio
async def test_default_localhost_3003_preflight_allows_exact_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("COREMCP_CORS_ALLOWED_ORIGINS", raising=False)
    async with _client(_settings(tmp_path)) as client:
        response = await client.options("/v1/settings", headers=_preflight_headers("http://localhost:3003"))

    assert response.headers["access-control-allow-origin"] == "http://localhost:3003"


@pytest.mark.asyncio
async def test_default_localhost_3003_get_with_auth_allows_exact_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("COREMCP_CORS_ALLOWED_ORIGINS", raising=False)
    async with _client(_settings(tmp_path)) as client:
        response = await client.get(
            "/v1/settings",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Origin": "http://localhost:3003",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3003"


@pytest.mark.asyncio
async def test_disallowed_preflight_omits_allow_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("COREMCP_CORS_ALLOWED_ORIGINS", raising=False)
    async with _client(_settings(tmp_path)) as client:
        response = await client.options("/v1/settings", headers=_preflight_headers("http://evil.example"))

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_custom_cors_allowed_origins_only_allows_configured_origin(tmp_path: Path):
    async with _client(_settings(tmp_path, COREMCP_CORS_ALLOWED_ORIGINS="http://example.test")) as client:
        allowed = await client.options("/v1/settings", headers=_preflight_headers("http://example.test"))
        default_localhost = await client.options("/v1/settings", headers=_preflight_headers("http://localhost:3003"))

    assert allowed.headers["access-control-allow-origin"] == "http://example.test"
    assert "access-control-allow-origin" not in default_localhost.headers
