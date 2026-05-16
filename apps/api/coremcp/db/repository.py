from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any
import aiosqlite

from coremcp.db.migrations import run_migrations
from coremcp.db.repository_audit import AuditRepositoryMixin
from coremcp.db.repository_catalog import CatalogRepositoryMixin
from coremcp.db.repository_connections import ConnectionsRepositoryMixin
from coremcp.db.repository_constants import DEFAULT_TOOLBOX_ID, LOCAL_USER_ID
from coremcp.db.repository_credentials import CredentialsRepositoryMixin
from coremcp.db.repository_ids import new_id as new_id
from coremcp.db.repository_jobs import JobsRepositoryMixin
from coremcp.db.repository_services import ServicesRepositoryMixin
from coremcp.db.repository_toolbox import ToolboxRepositoryMixin


class Repository(
    ServicesRepositoryMixin,
    CatalogRepositoryMixin,
    ToolboxRepositoryMixin,
    CredentialsRepositoryMixin,
    ConnectionsRepositoryMixin,
    AuditRepositoryMixin,
    JobsRepositoryMixin,
):
    """SQLite repository for the personal CoreMCP gateway.

    The repository intentionally keeps a small aiosqlite surface for the P1
    single-process runtime while Alembic owns the same schema for explicit CLI
    migrations. Startup bootstrap is idempotent so tests and local dev do not
    need to invoke Alembic manually.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if str(self.database_path) == ":memory:":
            raise RuntimeError("Repository database_path=':memory:' is not supported; use a file-backed SQLite path")

        database_path = self.database_path.expanduser().resolve()
        await asyncio.to_thread(run_migrations, database_path)

        self._db = await aiosqlite.connect(str(database_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self.bootstrap()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Repository is not connected")
        return self._db

    async def healthcheck(self) -> bool:
        cursor = await self.db.execute("SELECT 1 AS ok")
        row = await cursor.fetchone()
        return bool(row and row["ok"] == 1)

    async def bootstrap(self) -> None:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO users (id, email, name, bootstrap_completed_at)
            VALUES (?, 'me@local', 'Personal', CURRENT_TIMESTAMP)
            """,
            (LOCAL_USER_ID,),
        )
        await self.db.execute(
            """
            INSERT OR IGNORE INTO toolboxes (id, owner_user_id, name, slug, is_default)
            VALUES (?, ?, 'Default', 'default', 1)
            """,
            (DEFAULT_TOOLBOX_ID, LOCAL_USER_ID),
        )
        await self.db.commit()

    # ------------------------------------------------------------------
    # JSON helpers
    # ------------------------------------------------------------------
    @staticmethod
    def dumps_json(value: Any) -> str:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def dumps_json_array(value: Any) -> str:
        return json.dumps(value if value is not None else [], ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def loads_json(value: Any, default: Any = None) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(str(value))
        except json.JSONDecodeError:
            return default

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row | None, json_fields: Iterable[str] = ()) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        for field in json_fields:
            if field in item:
                fallback: Any = [] if field.endswith("s") or field.endswith("_json") else {}
                item[field] = Repository.loads_json(item[field], fallback)
        return item
