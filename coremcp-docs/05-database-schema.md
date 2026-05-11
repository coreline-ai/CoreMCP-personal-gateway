# CoreMCP Database Schema (Personal)

문서 버전: v1.0
대상 DB: SQLite 3.35+ (기본) 또는 PostgreSQL 15+ (옵션)

---

## 1. 설계 원칙

1. SQLite와 PostgreSQL 둘 다 지원되는 DDL을 우선.
2. 단일 사용자라 `workspaces`, `workspace_members`, RLS, 파티셔닝은 제외.
3. credential 평문 저장 금지 — `secret_ref`로 vault 참조.
4. soft-delete 기본. hard-delete는 명시적.
5. tool schema는 canonical JSON + schema_hash로 변경 추적.
6. 향후 SaaS 전환을 위해 `workspace_id` 컬럼은 nullable로 선반영 (PostgreSQL은 UUID, SQLite는 TEXT).
7. audit / invocation은 append-only.

## 2. ID 표기

API 노출 시 prefix:

| Entity | Prefix |
|---|---|
| user | usr_ |
| service | svc_ |
| tool | tool_ |
| tool_alias | tali_ |
| toolbox | tbx_ |
| toolbox_item | tbi_ |
| credential ref | cred_ |
| external_connection | ext_ |
| connection_token | otk_ |
| session | sess_ |
| invocation | inv_ |
| audit | aud_ |
| job | job_ |
| validation_run | val_ |

DB 내부는 UUID(Postgres) 또는 TEXT(SQLite, lower(hex(randomblob(16)))).

## 3. SQLite vs PostgreSQL 타입 매핑

| 의도 | SQLite | PostgreSQL |
|---|---|---|
| UUID PK | `TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16))))` | `UUID PRIMARY KEY DEFAULT gen_random_uuid()` |
| Timestamp | `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` (UTC) | `TIMESTAMPTZ NOT NULL DEFAULT now()` |
| JSON | `JSON` (3.38+) 또는 `TEXT` | `JSONB` |
| INET | `TEXT` | `INET` |
| TEXT[] | `JSON` (`["a","b"]`) | `TEXT[]` |
| ENUM | `TEXT CHECK (col IN (...))` | `CREATE TYPE ... AS ENUM (...)` |

이하 DDL은 SQLite 우선 표기.

## 4. ENUM-like CHECK

```sql
-- service_visibility (단일 사용자라 항상 'private', 컬럼은 미래용)
-- CHECK (visibility IN ('private', 'unlisted', 'public', 'review_pending', 'rejected'))

-- service_status
-- CHECK (status IN ('draft', 'validating', 'active', 'error', 'disabled', 'auth_required', 'deleted'))

-- credential_type
-- CHECK (credential_type IN ('none', 'bearer_token', 'api_key_header', 'oauth_delegated', 'service_account'))

-- connection_status
-- CHECK (status IN ('not_connected', 'connected', 'expired', 'revoked', 'error'))

-- external_client_type
-- CHECK (client_type IN ('claude_code', 'claude', 'claude_desktop', 'chatgpt', 'openclaw', 'cursor', 'windsurf', 'other'))

-- invocation_status
-- CHECK (status IN ('success', 'error', 'timeout', 'cancelled', 'policy_denied', 'auth_failed', 'rate_limited'))

-- tool_risk_level
-- CHECK (risk_level IN ('unknown', 'low', 'medium', 'high', 'critical'))
```

PostgreSQL에서는 위 CHECK 대신 `CREATE TYPE ... AS ENUM` 사용.

## 5. Core Tables

### 5.1 users

단일 사용자. bootstrap 시 1행 자동 생성.

```sql
CREATE TABLE users (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  email TEXT NOT NULL UNIQUE DEFAULT 'me@local',
  name TEXT NOT NULL DEFAULT 'Personal',
  avatar_url TEXT,
  locale TEXT NOT NULL DEFAULT 'ko',
  is_active INTEGER NOT NULL DEFAULT 1,
  bootstrap_completed_at TIMESTAMP,
  last_login_at TIMESTAMP,
  workspace_id TEXT,                  -- 미래용 (현재 NULL)
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);
```

