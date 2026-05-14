from __future__ import annotations

import stat
from pathlib import Path

import httpx
import pytest

from coremcp.main import create_app
from coremcp.settings import Settings

TOKEN = "cmcp_admin_testtoken"


def auth_headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_admin_token_rotate_writes_file_and_rejects_old_token(tmp_path: Path) -> None:
    async def downstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "unexpected downstream call"})

    token_file = tmp_path / "admin-token"
    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(downstream_handler))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=token_file,
            COREMCP_DB_PATH=tmp_path / "rotate.sqlite3",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "secrets.json",
        ),
        http_client=downstream_client,
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            rotated = await client.post("/v1/settings/admin-token/rotate", headers=auth_headers())
            assert rotated.status_code == 200
            body = rotated.json()
            new_token = body["new_token"]
            assert new_token.startswith("cmcp_admin_")
            assert body["admin_token_masked"].startswith("cmcp")
            assert body["expires_at"] is None

            assert token_file.read_text(encoding="utf-8").strip() == new_token
            assert stat.S_IMODE(token_file.stat().st_mode) == 0o600

            old_token = await client.get("/v1/settings", headers=auth_headers())
            assert old_token.status_code == 401
            new_token_response = await client.get("/v1/settings", headers=auth_headers(new_token))
            assert new_token_response.status_code == 200

            audit = await app.state.repository.recent_audit_logs(limit=5, action="admin_token.rotate")
            assert audit
            assert audit[0]["metadata"]["admin_token_masked"] == body["admin_token_masked"]
            assert new_token not in str(audit[0]["metadata"])

    await downstream_client.aclose()


@pytest.mark.asyncio
async def test_admin_token_rotate_rejects_unwritable_token_file(tmp_path: Path) -> None:
    async def downstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "unexpected downstream call"})

    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied", encoding="utf-8")
    downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(downstream_handler))
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=parent_file / "admin-token",
            COREMCP_DB_PATH=tmp_path / "rotate-unwritable.sqlite3",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "unwritable-secrets.json",
        ),
        http_client=downstream_client,
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            rotated = await client.post("/v1/settings/admin-token/rotate", headers=auth_headers())
            assert rotated.status_code == 409
            assert rotated.json()["error"]["code"] == "admin_token_file_unavailable"
            still_env = await client.get("/v1/settings", headers=auth_headers())
            assert still_env.status_code == 200

    await downstream_client.aclose()
