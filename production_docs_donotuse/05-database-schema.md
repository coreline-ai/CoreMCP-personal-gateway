# CoreMCP Database Schema

문서 버전: v0.1  
DB: PostgreSQL 15+

---

## 1. 설계 원칙

1. MVP부터 workspace 확장 가능성을 반영한다.
2. 모든 주요 리소스는 UUID 또는 prefixed id를 사용한다.
3. credential 원문은 저장하지 않는다.
4. 삭제는 기본 soft delete를 사용한다.
5. audit/invocation은 append-only에 가깝게 관리한다.
6. tool schema는 canonical JSON + schema_hash로 변경 추적한다.

---

## 2. ID Prefix 권장

| Entity | Prefix |
|---|---|
| user | `usr_` |
| workspace | `wks_` |
| service | `svc_` |
| tool | `tool_` |
| toolbox | `tbx_` |
| toolbox item | `tbi_` |
| credential | `cred_` |
| external connection | `ext_` |
| invocation | `inv_` |
| audit | `aud_` |

DB 내부는 UUID, API 노출은 prefixed id를 사용할 수 있다.

---

## 3. Enum Types

```sql
CREATE TYPE service_visibility AS ENUM (
  'private',
  'unlisted',
  'public',
  'review_pending',
  'rejected'
);

CREATE TYPE service_status AS ENUM (
  'draft',
  'validating',
  'active',
  'error',
  'disabled',
  'deleted'
);

CREATE TYPE credential_type AS ENUM (
  'none',
  'bearer_token',
  'api_key_header',
  'api_key_query',
  'oauth_delegated',
  'service_account'
);

CREATE TYPE connection_status AS ENUM (
  'not_connected',
  'pending_oauth',
  'oauth_consent_required',
  'connected',
  'expired',
  'revoked',
  'error'
);

CREATE TYPE external_client_type AS ENUM (
  'claude_code',
  'claude',
  'claude_desktop',
  'chatgpt',
  'openclaw',
  'cursor',
  'windsurf',
  'other'
);

CREATE TYPE invocation_status AS ENUM (
  'success',
  'error',
  'timeout',
  'cancelled',
  'policy_denied',
  'auth_failed',
  'rate_limited',
  'partial_success'
);

CREATE TYPE workspace_plan AS ENUM ('free','pro','team','enterprise');

CREATE TYPE tool_risk_level AS ENUM ('unknown','low','medium','high','critical');
```

---

## 4. Core Tables

### 4.1 users

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,
  name TEXT,
  avatar_url TEXT,
  provider TEXT NOT NULL DEFAULT 'email',
  provider_subject TEXT,
  default_workspace_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);

CREATE INDEX idx_users_provider_subject ON users(provider, provider_subject);

ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE users ADD COLUMN mfa_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN last_login_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN locked_until TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN locale TEXT NOT NULL DEFAULT 'en';
```

### 4.2 workspaces

```sql
CREATE TABLE workspaces (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  owner_user_id UUID NOT NULL REFERENCES users(id),
  plan TEXT NOT NULL DEFAULT 'free',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);

ALTER TABLE users
  ADD CONSTRAINT fk_users_default_workspace
  FOREIGN KEY (default_workspace_id) REFERENCES workspaces(id);

ALTER TABLE workspaces ADD COLUMN region TEXT NOT NULL DEFAULT 'global';
ALTER TABLE workspaces ALTER COLUMN plan TYPE workspace_plan USING plan::workspace_plan;
```

### 4.3 workspace_members

```sql
CREATE TABLE workspace_members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  user_id UUID NOT NULL REFERENCES users(id),
  role TEXT NOT NULL DEFAULT 'member',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(workspace_id, user_id)
);
```

---

## 5. MCP Registry Tables

### 5.1 mcp_services

```sql
CREATE TABLE mcp_services (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID REFERENCES workspaces(id),
  owner_user_id UUID NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  description TEXT,
  endpoint_url TEXT NOT NULL,
  canonical_resource_uri TEXT,
  auth_type credential_type NOT NULL DEFAULT 'none',
  visibility service_visibility NOT NULL DEFAULT 'private',
  status service_status NOT NULL DEFAULT 'draft',
  category TEXT,
  logo_url TEXT,
  homepage_url TEXT,
  documentation_url TEXT,
  terms_url TEXT,
  privacy_url TEXT,
  risk_level TEXT NOT NULL DEFAULT 'unknown',
  validation_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_validated_at TIMESTAMPTZ,
  last_tool_refresh_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ,
  UNIQUE(owner_user_id, slug)
);