## 6. MCP Registry

### 6.1 mcp_services

```sql
CREATE TABLE mcp_services (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  workspace_id TEXT,                  -- 미래용
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  description TEXT,
  endpoint_url TEXT NOT NULL,
  auth_type TEXT NOT NULL DEFAULT 'none'
    CHECK (auth_type IN ('none', 'bearer_token', 'api_key_header', 'oauth_delegated', 'service_account')),
  visibility TEXT NOT NULL DEFAULT 'private'
    CHECK (visibility IN ('private', 'unlisted', 'public', 'review_pending', 'rejected')),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'validating', 'active', 'error', 'disabled', 'auth_required', 'deleted')),
  category TEXT,
  logo_url TEXT,
  homepage_url TEXT,
  documentation_url TEXT,
  risk_level TEXT NOT NULL DEFAULT 'unknown'
    CHECK (risk_level IN ('unknown', 'low', 'medium', 'high', 'critical')),
  validation_summary JSON NOT NULL DEFAULT '{}',
  last_validated_at TIMESTAMP,
  last_tool_refresh_at TIMESTAMP,
  protocol_version TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
  -- partial unique index 아래 정의 (deleted_at IS NULL)
);

-- soft-delete 호환 partial unique (ADR-035)
CREATE UNIQUE INDEX uq_mcp_services_owner_slug_active
  ON mcp_services(owner_user_id, slug)
  WHERE deleted_at IS NULL;

CREATE INDEX idx_mcp_services_owner ON mcp_services(owner_user_id);
CREATE INDEX idx_mcp_services_status ON mcp_services(status);
```

### 6.2 service_tools

```sql
CREATE TABLE service_tools (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  original_name TEXT NOT NULL,
  title TEXT,
  description TEXT,
  input_schema_json JSON NOT NULL DEFAULT '{}',
  output_schema_json JSON,
  structured_output_schema_json JSON,
  annotations JSON NOT NULL DEFAULT '{}',
  icons_json JSON NOT NULL DEFAULT '[]',           -- MCP 2025-11-25 top-level icons (ADR-029)
  -- 형식: [{src: string, mimeType: string, sizes?: string}, ...]
  -- src는 https URL 또는 data URI (image/png, image/webp, image/svg+xml)
  -- "url"이 아닌 "src" field 사용 (MCP spec align with HTML <img>)
  schema_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'disabled', 'deprecated')),
  risk_level TEXT NOT NULL DEFAULT 'unknown',
  metadata_scan JSON NOT NULL DEFAULT '{}',
  first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  cached_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  disabled_at TIMESTAMP,
  UNIQUE(service_id, original_name)  -- service_id가 새 row이면 자동 분리, soft-delete 영향 없음
);

CREATE INDEX idx_service_tools_service ON service_tools(service_id);
CREATE INDEX idx_service_tools_hash ON service_tools(schema_hash);

-- icons_json: MCP 2025-11-25 tool top-level field (ADR-029)
-- annotations 안에 두지 않음

-- icons object schema:
--   src       : required, https URL 또는 data URI
--   mimeType  : required, image/png | image/webp | image/svg+xml
--   sizes     : optional, "WIDTHxHEIGHT" 형식

-- 보안 정책 (P2 SVG XSS 방어):
-- 1. size cap: 32KB per row (애플리케이션 검증)
-- 2. content-type allowlist:
--    - image/png (권장)
--    - image/webp (권장)
--    - image/svg+xml (제한적 허용)
-- 3. SVG는 inline 렌더링 절대 금지 — <img> 태그로만 사용
-- 4. SVG가 외부 URL이면 SSRF guard 통과 후 캐시 (P1+)
-- 5. SVG가 data URI이면 디코드 후 다음 element 차단:
--    - <script>
--    - <foreignObject> (HTML injection)
--    - on* attribute (이벤트 핸들러)
--    - <use href="http..."> (외부 참조)
-- 6. 옵션 환경 변수 ICON_SVG_ENABLED=false 로 SVG 완전 차단 가능 (P0 권장)
-- 7. Web UI에서 CSP `img-src 'self' data:` 적용
```

