from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from coremcp.db.repository import DEFAULT_TOOLBOX_ID, LOCAL_USER_ID, Repository

HEAD_REVISION = "20260515_0008"


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _column_names(db_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _scalar(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> object:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


async def _connect_and_close(db_path: Path) -> None:
    repo = Repository(db_path)
    try:
        await repo.connect()
        assert await repo.healthcheck()
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_repository_connect_empty_sqlite_file_runs_migrations_and_bootstrap(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.sqlite3"

    await _connect_and_close(db_path)

    assert {
        "alembic_version",
        "users",
        "toolboxes",
        "mcp_services",
        "service_tools",
        "external_connections",
        "personal_access_tokens",
        "oauth_clients",
        "service_resources",
        "service_resource_templates",
        "service_prompts",
        "mcp_sessions",
        "tool_invocations",
        "jobs",
    } <= _table_names(db_path)
    assert _scalar(db_path, "SELECT version_num FROM alembic_version") == HEAD_REVISION
    assert _scalar(db_path, "SELECT COUNT(*) FROM users WHERE id = ?", (LOCAL_USER_ID,)) == 1
    assert _scalar(db_path, "SELECT COUNT(*) FROM toolboxes WHERE id = ?", (DEFAULT_TOOLBOX_ID,)) == 1


def _create_legacy_0001_like_db(db_path: Path) -> None:
    """Create a compact 0001-era file DB without Phase 2 transport columns."""

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);
            INSERT INTO alembic_version (version_num) VALUES ('20260512_0001');

            CREATE TABLE users (
              id TEXT PRIMARY KEY,
              email TEXT NOT NULL UNIQUE DEFAULT 'me@local',
              name TEXT NOT NULL DEFAULT 'Personal',
              avatar_url TEXT,
              locale TEXT NOT NULL DEFAULT 'ko',
              is_active INTEGER NOT NULL DEFAULT 1,
              bootstrap_completed_at TIMESTAMP,
              last_login_at TIMESTAMP,
              workspace_id TEXT,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              deleted_at TIMESTAMP
            );

            CREATE TABLE toolboxes (
              id TEXT PRIMARY KEY,
              owner_user_id TEXT NOT NULL REFERENCES users(id),
              workspace_id TEXT,
              name TEXT NOT NULL,
              slug TEXT,
              is_default INTEGER NOT NULL DEFAULT 0,
              visibility TEXT NOT NULL DEFAULT 'private',
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              deleted_at TIMESTAMP
            );

            CREATE TABLE mcp_services (
              id TEXT PRIMARY KEY,
              owner_user_id TEXT NOT NULL REFERENCES users(id),
              workspace_id TEXT,
              name TEXT NOT NULL,
              slug TEXT NOT NULL,
              description TEXT,
              endpoint_url TEXT NOT NULL,
              auth_type TEXT NOT NULL DEFAULT 'none',
              visibility TEXT NOT NULL DEFAULT 'private',
              status TEXT NOT NULL DEFAULT 'draft',
              category TEXT,
              logo_url TEXT,
              homepage_url TEXT,
              documentation_url TEXT,
              risk_level TEXT NOT NULL DEFAULT 'unknown',
              validation_summary TEXT NOT NULL DEFAULT '{}',
              last_validated_at TIMESTAMP,
              last_tool_refresh_at TIMESTAMP,
              protocol_version TEXT,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              deleted_at TIMESTAMP
            );

            CREATE TABLE service_tools (
              id TEXT PRIMARY KEY,
              service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
              original_name TEXT NOT NULL,
              input_schema_json TEXT NOT NULL DEFAULT '{}',
              annotations TEXT NOT NULL DEFAULT '{}',
              icons_json TEXT NOT NULL DEFAULT '[]',
              schema_hash TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active'
            );

            CREATE TABLE external_connections (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id),
              toolbox_id TEXT REFERENCES toolboxes(id),
              client_type TEXT NOT NULL,
              client_name TEXT,
              oauth_client_id TEXT,
              protocol_version TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              scopes TEXT NOT NULL DEFAULT '[]',
              client_quirks TEXT NOT NULL DEFAULT '{}',
              created_ip TEXT,
              created_user_agent TEXT,
              last_used_at TIMESTAMP,
              revoked_at TIMESTAMP,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            INSERT INTO users (id, email, name, bootstrap_completed_at)
            VALUES ('usr_legacy', 'legacy@local', 'Legacy', CURRENT_TIMESTAMP);
            INSERT INTO mcp_services (id, owner_user_id, name, slug, endpoint_url, status)
            VALUES ('svc_legacy', 'usr_legacy', 'Legacy Service', 'legacy', 'http://legacy.local/mcp', 'active');
            """
        )


@pytest.mark.asyncio
async def test_repository_connect_upgrades_legacy_0001_like_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    _create_legacy_0001_like_db(db_path)

    await _connect_and_close(db_path)

    columns = _column_names(db_path, "mcp_services")
    assert {
        "transport_type",
        "stdio_command",
        "stdio_args",
        "stdio_env",
        "stdio_idle_timeout_seconds",
        "last_stdio_started_at",
        "stdio_restart_count",
        "last_stdio_stderr_tail",
    } <= columns
    assert _scalar(db_path, "SELECT version_num FROM alembic_version") == HEAD_REVISION
    assert _scalar(db_path, "SELECT transport_type FROM mcp_services WHERE id = 'svc_legacy'") == "http"
    assert _scalar(db_path, "SELECT stdio_args FROM mcp_services WHERE id = 'svc_legacy'") == "[]"
    assert _scalar(db_path, "SELECT COUNT(*) FROM mcp_services WHERE id = 'svc_legacy'") == 1


@pytest.mark.asyncio
async def test_repository_connect_close_twice_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "idempotent.sqlite3"
    repo = Repository(db_path)

    for _ in range(2):
        await repo.connect()
        assert await repo.healthcheck()
        await repo.close()

    assert _scalar(db_path, "SELECT version_num FROM alembic_version") == HEAD_REVISION
    assert _scalar(db_path, "SELECT COUNT(*) FROM users WHERE id = ?", (LOCAL_USER_ID,)) == 1
