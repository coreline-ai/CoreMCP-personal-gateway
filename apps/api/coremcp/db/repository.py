import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite


class Repository:
    """Minimal SQLite repository for P0 bootstrap and invocation logging."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if str(self.database_path) != ":memory:":
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.database_path))
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

    async def bootstrap(self) -> None:
        await self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,
              email TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              locale TEXT NOT NULL DEFAULT 'ko',
              is_active INTEGER NOT NULL DEFAULT 1,
              bootstrap_completed_at TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              deleted_at TEXT
            );

            CREATE TABLE IF NOT EXISTS toolboxes (
              id TEXT PRIMARY KEY,
              owner_user_id TEXT NOT NULL REFERENCES users(id),
              name TEXT NOT NULL,
              is_default INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tool_invocations (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id),
              toolbox_id TEXT NOT NULL REFERENCES toolboxes(id),
              session_id TEXT,
              method TEXT NOT NULL,
              tool_name TEXT,
              status TEXT NOT NULL,
              error_code INTEGER,
              latency_ms INTEGER,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await self.db.execute(
            """
            INSERT OR IGNORE INTO users (id, email, name, bootstrap_completed_at)
            VALUES ('usr_local', 'me@local', 'Personal', CURRENT_TIMESTAMP)
            """
        )
        await self.db.execute(
            """
            INSERT OR IGNORE INTO toolboxes (id, owner_user_id, name, is_default)
            VALUES ('tbx_default', 'usr_local', 'Default', 1)
            """
        )
        await self.db.commit()

    async def log_invocation(
        self,
        *,
        session_id: str | None,
        method: str,
        tool_name: str | None,
        status: str,
        error_code: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO tool_invocations
              (id, user_id, toolbox_id, session_id, method, tool_name, status, error_code, latency_ms)
            VALUES (?, 'usr_local', 'tbx_default', ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), session_id, method, tool_name, status, error_code, latency_ms),
        )
        await self.db.commit()

    async def count_invocations(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) AS count FROM tool_invocations")
        row = await cursor.fetchone()
        return int(row["count"])

    async def recent_invocations(self, limit: int = 20) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT id, method, tool_name, status, error_code, latency_ms, created_at
            FROM tool_invocations
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def dumps_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
