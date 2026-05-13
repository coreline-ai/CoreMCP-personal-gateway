from __future__ import annotations

from alembic import op

revision = "20260513_0003"
down_revision = "20260513_0002"
branch_labels = None
depends_on = None


EXTERNAL_CONNECTIONS_WITH_CODEX_SQL = """
CREATE TABLE external_connections_new (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  toolbox_id TEXT REFERENCES toolboxes(id),
  client_type TEXT NOT NULL CHECK (client_type IN ('codex_cli', 'claude_code', 'claude', 'claude_desktop', 'chatgpt', 'openclaw', 'cursor', 'windsurf', 'other')),
  client_name TEXT,
  oauth_client_id TEXT,
  protocol_version TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  scopes TEXT NOT NULL DEFAULT '[]',
  client_quirks TEXT NOT NULL DEFAULT '{}',
  created_ip TEXT,
  created_user_agent TEXT,
  last_used_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        # CoreMCP personal gateway currently ships SQLite migrations. Keep
        # non-SQLite environments forward-compatible with repository SCHEMA_SQL.
        return

    existing = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='external_connections'"
    ).fetchone()
    if not existing:
        return

    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    connection.exec_driver_sql(EXTERNAL_CONNECTIONS_WITH_CODEX_SQL)
    connection.exec_driver_sql(
        """
        INSERT INTO external_connections_new (
          id, user_id, toolbox_id, client_type, client_name, oauth_client_id,
          protocol_version, status, scopes, client_quirks, created_ip,
          created_user_agent, last_used_at, revoked_at, created_at, updated_at
        )
        SELECT
          id, user_id, toolbox_id, client_type, client_name, oauth_client_id,
          protocol_version, status, scopes, client_quirks, created_ip,
          created_user_agent, last_used_at, revoked_at, created_at, updated_at
        FROM external_connections
        """
    )
    connection.exec_driver_sql("DROP TABLE external_connections")
    connection.exec_driver_sql("ALTER TABLE external_connections_new RENAME TO external_connections")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_ext_user ON external_connections(user_id)")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_ext_client ON external_connections(client_type, status)")
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    # Personal gateway migrations are forward-only for now. Keep downgrade
    # intentionally empty to avoid accidental local data loss.
    pass
