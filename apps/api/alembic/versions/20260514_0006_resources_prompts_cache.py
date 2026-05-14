from __future__ import annotations

from alembic import op

revision = "20260514_0006"
down_revision = "20260514_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return

    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS service_resources (
          id TEXT PRIMARY KEY,
          service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
          uri TEXT NOT NULL,
          name TEXT,
          title TEXT,
          description TEXT,
          mime_type TEXT,
          annotations TEXT NOT NULL DEFAULT '{}',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'deprecated')),
          first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          cached_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          disabled_at TIMESTAMP,
          UNIQUE(service_id, uri)
        )
        """
    )
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_service_resources_service ON service_resources(service_id, status)")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_service_resources_uri ON service_resources(uri)")

    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS service_resource_templates (
          id TEXT PRIMARY KEY,
          service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
          uri_template TEXT NOT NULL,
          name TEXT,
          title TEXT,
          description TEXT,
          mime_type TEXT,
          annotations TEXT NOT NULL DEFAULT '{}',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'deprecated')),
          first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          cached_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          disabled_at TIMESTAMP,
          UNIQUE(service_id, uri_template)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_service_resource_templates_service ON service_resource_templates(service_id, status)"
    )

    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS service_prompts (
          id TEXT PRIMARY KEY,
          service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          title TEXT,
          description TEXT,
          arguments_json TEXT NOT NULL DEFAULT '[]',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'deprecated')),
          first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          cached_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          disabled_at TIMESTAMP,
          UNIQUE(service_id, name)
        )
        """
    )
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_service_prompts_service ON service_prompts(service_id, status)")


def downgrade() -> None:
    # Personal gateway migrations are forward-only for now. Keep downgrade
    # intentionally empty to avoid accidental local data loss.
    pass
