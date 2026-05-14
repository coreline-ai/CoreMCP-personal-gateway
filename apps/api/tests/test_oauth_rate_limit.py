from __future__ import annotations

import base64
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from coremcp.main import create_app
from coremcp.settings import Settings

TOKEN = "cmcp_admin_testtoken"


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@asynccontextmanager
async def oauth_rate_client(tmp_path: Path, *, transport_handler: Any | None = None, allow_hosts: str = "fake.local"):
    async def default_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "unexpected downstream call"})

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler or default_handler))
    app = create_app(
        settings=Settings(
            AUTH_MODE="oauth",
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "oauth-rate.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS=allow_hosts,
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "oauth-rate-secrets.json",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            yield client, app
    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_oauth_dcr_rate_limit_is_10_per_hour_per_ip(tmp_path: Path) -> None:
    async with oauth_rate_client(tmp_path) as (client, _app):
        for index in range(10):
            response = await client.post(
                "/oauth/register",
                json={"client_name": f"DCR {index}", "redirect_uris": [f"http://localhost/callback-{index}"]},
            )
            assert response.status_code == 201

        limited = await client.post(
            "/oauth/register",
            json={"client_name": "DCR limited", "redirect_uris": ["http://localhost/limited"]},
        )
        assert limited.status_code == 429
        assert limited.json()["error"] == "rate_limited"
        assert "dynamic client registration" in limited.json()["error_description"]
        assert int(limited.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_oauth_cimd_rate_limit_is_30_per_hour_per_ip(tmp_path: Path) -> None:
    async def cimd_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and url.startswith("https://client.example/"):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "client_id": url,
                    "client_name": "CIMD Rate Client",
                    "redirect_uris": ["https://client.example/callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                    "scope": "mcp:tools.read mcp:tools.call",
                },
            )
        return httpx.Response(500, json={"error": "unexpected downstream call"})

    async with oauth_rate_client(tmp_path, transport_handler=cimd_handler, allow_hosts="client.example") as (client, _app):
        verifier = "r" * 64
        for index in range(30):
            response = await client.get(
                "/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": f"https://client.example/client-{index}",
                    "redirect_uri": "https://client.example/callback",
                    "resource": "http://testserver/mcp",
                    "code_challenge": pkce_challenge(verifier),
                    "code_challenge_method": "S256",
                },
                follow_redirects=False,
            )
            assert response.status_code == 302

        limited = await client.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": "https://client.example/client-limited",
                "redirect_uri": "https://client.example/callback",
                "resource": "http://testserver/mcp",
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            },
        )
        assert limited.status_code == 429
        assert limited.json()["error"] == "rate_limited"
        assert "CIMD" in limited.json()["error_description"]
        assert int(limited.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_oauth_rate_limits_do_not_expose_oauth_routes_in_static_mode(tmp_path: Path) -> None:
    async def downstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "unexpected downstream call"})

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(downstream_handler))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "static.sqlite3",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "static-secrets.json",
        ),
        http_client=downstream_client,
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            for _ in range(12):
                response = await client.post("/oauth/register", json={})
                assert response.status_code == 404

    await downstream_client.aclose()