### 6.3 tool_aliases (별도, slug rename 안정성)

```sql
CREATE TABLE tool_aliases (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  service_tool_id TEXT NOT NULL REFERENCES service_tools(id) ON DELETE CASCADE,
  exposed_name TEXT NOT NULL,           -- partial unique index 아래 정의 (deprecated_at IS NULL)
  is_primary INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deprecated_at TIMESTAMP
);

-- soft-delete (deprecated) 호환 partial unique (ADR-035)
CREATE UNIQUE INDEX uq_tool_aliases_exposed_name_active
  ON tool_aliases(exposed_name)
  WHERE deprecated_at IS NULL;

CREATE INDEX idx_tool_aliases_tool ON tool_aliases(service_tool_id);
CREATE UNIQUE INDEX uq_primary_alias_per_tool
  ON tool_aliases(service_tool_id)
  WHERE is_primary = 1 AND deprecated_at IS NULL;
```

### 6.4 service_validation_runs

```sql
CREATE TABLE service_validation_runs (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  triggered_by TEXT NOT NULL DEFAULT 'user'
    CHECK (triggered_by IN ('user', 'system_ttl', 'system_event', 'manual_refresh')),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'success', 'failed')),
  stages JSON NOT NULL DEFAULT '[]',
  tools_found INTEGER NOT NULL DEFAULT 0,
  errors JSON NOT NULL DEFAULT '[]',
  warnings JSON NOT NULL DEFAULT '[]',
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_val_runs_service ON service_validation_runs(service_id, created_at DESC);
```

## 7. Toolbox

### 7.1 toolboxes

```sql
CREATE TABLE toolboxes (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
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

CREATE UNIQUE INDEX uq_default_toolbox_per_user
  ON toolboxes(owner_user_id)
  WHERE is_default = 1 AND deleted_at IS NULL;

CREATE INDEX idx_toolboxes_owner ON toolboxes(owner_user_id);
```

### 7.2 toolbox_items

```sql
CREATE TABLE toolbox_items (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  toolbox_id TEXT NOT NULL REFERENCES toolboxes(id) ON DELETE CASCADE,
  service_id TEXT NOT NULL REFERENCES mcp_services(id),
  enabled INTEGER NOT NULL DEFAULT 1,
  added_by_user_id TEXT NOT NULL REFERENCES users(id),
  position INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
  -- partial unique index 아래 정의 (deleted_at IS NULL)
);

-- soft-delete 호환 partial unique (ADR-035)
CREATE UNIQUE INDEX uq_toolbox_items_active
  ON toolbox_items(toolbox_id, service_id)
  WHERE deleted_at IS NULL;

CREATE INDEX idx_tbi_toolbox ON toolbox_items(toolbox_id);
```

## 8. Credentials (Vault Reference)

### 8.1 service_credentials

실제 secret은 vault(macOS Keychain 또는 fernet)에. DB는 ref와 메타.

```sql
CREATE TABLE service_credentials (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  credential_type TEXT NOT NULL
    CHECK (credential_type IN ('none', 'bearer_token', 'api_key_header', 'oauth_delegated', 'service_account')),
  secret_ref TEXT NOT NULL,           -- e.g. "keychain:coremcp:svc_xxx" or "fernet:row_id"
  header_name TEXT,                   -- api_key_header 시
  masked_value TEXT,                  -- UI 표시용 (예: "ghp_••••abcd")
  scopes JSON NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'connected'
    CHECK (status IN ('not_connected', 'connected', 'expired', 'revoked', 'error')),
  last_error_code TEXT,
  last_error_message TEXT,
  expires_at TIMESTAMP,
  rotated_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  -- partial unique index 아래 정의 (revoked_at IS NULL, service 당 1개 active credential)
);

-- revoke 후 재발급 호환 partial unique (ADR-035)
CREATE UNIQUE INDEX uq_service_credentials_service_active
  ON service_credentials(service_id)
  WHERE revoked_at IS NULL;
```

