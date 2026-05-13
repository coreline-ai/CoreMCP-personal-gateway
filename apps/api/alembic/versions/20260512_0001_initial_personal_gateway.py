from __future__ import annotations

from alembic import op

from coremcp.db.repository import SCHEMA_SQL

revision = "20260512_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    for statement in SCHEMA_SQL.split(";"):
        sql = statement.strip()
        if sql:
            connection.exec_driver_sql(sql)


def downgrade() -> None:
    # Personal gateway migrations are forward-only for now. Keep downgrade
    # intentionally empty to avoid accidental local data loss.
    pass