CREATE INDEX idx_mcp_services_owner ON mcp_services(owner_user_id);
CREATE INDEX idx_mcp_services_visibility_status ON mcp_services(visibility, status);
CREATE INDEX idx_mcp_services_workspace ON mcp_services(workspace_id);
```

### 5.2 service_tools

```sql
CREATE TABLE service_tools (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  original_name TEXT NOT NULL,
  exposed_name TEXT NOT NULL,
  title TEXT,
  description TEXT,
  input_schema_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  output_schema_json JSONB,
  schema_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  risk_level TEXT NOT NULL DEFAULT 'unknown',
  metadata_scan JSONB NOT NULL DEFAULT '{}'::jsonb,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  cached_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  disabled_at TIMESTAMPTZ,
  UNIQUE(service_id, original_name),
  UNIQUE(service_id, exposed_name)
);

CREATE INDEX idx_service_tools_service ON service_tools(service_id);
CREATE INDEX idx_service_tools_exposed ON service_tools(exposed_name);
CREATE INDEX idx_service_tools_schema_hash ON service_tools(schema_hash);

ALTER TABLE service_tools ADD COLUMN annotations JSONB NOT NULL DEFAULT '{}';
ALTER TABLE service_tools ADD COLUMN structured_output_schema_json JSONB;
```

annotations JSONB 예시:

```json
{
  "title": "Create Issue",
  "destructiveHint": false,
  "readOnlyHint": false,
  "idempotentHint": false,
  "openWorldHint": true
}
```

### 5.3 service_validation_runs

```sql
CREATE TABLE service_validation_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  triggered_by_user_id UUID REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'queued',
  stages JSONB NOT NULL DEFAULT '[]'::jsonb,
  tools_found INTEGER NOT NULL DEFAULT 0,
  errors JSONB NOT NULL DEFAULT '[]'::jsonb,
  warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_validation_runs_service_created ON service_validation_runs(service_id, created_at DESC);
```

### 5.4 tool_aliases (ADR-016)

```sql
-- 5.4 tool_aliases
CREATE TABLE tool_aliases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_tool_id UUID NOT NULL REFERENCES service_tools(id) ON DELETE CASCADE,
  exposed_name TEXT NOT NULL UNIQUE,
  is_primary BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deprecated_at TIMESTAMPTZ
);

CREATE INDEX idx_tool_aliases_tool ON tool_aliases(service_tool_id);
CREATE UNIQUE INDEX uq_primary_alias_per_tool
  ON tool_aliases(service_tool_id)
  WHERE is_primary = true AND deprecated_at IS NULL;
```

service slug 변경 시 기존 alias는 `is_primary=false, deprecated_at=now()`로 마크하고 새 alias 추가.

---

## 6. Toolbox Tables

### 6.1 toolboxes

```sql
CREATE TABLE toolboxes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID REFERENCES workspaces(id),
  owner_user_id UUID NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  slug TEXT,
  is_default BOOLEAN NOT NULL DEFAULT false,
  visibility TEXT NOT NULL DEFAULT 'private',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX uq_default_toolbox_per_user
  ON toolboxes(owner_user_id)
  WHERE is_default = true AND deleted_at IS NULL;

