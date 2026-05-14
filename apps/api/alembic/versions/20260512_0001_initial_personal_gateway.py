from __future__ import annotations

from alembic import op

revision = "20260512_0001"
down_revision = None
branch_labels = None
depends_on = None


INITIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE DEFAULT 'me@local',
  name TEXT NOT NULL DEFAULT 'Personal',
  avatar_url TEXT,
  locale TEXT NOT NULL DEFAULT 'ko',
  is_active INTEGER NOT NULL DEFAULT 1,
  bootstrap_completed_at TIMESTAMP,
  last_login_at TIMESTAMP,
  workspace_id TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS toolboxes (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  workspace_id TEXT,
  name TEXT NOT NULL,
  slug TEXT,
  is_default INTEGER NOT NULL DEFAULT 0,
  visibility TEXT NOT NULL DEFAULT 'private',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_default_toolbox_per_user ON toolboxes(owner_user_id) WHERE is_default = 1 AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_toolboxes_owner ON toolboxes(owner_user_id);

CREATE TABLE IF NOT EXISTS mcp_services (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  workspace_id TEXT,
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  description TEXT,
  endpoint_url TEXT NOT NULL,
  transport_type TEXT NOT NULL DEFAULT 'http' CHECK (transport_type IN ('http', 'stdio')),
  auth_type TEXT NOT NULL DEFAULT 'none' CHECK (auth_type IN ('none', 'bearer_token', 'api_key_header', 'oauth_delegated', 'service_account')),
  visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'unlisted', 'public', 'review_pending', 'rejected')),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'validating', 'active', 'error', 'disabled', 'auth_required', 'deleted')),
  category TEXT,
  logo_url TEXT,
  homepage_url TEXT,
  documentation_url TEXT,
  stdio_command TEXT,
  stdio_args TEXT NOT NULL DEFAULT '[]',
  stdio_env TEXT NOT NULL DEFAULT '{}',
  stdio_cwd TEXT,
  stdio_idle_timeout_seconds INTEGER NOT NULL DEFAULT 300,
  last_health_check_at TIMESTAMP,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  circuit_open_until REAL,
  last_stdio_started_at REAL,
  last_stdio_used_at REAL,
  stdio_restart_count INTEGER NOT NULL DEFAULT 0,
  last_stdio_exit_code INTEGER,
  last_stdio_error TEXT,
  last_stdio_stderr_tail TEXT,
  risk_level TEXT NOT NULL DEFAULT 'unknown' CHECK (risk_level IN ('unknown', 'low', 'medium', 'high', 'critical')),
  validation_summary TEXT NOT NULL DEFAULT '{}',
  last_validated_at TIMESTAMP,
  last_tool_refresh_at TIMESTAMP,
  protocol_version TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mcp_services_owner_slug_active ON mcp_services(owner_user_id, slug) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_mcp_services_owner ON mcp_services(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_services_status ON mcp_services(status);
CREATE INDEX IF NOT EXISTS idx_mcp_services_transport ON mcp_services(transport_type, status);
CREATE INDEX IF NOT EXISTS idx_mcp_services_circuit ON mcp_services(circuit_open_until) WHERE circuit_open_until IS NOT NULL;

CREATE TABLE IF NOT EXISTS service_tools (
  id TEXT PRIMARY KEY,
  service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  original_name TEXT NOT NULL,
  title TEXT,
  description TEXT,
  input_schema_json TEXT NOT NULL DEFAULT '{}',
  output_schema_json TEXT,
  structured_output_schema_json TEXT,
  annotations TEXT NOT NULL DEFAULT '{}',
  icons_json TEXT NOT NULL DEFAULT '[]',
  schema_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'deprecated')),
  risk_level TEXT NOT NULL DEFAULT 'unknown',
  metadata_scan TEXT NOT NULL DEFAULT '{}',
  first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  cached_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  disabled_at TIMESTAMP,
  UNIQUE(service_id, original_name)
);
CREATE INDEX IF NOT EXISTS idx_service_tools_service ON service_tools(service_id);
CREATE INDEX IF NOT EXISTS idx_service_tools_hash ON service_tools(schema_hash);

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
);
CREATE INDEX IF NOT EXISTS idx_service_resources_service ON service_resources(service_id, status);
CREATE INDEX IF NOT EXISTS idx_service_resources_uri ON service_resources(uri);

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
);
CREATE INDEX IF NOT EXISTS idx_service_resource_templates_service ON service_resource_templates(service_id, status);

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
);
CREATE INDEX IF NOT EXISTS idx_service_prompts_service ON service_prompts(service_id, status);