## 9. External Client

### 9.1 external_connections

```sql
CREATE TABLE external_connections (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  user_id TEXT NOT NULL REFERENCES users(id),
  toolbox_id TEXT REFERENCES toolboxes(id),
  client_type TEXT NOT NULL
    CHECK (client_type IN ('claude_code', 'claude', 'claude_desktop', 'chatgpt', 'openclaw', 'cursor', 'windsurf', 'other')),
  client_name TEXT,
  oauth_client_id TEXT,
  protocol_version TEXT,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'revoked')),
  scopes JSON NOT NULL DEFAULT '[]',
  client_quirks JSON NOT NULL DEFAULT '{}',
  created_ip TEXT,
  created_user_agent TEXT,
  last_used_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ext_user ON external_connections(user_id);
CREATE INDEX idx_ext_client ON external_connections(client_type, status);
```

### 9.2 connection_tokens (one-time)

```sql
CREATE TABLE connection_tokens (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  user_id TEXT NOT NULL REFERENCES users(id),
  toolbox_id TEXT REFERENCES toolboxes(id),
  token_hash TEXT NOT NULL UNIQUE,
  client_type TEXT NOT NULL,
  requested_scopes JSON NOT NULL DEFAULT '[]',
  created_ip TEXT,
  created_user_agent TEXT,
  used_ip TEXT,
  used_user_agent TEXT,
  expires_at TIMESTAMP NOT NULL,
  used_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_otk_expires ON connection_tokens(expires_at);
```

### 9.3 personal_access_tokens (Dual Token Model — ADR-030)

`cmcp_admin_*` token은 파일(`~/.coremcp/admin-token`)에 보관, DB 미저장.
`cmcp_client_*` token은 external_connection 단위로 발급되며 hash만 DB에 저장.
external_connection revoke 시 해당 token 즉시 invalidate.

```sql
CREATE TABLE personal_access_tokens (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  external_connection_id TEXT REFERENCES external_connections(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id),
  token_hash TEXT NOT NULL,
  token_prefix TEXT NOT NULL,                  -- 마지막 8자 등 (UI 표시용)
  scopes JSON NOT NULL DEFAULT '[]',
  protocol_version TEXT,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'revoked')),
  last_used_at TIMESTAMP,
  expires_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT chk_pat_revoked_consistency CHECK (
    (status = 'revoked' AND revoked_at IS NOT NULL) OR
    (status = 'active' AND revoked_at IS NULL)
  )
);

-- 상태 일관성: revoked_at IS NOT NULL ⇔ status='revoked'
-- 애플리케이션에서 revoke 시 둘 다 동시 업데이트:
--   UPDATE personal_access_tokens SET status='revoked', revoked_at=CURRENT_TIMESTAMP WHERE id=?
-- CHECK constraint으로 강제 (위 chk_pat_revoked_consistency).

CREATE UNIQUE INDEX uq_pat_hash_active
  ON personal_access_tokens(token_hash)
  WHERE revoked_at IS NULL;

CREATE INDEX idx_pat_external_conn ON personal_access_tokens(external_connection_id);
CREATE INDEX idx_pat_user ON personal_access_tokens(user_id);

CREATE INDEX idx_pat_status_revoked
  ON personal_access_tokens(status, revoked_at);

CREATE INDEX idx_pat_expires
  ON personal_access_tokens(expires_at)
  WHERE expires_at IS NOT NULL AND revoked_at IS NULL;
```

운영:
- 새 client 등록 시 token 평문은 1회만 응답에 노출, 이후 hash만
- token 길이 256-bit, prefix `cmcp_client_`
- hash: sha256 + 비교는 hmac.compare_digest
- external_connections 삭제 시 ON DELETE CASCADE
- admin token은 본 테이블 미사용 (파일 기반)