CREATE INDEX idx_toolboxes_owner ON toolboxes(owner_user_id);
```

### 6.2 toolbox_items

```sql
CREATE TABLE toolbox_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  toolbox_id UUID NOT NULL REFERENCES toolboxes(id) ON DELETE CASCADE,
  service_id UUID NOT NULL REFERENCES mcp_services(id),
  enabled BOOLEAN NOT NULL DEFAULT true,
  added_by_user_id UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ,
  UNIQUE(toolbox_id, service_id)
);

CREATE INDEX idx_toolbox_items_toolbox ON toolbox_items(toolbox_id);
CREATE INDEX idx_toolbox_items_service ON toolbox_items(service_id);
```

---

## 7. Credential Tables

### 7.1 user_service_connections

```sql
CREATE TABLE user_service_connections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  service_id UUID NOT NULL REFERENCES mcp_services(id),
  status connection_status NOT NULL DEFAULT 'not_connected',
  credential_type credential_type NOT NULL DEFAULT 'none',
  secret_ref TEXT,
  scopes TEXT[] NOT NULL DEFAULT '{}',
  expires_at TIMESTAMPTZ,
  refreshed_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  last_error_code TEXT,
  last_error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, service_id)
);

CREATE INDEX idx_user_service_connections_user ON user_service_connections(user_id);
CREATE INDEX idx_user_service_connections_service ON user_service_connections(service_id);
```

### 7.2 service_credentials

서비스 owner가 등록하는 service-level credential. private service의 경우 MVP에서 이 테이블을 사용할 수 있다.

```sql
CREATE TABLE service_credentials (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  owner_user_id UUID NOT NULL REFERENCES users(id),
  credential_type credential_type NOT NULL,
  secret_ref TEXT NOT NULL,
  header_name TEXT,
  scopes TEXT[] NOT NULL DEFAULT '{}',
  expires_at TIMESTAMPTZ,
  rotated_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_service_credentials_service ON service_credentials(service_id);
```

---

## 8. External Client Tables

### 8.1 external_connections

```sql
CREATE TABLE external_connections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  toolbox_id UUID REFERENCES toolboxes(id),
  client_type external_client_type NOT NULL,
  client_name TEXT,
  oauth_client_id TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  scopes TEXT[] NOT NULL DEFAULT '{}',
  last_used_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_external_connections_user ON external_connections(user_id);
CREATE INDEX idx_external_connections_client ON external_connections(client_type, status);

ALTER TABLE external_connections ADD COLUMN protocol_version TEXT;
ALTER TABLE external_connections ADD COLUMN refresh_token_hash TEXT;
ALTER TABLE external_connections ADD COLUMN client_quirks JSONB NOT NULL DEFAULT '{}';
ALTER TABLE external_connections ADD COLUMN device_fingerprint TEXT;
ALTER TABLE external_connections ADD COLUMN created_ip INET;
ALTER TABLE external_connections ADD COLUMN created_user_agent TEXT;
```

### 8.2 connection_tokens

```sql
CREATE TABLE connection_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  toolbox_id UUID REFERENCES toolboxes(id),
  token_hash TEXT NOT NULL UNIQUE,
  client_type external_client_type NOT NULL,
  requested_scopes TEXT[] NOT NULL DEFAULT '{}',
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_connection_tokens_user ON connection_tokens(user_id);
CREATE INDEX idx_connection_tokens_expires ON connection_tokens(expires_at);

ALTER TABLE connection_tokens ADD COLUMN created_ip INET;
ALTER TABLE connection_tokens ADD COLUMN created_user_agent TEXT;
ALTER TABLE connection_tokens ADD COLUMN used_ip INET;
ALTER TABLE connection_tokens ADD COLUMN used_user_agent TEXT;
```

---

## 9. MCP Session Tables

### 9.1 mcp_sessions

```sql
CREATE TABLE mcp_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id TEXT NOT NULL UNIQUE,
  user_id UUID NOT NULL REFERENCES users(id),
  external_connection_id UUID REFERENCES external_connections(id),
  client_name TEXT,
  client_version TEXT,
  protocol_version TEXT,
  capabilities_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'active',
  initialized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  terminated_at TIMESTAMPTZ
);

