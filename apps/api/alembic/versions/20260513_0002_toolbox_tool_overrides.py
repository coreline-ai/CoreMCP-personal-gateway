from __future__ import annotations

from alembic import op

revision = "20260513_0002"
down_revision = "20260512_0001"
branch_labels = None
depends_on = None


TOOLBOX_TOOL_OVERRIDES_SQL = """
CREATE TABLE IF NOT EXISTS toolbox_tool_overrides (
  id TEXT PRIMARY KEY,
  toolbox_id TEXT NOT NULL REFERENCES toolboxes(id) ON DELETE CASCADE,
  service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  service_tool_id TEXT NOT NULL REFERENCES service_tools(id) ON DELETE CASCADE,
  enabled INTEGER NOT NULL DEFAULT 1,
  permission_level TEXT NOT NULL DEFAULT 'callable' CHECK (permission_level IN ('hidden', 'visible_only', 'callable')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(toolbox_id, service_tool_id)
);
CREATE INDEX IF NOT EXISTS idx_tto_toolbox_service ON toolbox_tool_overrides(toolbox_id, service_id);
CREATE INDEX IF NOT EXISTS idx_tto_tool ON toolbox_tool_overrides(service_tool_id);
"""


def upgrade() -> None:
    connection = op.get_bind()
    for statement in TOOLBOX_TOOL_OVERRIDES_SQL.split(";"):
        sql = statement.strip()
        if sql:
            connection.exec_driver_sql(sql)


def downgrade() -> None:
    # Personal gateway migrations are forward-only for now. Keep downgrade
    # intentionally empty to avoid accidental local data loss.
    pass