CREATE TABLE IF NOT EXISTS tool_aliases (
  id TEXT PRIMARY KEY,
  service_tool_id TEXT NOT NULL REFERENCES service_tools(id) ON DELETE CASCADE,
  exposed_name TEXT NOT NULL,
  is_primary INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deprecated_at TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_aliases_exposed_name_active ON tool_aliases(exposed_name) WHERE deprecated_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tool_aliases_tool ON tool_aliases(service_tool_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_primary_alias_per_tool ON tool_aliases(service_tool_id) WHERE is_primary = 1 AND deprecated_at IS NULL;

CREATE TABLE IF NOT EXISTS service_validation_runs (
  id TEXT PRIMARY KEY,
  service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  triggered_by TEXT NOT NULL DEFAULT 'user' CHECK (triggered_by IN ('user', 'system_ttl', 'system_event', 'manual_refresh')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'success', 'failed')),
  stages TEXT NOT NULL DEFAULT '[]',
  tools_found INTEGER NOT NULL DEFAULT 0,
  errors TEXT NOT NULL DEFAULT '[]',
  warnings TEXT NOT NULL DEFAULT '[]',
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_val_runs_service ON service_validation_runs(service_id, created_at DESC);

CREATE TABLE IF NOT EXISTS toolbox_items (
  id TEXT PRIMARY KEY,
  toolbox_id TEXT NOT NULL REFERENCES toolboxes(id) ON DELETE CASCADE,
  service_id TEXT NOT NULL REFERENCES mcp_services(id),
  enabled INTEGER NOT NULL DEFAULT 1,
  added_by_user_id TEXT NOT NULL REFERENCES users(id),
  position INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_toolbox_items_active ON toolbox_items(toolbox_id, service_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tbi_toolbox ON toolbox_items(toolbox_id);

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

CREATE TABLE IF NOT EXISTS service_credentials (
  id TEXT PRIMARY KEY,
  service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  credential_type TEXT NOT NULL CHECK (credential_type IN ('none', 'bearer_token', 'api_key_header', 'oauth_delegated', 'service_account')),
  secret_ref TEXT NOT NULL,
  header_name TEXT,
  masked_value TEXT,
  scopes TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'connected' CHECK (status IN ('not_connected', 'connected', 'expired', 'revoked', 'error')),
  last_error_code TEXT,
  last_error_message TEXT,
  expires_at TIMESTAMP,
  rotated_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_service_credentials_service_active ON service_credentials(service_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS external_connections (
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
);
CREATE INDEX IF NOT EXISTS idx_ext_user ON external_connections(user_id);
CREATE INDEX IF NOT EXISTS idx_ext_client ON external_connections(client_type, status);

CREATE TABLE IF NOT EXISTS connection_tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  toolbox_id TEXT REFERENCES toolboxes(id),
  token_hash TEXT NOT NULL UNIQUE,
  client_type TEXT NOT NULL,
  requested_scopes TEXT NOT NULL DEFAULT '[]',
  created_ip TEXT,
  created_user_agent TEXT,
  used_ip TEXT,
  used_user_agent TEXT,
  expires_at TIMESTAMP NOT NULL,
  used_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_otk_expires ON connection_tokens(expires_at);

CREATE TABLE IF NOT EXISTS personal_access_tokens (
  id TEXT PRIMARY KEY,
  external_connection_id TEXT REFERENCES external_connections(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id),
  token_hash TEXT NOT NULL,
  token_prefix TEXT NOT NULL,
  scopes TEXT NOT NULL DEFAULT '[]',
  protocol_version TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  last_used_at TIMESTAMP,
  expires_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT chk_pat_revoked_consistency CHECK (
    (status = 'revoked' AND revoked_at IS NOT NULL) OR
    (status = 'active' AND revoked_at IS NULL)
  )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pat_hash_active ON personal_access_tokens(token_hash) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pat_external_conn ON personal_access_tokens(external_connection_id);
CREATE INDEX IF NOT EXISTS idx_pat_user ON personal_access_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_pat_status_revoked ON personal_access_tokens(status, revoked_at);
CREATE INDEX IF NOT EXISTS idx_pat_expires ON personal_access_tokens(expires_at) WHERE expires_at IS NOT NULL AND revoked_at IS NULL;

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

CREATE TABLE IF NOT EXISTS mcp_sessions (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL REFERENCES users(id),
  external_connection_id TEXT REFERENCES external_connections(id),
  client_name TEXT,
  client_version TEXT,
  protocol_version TEXT,
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  initialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP,
  terminated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sess_user ON mcp_sessions(user_id);

CREATE TABLE IF NOT EXISTS tool_invocations (
  id TEXT PRIMARY KEY,
  request_id TEXT,
  user_id TEXT NOT NULL REFERENCES users(id),
  external_connection_id TEXT REFERENCES external_connections(id),
  toolbox_id TEXT REFERENCES toolboxes(id),
  service_id TEXT REFERENCES mcp_services(id),
  service_tool_id TEXT REFERENCES service_tools(id),
  session_id TEXT,
  method TEXT,
  tool_name TEXT,
  exposed_tool_name TEXT,
  downstream_tool_name TEXT,
  status TEXT NOT NULL CHECK (status IN ('success', 'error', 'timeout', 'cancelled', 'policy_denied', 'auth_failed', 'rate_limited')),
  latency_ms INTEGER,
  downstream_latency_ms INTEGER,
  error_code TEXT,
  error_message TEXT,
  input_size_bytes INTEGER,
  output_size_bytes INTEGER,
  protocol_version TEXT,
  idempotency_key TEXT,
  client_ip TEXT,
  user_agent TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_inv_user_created ON tool_invocations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_service_created ON tool_invocations(service_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_request ON tool_invocations(request_id);
CREATE INDEX IF NOT EXISTS idx_inv_idempotency ON tool_invocations(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS audit_logs (
  id TEXT PRIMARY KEY,
  request_id TEXT,
  actor_user_id TEXT REFERENCES users(id),
  action TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT,
  ip TEXT,
  user_agent TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aud_actor_created ON audit_logs(actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_aud_resource ON audit_logs(resource_type, resource_id);

CREATE TABLE IF NOT EXISTS debug_traces (
  id TEXT PRIMARY KEY,
  invocation_id TEXT NOT NULL REFERENCES tool_invocations(id) ON DELETE CASCADE,
  arguments_json TEXT,
  result_json TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dt_expires ON debug_traces(expires_at);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('service_validation', 'service_refresh', 'credential_rotate', 'export', 'cleanup')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'success', 'failed', 'cancelled')),
  progress REAL NOT NULL DEFAULT 0.0,
  payload TEXT NOT NULL DEFAULT '{}',
  result TEXT,
  error TEXT,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def upgrade() -> None:
    connection = op.get_bind()
    for statement in INITIAL_SCHEMA.split(";"):
        sql = statement.strip()
        if sql:
            connection.exec_driver_sql(sql)


def downgrade() -> None:
    # Personal gateway migrations are forward-only for now. Keep downgrade
    # intentionally empty to avoid accidental local data loss.
    pass