## 10. MCP Sessions

in-memory 우선이지만 multi-process 확장 시 DB로 이동 가능. 옵션 테이블:

```sql
CREATE TABLE mcp_sessions (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  session_id TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL REFERENCES users(id),
  external_connection_id TEXT REFERENCES external_connections(id),
  client_name TEXT,
  client_version TEXT,
  protocol_version TEXT,
  capabilities_json JSON NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  initialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP,
  terminated_at TIMESTAMP
);

CREATE INDEX idx_sess_user ON mcp_sessions(user_id);
```

MVP는 이 테이블 미사용, app dict 기반.

## 11. Logs

### 11.1 tool_invocations

```sql
CREATE TABLE tool_invocations (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  request_id TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id),
  external_connection_id TEXT REFERENCES external_connections(id),
  toolbox_id TEXT REFERENCES toolboxes(id),
  service_id TEXT REFERENCES mcp_services(id),
  service_tool_id TEXT REFERENCES service_tools(id),
  exposed_tool_name TEXT NOT NULL,
  downstream_tool_name TEXT,
  status TEXT NOT NULL
    CHECK (status IN ('success', 'error', 'timeout', 'cancelled', 'policy_denied', 'auth_failed', 'rate_limited')),
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

CREATE INDEX idx_inv_user_created ON tool_invocations(user_id, created_at DESC);
CREATE INDEX idx_inv_service_created ON tool_invocations(service_id, created_at DESC);
CREATE INDEX idx_inv_request ON tool_invocations(request_id);
CREATE INDEX idx_inv_idempotency ON tool_invocations(idempotency_key) WHERE idempotency_key IS NOT NULL;
```

raw arguments / output은 미저장. 옵션 debug trace는 별도 테이블(아래).

### 11.2 audit_logs

```sql
CREATE TABLE audit_logs (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  request_id TEXT,
  actor_user_id TEXT REFERENCES users(id),
  action TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT,
  ip TEXT,
  user_agent TEXT,
  metadata JSON NOT NULL DEFAULT '{}',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_aud_actor_created ON audit_logs(actor_user_id, created_at DESC);
CREATE INDEX idx_aud_resource ON audit_logs(resource_type, resource_id);
```

### 11.3 debug_traces (opt-in)

환경 변수로 활성. 24h 자동 삭제.

```sql
CREATE TABLE debug_traces (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  invocation_id TEXT NOT NULL REFERENCES tool_invocations(id) ON DELETE CASCADE,
  arguments_json JSON,
  result_json JSON,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_dt_expires ON debug_traces(expires_at);
```

## 12. Async Jobs

### 12.1 jobs

BackgroundTasks는 in-memory 추적 가능하지만 dashboard에 노출하려면 DB에 기록.

```sql
CREATE TABLE jobs (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  kind TEXT NOT NULL
    CHECK (kind IN ('service_validation', 'service_refresh', 'credential_rotate', 'export', 'cleanup')),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'success', 'failed', 'cancelled')),
  progress REAL NOT NULL DEFAULT 0.0,
  payload JSON NOT NULL DEFAULT '{}',
  result JSON,
  error JSON,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_jobs_status ON jobs(status);
```

## 13. OAuth (옵션, Phase P3+)

OAuth 활성 시 추가:

