from __future__ import annotations

from alembic import op

revision = "20260514_0004"
down_revision = "20260513_0003"
branch_labels = None
depends_on = None


OAUTH_PERSISTENCE_SQL = """
CREATE TABLE IF NOT EXISTS oauth_signing_keys (
  kid TEXT PRIMARY KEY,
  private_key_pem TEXT NOT NULL,
  alg TEXT NOT NULL DEFAULT 'RS256',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  retired_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oauth_signing_keys_status ON oauth_signing_keys(status, created_at DESC);

CREATE TABLE IF NOT EXISTS oauth_clients (
  client_id TEXT PRIMARY KEY,
  client_name TEXT NOT NULL,
  redirect_uris TEXT NOT NULL DEFAULT '[]',
  scope TEXT NOT NULL,
  grant_types TEXT NOT NULL DEFAULT '[]',
  response_types TEXT NOT NULL DEFAULT '[]',
  token_endpoint_auth_method TEXT NOT NULL DEFAULT 'none',
  source TEXT NOT NULL DEFAULT 'dcr' CHECK (source IN ('dcr', 'cimd')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oauth_clients_source ON oauth_clients(source);

CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
  id TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL UNIQUE,
  client_id TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  redirect_uri TEXT NOT NULL,
  resource TEXT NOT NULL,
  scope TEXT NOT NULL,
  code_challenge TEXT NOT NULL,
  expires_at REAL NOT NULL,
  used_at REAL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oauth_codes_client ON oauth_authorization_codes(client_id);
CREATE INDEX IF NOT EXISTS idx_oauth_codes_expires ON oauth_authorization_codes(expires_at);

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  client_id TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  external_connection_id TEXT NOT NULL REFERENCES external_connections(id) ON DELETE CASCADE,
  resource TEXT NOT NULL,
  scope TEXT NOT NULL,
  expires_at REAL NOT NULL,
  family_id TEXT NOT NULL,
  parent_hash TEXT,
  issued_at REAL,
  used_at REAL,
  revoked_at REAL,
  revoked_reason TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_family ON oauth_refresh_tokens(family_id);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_client ON oauth_refresh_tokens(client_id);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_expires ON oauth_refresh_tokens(expires_at);

CREATE TABLE IF NOT EXISTS oauth_revoked_access_tokens (
  jti TEXT PRIMARY KEY,
  expires_at REAL NOT NULL,
  revoked_at REAL NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oauth_revoked_access_expires ON oauth_revoked_access_tokens(expires_at);

CREATE TABLE IF NOT EXISTS oauth_cimd_cache (
  client_id TEXT PRIMARY KEY REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  cached_until REAL NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oauth_cimd_cache_until ON oauth_cimd_cache(cached_until);
"""


def upgrade() -> None:
    connection = op.get_bind()
    for statement in OAUTH_PERSISTENCE_SQL.split(";"):
        sql = statement.strip()
        if sql:
            connection.exec_driver_sql(sql)


def downgrade() -> None:
    # Personal gateway migrations are forward-only for now. Keep downgrade
    # intentionally empty to avoid accidental local data loss.
    pass
