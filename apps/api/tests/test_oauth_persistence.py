from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from coremcp.main import create_app
from coremcp.settings import Settings

TOKEN = "cmcp_admin_testtoken"


def auth_headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@asynccontextmanager
async def oauth_client(db_path: Path, secrets_path: Path):
    async def downstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "unexpected downstream call"})

    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(downstream_handler))
    app = create_app(
        settings=Settings(
            AUTH_MODE="oauth",
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=db_path.parent / "missing-admin-token",
            COREMCP_DB_PATH=db_path,
            FAKE_MCP_URL="http://fake.local/mcp",
            COREMCP_SSRF_ALLOW_HOSTS="fake.local",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=secrets_path,
        ),
        http_client=downstream_client,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, app
    await downstream_client.aclose()


async def issue_oauth_tokens(client: httpx.AsyncClient) -> tuple[str, str, str, str]:
    verifier = "p" * 64
    resource = "http://testserver/mcp"
    redirect_uri = "http://localhost/persist"
    registered = await client.post(
        "/oauth/register",
        json={"client_name": "Persistent OAuth Client", "redirect_uris": [redirect_uri]},
    )
    assert registered.status_code == 201
    client_id = registered.json()["client_id"]

    authorize = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "resource": resource,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert authorize.status_code == 302
    code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]

    token = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "resource": resource,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200
    body = token.json()
    return client_id, body["access_token"], body["refresh_token"], code


@pytest.mark.asyncio
async def test_oauth_authorization_code_binding_mismatch_does_not_consume_code(tmp_path: Path):
    db_path = tmp_path / "oauth-code-binding.sqlite3"
    secrets_path = tmp_path / "oauth-secrets.json"
    verifier = "q" * 64
    resource = "http://testserver/mcp"
    redirect_uri = "http://localhost/persist"

    async with oauth_client(db_path, secrets_path) as (client, _app):
        registered = await client.post(
            "/oauth/register",
            json={"client_name": "Binding Client", "redirect_uris": [redirect_uri]},
        )
        client_id = registered.json()["client_id"]
        authorize = await client.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "resource": resource,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]

        wrong_binding = await client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "redirect_uri": "http://localhost/wrong",
                "resource": resource,
                "code_verifier": verifier,
            },
        )
        assert wrong_binding.status_code == 400

        correct_binding = await client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "resource": resource,
                "code_verifier": verifier,
            },
        )
        assert correct_binding.status_code == 200


@pytest.mark.asyncio
async def test_oauth_tokens_and_revocation_survive_app_recreate(tmp_path: Path):
    db_path = tmp_path / "oauth-persistence.sqlite3"
    secrets_path = tmp_path / "oauth-secrets.json"

    async with oauth_client(db_path, secrets_path) as (client, _app):
        client_id, access_token, refresh_token, authorization_code = await issue_oauth_tokens(client)
        ping = await client.post("/mcp", headers=auth_headers(access_token), json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert ping.status_code == 200

    # Raw authorization codes and refresh tokens must not be written to SQLite.
    db_blob = db_path.read_bytes()
    assert authorization_code.encode() not in db_blob
    assert refresh_token.encode() not in db_blob

    async with oauth_client(db_path, secrets_path) as (client, _app):
        # Signing key persistence: access token issued before restart remains valid.
        ping = await client.post("/mcp", headers=auth_headers(access_token), json={"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert ping.status_code == 200

        # DCR client and refresh-token family persistence: refresh works after restart and rotates.
        refreshed = await client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "resource": "http://testserver/mcp",
            },
        )
        assert refreshed.status_code == 200
        rotated_refresh_token = refreshed.json()["refresh_token"]
        assert rotated_refresh_token != refresh_token

        reused = await client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "resource": "http://testserver/mcp",
            },
        )
        assert reused.status_code == 400
        assert reused.json()["error"] == "invalid_grant"

        family_revoked = await client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": rotated_refresh_token,
                "client_id": client_id,
                "resource": "http://testserver/mcp",
            },
        )
        assert family_revoked.status_code == 400
        assert family_revoked.json()["error"] == "invalid_grant"

        revoked = await client.post("/oauth/revoke", data={"token": access_token})
        assert revoked.status_code == 200

    async with oauth_client(db_path, secrets_path) as (client, _app):
        ping_after_revoke_restart = await client.post(
            "/mcp",
            headers=auth_headers(access_token),
            json={"jsonrpc": "2.0", "id": 3, "method": "ping"},
        )
        assert ping_after_revoke_restart.status_code == 401

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        connection = conn.execute("SELECT oauth_client_id FROM external_connections LIMIT 1").fetchone()
        assert connection is not None
        assert connection["oauth_client_id"] == client_id
        assert conn.execute("SELECT COUNT(*) FROM oauth_signing_keys").fetchone()[0] == 1
        key_row = conn.execute("SELECT private_key_pem FROM oauth_signing_keys LIMIT 1").fetchone()
        assert key_row is not None
        assert str(key_row["private_key_pem"]).startswith("fernet:coremcp:oauth-signing-key:")
        assert "BEGIN PRIVATE KEY" not in str(key_row["private_key_pem"])
        assert conn.execute("SELECT COUNT(*) FROM oauth_revoked_access_tokens").fetchone()[0] == 1
    assert "BEGIN PRIVATE KEY" not in secrets_path.read_text(encoding="utf-8")