```sql
CREATE TABLE oauth_clients (
  id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  client_id TEXT NOT NULL UNIQUE,
  client_secret_hash TEXT,
  client_name TEXT NOT NULL,
  redirect_uris JSON NOT NULL DEFAULT '[]',
  grant_types JSON NOT NULL DEFAULT '[]',
  response_types JSON NOT NULL DEFAULT '[]',
  token_endpoint_auth_method TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  revoked_at TIMESTAMP
);

CREATE TABLE oauth_authorization_codes (
  code_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  client_id TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  resource TEXT,
  scopes JSON NOT NULL DEFAULT '[]',
  code_challenge TEXT NOT NULL,
  code_challenge_method TEXT NOT NULL DEFAULT 'S256',
  expires_at TIMESTAMP NOT NULL,
  used_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE oauth_access_tokens (
  jti TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  client_id TEXT NOT NULL,
  external_connection_id TEXT REFERENCES external_connections(id),
  audience TEXT NOT NULL,
  scopes JSON NOT NULL DEFAULT '[]',
  issued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL,
  revoked_at TIMESTAMP
);

CREATE TABLE oauth_refresh_tokens (
  token_hash TEXT PRIMARY KEY,
  parent_jti TEXT,
  user_id TEXT NOT NULL REFERENCES users(id),
  client_id TEXT NOT NULL,
  external_connection_id TEXT REFERENCES external_connections(id),
  scopes JSON NOT NULL DEFAULT '[]',
  issued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL,
  used_at TIMESTAMP,
  revoked_at TIMESTAMP
);

CREATE TABLE jwks_keys (
  kid TEXT PRIMARY KEY,
  algorithm TEXT NOT NULL DEFAULT 'RS256',
  public_key_pem TEXT NOT NULL,
  private_key_secret_ref TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  rotated_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

OAuth 비활성 시 위 테이블 미사용.

### 13.x CIMD Client Metadata Cache (ADR-036)

CIMD(Client ID Metadata Documents) cache는 다음 두 가지 옵션 중 선택:

#### Option A (default for P3 MVP): in-memory
- Python dict 또는 cachetools.TTLCache
- key: `cimd:<client_id_url>`
- TTL 1h
- 프로세스 재시작 시 모두 invalidate (재fetch 발생)
- 운영 단순, 단일 process에 적합

#### Option B (선택): DB 영구 저장
```sql
CREATE TABLE oauth_cimd_clients (
  client_id_url TEXT PRIMARY KEY,        -- HTTPS URL
  metadata_json JSON NOT NULL,           -- fetch한 metadata 원문
  fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL,
  fetch_count INTEGER NOT NULL DEFAULT 1,
  last_fetch_status_code INTEGER,
  last_fetch_error TEXT
);
-- client_id_url은 요청 URL과 byte-exact 일치하는 값 사용 (CIMD validation)
-- 응답 metadata의 client_id field == client_id_url 검증 (06-security-auth §4.4.2)

