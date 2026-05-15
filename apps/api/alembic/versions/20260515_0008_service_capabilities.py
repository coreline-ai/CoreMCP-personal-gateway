from __future__ import annotations

from alembic import op

revision = "20260515_0008"
down_revision = "20260514_0007"
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

    _add_column_if_missing(connection, "mcp_services", "capabilities_json", "TEXT NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    # Personal gateway migrations are forward-only for now. Keep downgrade
    # intentionally empty to avoid accidental local data loss.
    pass