CREATE INDEX idx_mcp_sessions_user ON mcp_sessions(user_id);
CREATE INDEX idx_mcp_sessions_last_seen ON mcp_sessions(last_seen_at);
```

---

## 10. Logs Tables

### 10.1 tool_invocations

```sql
-- ADR 권장: monthly RANGE partitioning by created_at
-- production은 pg_partman 자동화 권장
-- 예시: CREATE TABLE tool_invocations (...) PARTITION BY RANGE (created_at);
--       CREATE TABLE tool_invocations_2026_05 PARTITION OF tool_invocations
--         FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE tool_invocations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id TEXT NOT NULL,
  user_id UUID NOT NULL REFERENCES users(id),
  workspace_id UUID REFERENCES workspaces(id),
  external_connection_id UUID REFERENCES external_connections(id),
  toolbox_id UUID REFERENCES toolboxes(id),
  service_id UUID REFERENCES mcp_services(id),
  service_tool_id UUID REFERENCES service_tools(id),
  exposed_tool_name TEXT NOT NULL,
  downstream_tool_name TEXT,
  status invocation_status NOT NULL,
  latency_ms INTEGER,
  downstream_latency_ms INTEGER,
  error_code TEXT,
  error_message TEXT,
  input_size_bytes INTEGER,
  output_size_bytes INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tool_invocations_user_created ON tool_invocations(user_id, created_at DESC);
CREATE INDEX idx_tool_invocations_service_created ON tool_invocations(service_id, created_at DESC);
CREATE INDEX idx_tool_invocations_request ON tool_invocations(request_id);

ALTER TABLE tool_invocations ADD COLUMN client_ip INET;
ALTER TABLE tool_invocations ADD COLUMN user_agent TEXT;
ALTER TABLE tool_invocations ADD COLUMN protocol_version TEXT;
ALTER TABLE tool_invocations ADD COLUMN idempotency_key TEXT;
```

### 10.2 audit_logs

```sql
-- ADR 권장: monthly RANGE partitioning by created_at
-- production은 pg_partman 자동화 권장
-- 예시: CREATE TABLE audit_logs (...) PARTITION BY RANGE (created_at);
--       CREATE TABLE audit_logs_2026_05 PARTITION OF audit_logs
--         FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE audit_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id TEXT,
  actor_user_id UUID REFERENCES users(id),
  workspace_id UUID REFERENCES workspaces(id),
  action TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id UUID,
  ip INET,
  user_agent TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_logs_actor_created ON audit_logs(actor_user_id, created_at DESC);
CREATE INDEX idx_audit_logs_resource ON audit_logs(resource_type, resource_id);

COMMENT ON COLUMN audit_logs.actor_user_id IS
  'NULL after right-to-erasure anonymization';
```

---

## 11. OAuth Tables

외부 provider를 쓰는 경우 최소화 가능하지만, CoreMCP 자체 authorization server를 구현하면 필요하다.

```sql
CREATE TABLE oauth_clients (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id TEXT NOT NULL UNIQUE,
  client_secret_hash TEXT,
  client_name TEXT NOT NULL,
  redirect_uris TEXT[] NOT NULL DEFAULT '{}',
  grant_types TEXT[] NOT NULL DEFAULT '{}',
  response_types TEXT[] NOT NULL DEFAULT '{}',
  token_endpoint_auth_method TEXT,
  created_by_user_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ
);

CREATE TABLE user_consents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  client_id TEXT NOT NULL,
  scopes TEXT[] NOT NULL DEFAULT '{}',
  redirect_uri TEXT,
  approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ,
  UNIQUE(user_id, client_id)
);

CREATE TABLE oauth_authorization_codes (
  code_hash TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id),
  client_id TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  resource TEXT,
  scopes TEXT[] NOT NULL,
  code_challenge TEXT NOT NULL,
  code_challenge_method TEXT NOT NULL DEFAULT 'S256',
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE oauth_access_tokens (
  jti TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id),
  client_id TEXT NOT NULL,
  external_connection_id UUID REFERENCES external_connections(id),
  audience TEXT NOT NULL,
  scopes TEXT[] NOT NULL,
  issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ
);