CREATE INDEX idx_cimd_expires ON oauth_cimd_clients(expires_at);
```

권장:
- 개인 단일 프로세스: Option A (in-memory)
- multi-process 확장 시: Option A + Redis 또는 Option B (DB)
- SaaS 전환 시: Option B 또는 Redis 필수

선택은 환경 변수 `CIMD_CACHE_BACKEND=memory|db` 로.

본 프로젝트 P3 default: memory.

## 14. Migration Order

1. users
2. mcp_services
3. service_tools
4. tool_aliases
5. service_validation_runs
6. toolboxes
7. toolbox_items
8. service_credentials
9. external_connections
10. personal_access_tokens
11. connection_tokens
12. mcp_sessions (옵션)
13. tool_invocations
14. audit_logs
15. debug_traces (옵션)
16. jobs
17. oauth_* (옵션, Phase P3+)

Alembic 사용. SQLite 대상 시 `render_as_batch=True`.

## 15. Data Retention

| Data | Retention |
|---|---|
| tool_invocations metadata | 90일 기본, 환경 변수로 연장 |
| audit_logs | 1년 |
| raw arguments / output | 미저장 (debug_traces opt-in, 24h auto-delete) |
| validation_runs | 최근 20개 per service, 이전은 정리 |
| connection_tokens expired | 7일 후 삭제 |
| revoked credentials | metadata만 유지, secret_ref vault에서 destroy |
| personal_access_tokens revoked | metadata 30일, 이후 삭제 |
| jobs finished | 30일 |
| oauth_authorization_codes | 1시간 |
| oauth_access_tokens expired | 즉시 삭제 또는 jti만 denylist 7일 |
| oauth_refresh_tokens | rotation 시 family 추적 30일 |
| service_tools.icons_json size | 32KB per row, allowlist content-type |
| oauth_cimd_clients cache | TTL 1h, fetch fail 시 24h backoff |
| icons_json SVG content | 보안 검증 후 저장 (애플리케이션 sanitize) |

cleanup은 `kind='cleanup'` job으로 daily 실행.

## 16. Privacy / Security Notes

- secret_ref는 secret value가 아니다.
- tool arguments는 기본 미저장. debug_traces opt-in 시에만 평문 24h 한정.
- audit_logs / tool_invocations에 credential 원문 금지.
- backup 파일도 secret_ref만 포함하므로 keychain/fernet 분실 시 복호화 불가 (각 secret 재등록 필요).
- `cmcp_admin_*` token은 DB에 절대 저장하지 않는다 (파일 + chmod 600).
- `cmcp_client_*` token은 hash만 DB. 응답에서 1회 평문 노출 후 재조회 불가.
- partial unique index로 soft-delete 후 재생성을 허용한다. hard-delete cleanup job은 deleted_at < now() - 30d 기준.
- icons_json은 외부 URL 또는 data URI를 포함할 수 있다. 외부 URL fetch는 서비스 등록 시 SSRF guard 통과 후 캐싱 (P1+). data URI는 size cap 검증.
- icons의 SVG 처리는 XSS 위험이 크다. inline 렌더링 금지, `<img>` only, CSP 적용. 옵션 `ICON_SVG_ENABLED=false`로 완전 차단 가능 (default false 권장).
- CIMD metadata는 외부 URL fetch이므로 SSRF guard 적용 후 캐시 (06-security-auth §7.5).
- icons_json 각 entry는 `{src, mimeType, sizes?}` schema 검증. `url` field는 MCP spec 표준 아님이므로 sanitize 단계에서 정정 또는 reject.

## 17. PostgreSQL 차이 요약

PostgreSQL로 마이그레이션 시 변경:

- UUID 컬럼: `UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- TIMESTAMP: `TIMESTAMPTZ NOT NULL DEFAULT now()`
- JSON: `JSONB`
- INET: `INET`
- TEXT[]: `TEXT[]`
- ENUM-like CHECK → `CREATE TYPE ... AS ENUM`
- partial unique index 동일 지원
- tool_invocations / audit_logs를 PARTITION BY RANGE(created_at) monthly 권장 (단일 사용자라 필수 아님)
- RLS는 미사용 (단일 사용자)
- 향후 SaaS 전환 시 `15-future-saas-migration.md`
- partial unique index는 SQLite 3.8+, PostgreSQL 모두 지원
- soft-delete 정책과 정합

## 18. Bootstrap (최초 실행)

```sql
-- users 1행
INSERT INTO users (id, email, name, locale, bootstrap_completed_at)
VALUES ('usr_local', 'me@local', 'Personal', 'ko', CURRENT_TIMESTAMP);

-- default toolbox
INSERT INTO toolboxes (id, owner_user_id, name, is_default)
VALUES ('tbx_default', 'usr_local', 'Default', 1);
```

bootstrap migration이나 app 첫 부트 시 실행.

## 19. 향후 SaaS 확장 hook

- `workspace_id` 컬럼은 모든 owned 테이블에 nullable로 선반영
- `visibility` 컬럼 (현재 private 고정)
- OAuth 테이블은 옵션 활성
- partition 적용 시점 명시: tool_invocations 1억 행 도달 시
- personal_access_tokens.scopes를 활용해 token별 권한 차등 (admin-readonly 등)
- workspace 활성 시 personal_access_tokens.workspace_id 컬럼 추가
- icons CDN 캐시 (object storage 이전) — Phase SaaS
- CIMD cache는 SaaS 전환 시 Redis(option A 확장) 또는 oauth_cimd_clients 테이블(option B) 사용

상세는 `15-future-saas-migration.md`.
