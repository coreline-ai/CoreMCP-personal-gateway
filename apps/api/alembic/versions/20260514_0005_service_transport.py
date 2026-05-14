from __future__ import annotations

from alembic import op

revision = "20260514_0005"
down_revision = "20260514_0004"
branch_labels = None
depends_on = None


def _columns(connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in connection.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()}


def _add_column_if_missing(connection, table_name: str, column_name: str, ddl: str) -> None:
    if column_name not in _columns(connection, table_name):
        connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return

    _add_column_if_missing(connection, "mcp_services", "transport_type", "TEXT NOT NULL DEFAULT 'http'")
    _add_column_if_missing(connection, "mcp_services", "stdio_command", "TEXT")
    _add_column_if_missing(connection, "mcp_services", "stdio_args", "TEXT NOT NULL DEFAULT '[]'")
    _add_column_if_missing(connection, "mcp_services", "stdio_env", "TEXT NOT NULL DEFAULT '{}'")
    _add_column_if_missing(connection, "mcp_services", "stdio_cwd", "TEXT")
    _add_column_if_missing(connection, "mcp_services", "stdio_idle_timeout_seconds", "INTEGER NOT NULL DEFAULT 300")
    _add_column_if_missing(connection, "mcp_services", "last_health_check_at", "TIMESTAMP")
    _add_column_if_missing(connection, "mcp_services", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(connection, "mcp_services", "circuit_open_until", "REAL")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_mcp_services_transport ON mcp_services(transport_type, status)")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_mcp_services_circuit ON mcp_services(circuit_open_until) WHERE circuit_open_until IS NOT NULL")


def downgrade() -> None:
    # Personal gateway migrations are forward-only for now. Keep downgrade
    # intentionally empty to avoid accidental local data loss.
    pass
