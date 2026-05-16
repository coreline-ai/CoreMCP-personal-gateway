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
async def oauth_policy_client(tmp_path: Path):
    async def downstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "unexpected downstream call"})

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(downstream_handler))
    app = create_app(
        settings=Settings(
            AUTH_MODE="oauth",
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "oauth-policy.sqlite3",
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "oauth-policy-secrets.json",
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            yield client, app
    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_oauth_dcr_disabled_rejects_registration(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("COREMCP_OAUTH_DCR_ENABLED", "false")

    async with oauth_policy_client(tmp_path) as (client, _app):
        response = await client.post(
            "/oauth/register",
            json={"client_name": "Blocked DCR", "redirect_uris": ["http://localhost/callback"]},
        )

    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["error"] == "access_denied"
    assert "dynamic client registration is disabled" in body["error_description"]


@pytest.mark.asyncio
async def test_oauth_client_allowlist_rejects_unknown_authorize_and_token(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    allowed_client_id = "cmcp_allowed_client"
    unknown_client_id = "cmcp_unknown_client"
    resource = "http://testserver/mcp"
    redirect_uri = "http://localhost/callback"
    verifier = "p" * 64

    monkeypatch.setenv("COREMCP_OAUTH_ALLOWED_CLIENT_IDS", allowed_client_id)

    async with oauth_policy_client(tmp_path) as (client, _app):
        authorize_unknown = await client.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": unknown_client_id,
                "redirect_uri": redirect_uri,
                "resource": resource,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

        token_unknown = await client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": "cmcp_code_unknown",
                "client_id": unknown_client_id,
                "redirect_uri": redirect_uri,
                "resource": resource,
                "code_verifier": verifier,
            },
        )

        authorize_allowed = await client.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": allowed_client_id,
                "redirect_uri": redirect_uri,
                "resource": resource,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

        token_allowed = await client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": "cmcp_code_allowed_but_invalid",
                "client_id": allowed_client_id,
                "redirect_uri": redirect_uri,
                "resource": resource,
                "code_verifier": verifier,
            },
        )

    assert authorize_unknown.status_code == 403
    assert authorize_unknown.headers["cache-control"] == "no-store"
    assert authorize_unknown.json()["error"] == "unauthorized_client"

    assert token_unknown.status_code == 403
    assert token_unknown.headers["cache-control"] == "no-store"
    assert token_unknown.json()["error"] == "unauthorized_client"

    assert authorize_allowed.status_code == 401
    assert authorize_allowed.headers["cache-control"] == "no-store"
    assert authorize_allowed.json()["error"] == "invalid_client"

    assert token_allowed.status_code == 400
    assert token_allowed.headers["cache-control"] == "no-store"
    assert token_allowed.json()["error"] == "invalid_grant"