CREATE INDEX idx_oauth_access_tokens_user ON oauth_access_tokens(user_id);
CREATE INDEX idx_oauth_access_tokens_expires ON oauth_access_tokens(expires_at);

CREATE TABLE oauth_refresh_tokens (
  token_hash TEXT PRIMARY KEY,
  parent_jti TEXT,
  user_id UUID NOT NULL REFERENCES users(id),
  client_id TEXT NOT NULL,
  external_connection_id UUID REFERENCES external_connections(id),
  scopes TEXT[] NOT NULL,
  issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ
);

CREATE TABLE jwks_keys (
  kid TEXT PRIMARY KEY,
  algorithm TEXT NOT NULL DEFAULT 'RS256',
  public_key_pem TEXT NOT NULL,
  private_key_secret_ref TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  rotated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 11.5 api_keys (power user / CI/CD)

```sql
-- 11.5 api_keys (power user / CI/CD)
CREATE TABLE api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  prefix TEXT NOT NULL,
  scopes TEXT[] NOT NULL DEFAULT '{}',
  last_used_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_api_keys_user ON api_keys(user_id);
CREATE INDEX idx_api_keys_prefix ON api_keys(prefix);
```

---

## 12. Migration Order

1. enums
2. users
3. workspaces
4. workspace_members
5. mcp_services
6. service_tools
7. service_validation_runs
8. toolboxes
9. toolbox_items
10. credentials
11. external_connections
12. connection_tokens
13. mcp_sessions
14. logs
15. oauth tables

---

## 13. Data Retention

| Data | Retention |
|---|---|
| tool_invocations metadata | 90 days free, 1 year paid |
| audit_logs | 1 year minimum |
| raw request/response | default not stored |
| validation reports | latest 20 per service |
| expired connection tokens | delete after 7 days |
| revoked credentials | keep metadata only |

---

## 14. Privacy/Security Notes

- secret_ref는 secret value가 아니다.
- credential 원문은 audit_logs/tool_invocations에 저장하지 않는다.
- tool arguments는 기본 저장하지 않는다.
- tool output은 기본 저장하지 않는다.
- public marketplace service의 metadata는 공개될 수 있으므로 민감정보를 포함하면 안 된다.
- right-to-erasure: users.deleted_at + 30d grace 후 hard-delete + audit_logs는 actor_user_id NULL로 anonymize
- KMS DEK rotation: on-demand, KEK rotation: yearly
- 모든 owner_user_id 컬럼 테이블에 RLS 적용 (위 §15)

---

## 15. Row-Level Security (ADR-017)

```sql
-- 15. Row-Level Security
-- 모든 user-owned 테이블에 RLS 활성화.
-- 애플리케이션은 connection 직후 SET LOCAL app.user_id = '<uuid>'.

ALTER TABLE mcp_services ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_mcp_services_owner ON mcp_services
  USING (owner_user_id = current_setting('app.user_id', true)::uuid);

ALTER TABLE toolboxes ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_toolboxes_owner ON toolboxes
  USING (owner_user_id = current_setting('app.user_id', true)::uuid);

ALTER TABLE toolbox_items ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_toolbox_items_owner ON toolbox_items
  USING (toolbox_id IN (
    SELECT id FROM toolboxes WHERE owner_user_id = current_setting('app.user_id', true)::uuid
  ));

ALTER TABLE user_service_connections ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_usc_owner ON user_service_connections
  USING (user_id = current_setting('app.user_id', true)::uuid);

ALTER TABLE external_connections ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_ext_owner ON external_connections
  USING (user_id = current_setting('app.user_id', true)::uuid);

ALTER TABLE tool_invocations ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_inv_owner ON tool_invocations
  USING (user_id = current_setting('app.user_id', true)::uuid);

-- BYPASSRLS는 superuser와 admin role에만 부여.
-- worker는 별도 service role로 SECURITY DEFINER 함수 통해 접근.
```
