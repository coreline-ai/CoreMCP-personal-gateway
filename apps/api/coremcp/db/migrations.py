from __future__ import annotations

import os
import threading
from pathlib import Path

from alembic import command
from alembic.config import Config

_MIGRATION_LOCK = threading.Lock()


def run_migrations(database_path: Path) -> None:
    """Run Alembic migrations to head for a file-backed SQLite database.

    `:memory:` SQLite databases are intentionally unsupported here because
    Alembic runs through a separate synchronous connection; by the time
    Repository opens its aiosqlite connection it would see a different empty
    in-memory database.
    """

    if str(database_path) == ":memory:":
        raise RuntimeError("Repository database_path=':memory:' is not supported; use a file-backed SQLite path")

    resolved_path = database_path.expanduser().resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    api_dir = Path(__file__).resolve().parents[2]
    config = Config(str(api_dir / "alembic.ini"))
    config.set_main_option("script_location", str(api_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{resolved_path}")

    # apps/api/alembic/env.py resolves the database from COREMCP_DB_PATH.
    # Guard the temporary environment override because process env is global.
    with _MIGRATION_LOCK:
        previous_db_path = os.environ.get("COREMCP_DB_PATH")
        os.environ["COREMCP_DB_PATH"] = str(resolved_path)
        try:
            command.upgrade(config, "head")
        finally:
            if previous_db_path is None:
                os.environ.pop("COREMCP_DB_PATH", None)
            else:
                os.environ["COREMCP_DB_PATH"] = previous_db_path
