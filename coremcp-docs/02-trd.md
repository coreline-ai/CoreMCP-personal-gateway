# CoreMCP TRD (Personal)

문서 버전: v1.0
작성일: 2026-05-11
대상: Backend / Frontend (1인 개발)

---

## 1. 기술 목표

CoreMCP는 Mac mini에서 단일 프로세스(또는 백엔드 + 웹 별도 프로세스)로 동작하는 protected MCP gateway다. `/mcp` endpoint 하나로 사용자 toolbox의 downstream MCP tool을 노출하고, 정적 bearer token으로 사용자를 식별한다.

## 2. 표준 / 호환성

### 2.1 MCP Transport
- Streamable HTTP 준수
- POST /mcp: client → server JSON-RPC
- GET /mcp: SSE keepalive + `notifications/{tools,resources,prompts}/list_changed`, `notifications/progress`, `notifications/resources/updated` emit
- DELETE /mcp: session termination
- `Mcp-Session-Id` 헤더 처리
- MCP-Protocol-Version 헤더 처리 (ADR-029):
  - 2025-11-25: 최신 지원 버전, default 응답
  - 2025-06-18: Codex CLI/Claude Code 호환 버전
  - 헤더 누락 시 2025-06-18로 가정
  - 미래 버전 요청 시 가장 가까운 지원 버전으로 downgrade

### 2.1.1 지원 프로토콜 버전

| 버전 | 상태 | 비고 |
|---|---|---|
| 2025-11-25 | Supported | latest, default 응답 |
| 2025-06-18 | Supported | Codex CLI/Claude Code 호환성 유지 |
| 2025-03-26 | Best-effort | 일부 deprecated 동작 fallback |

협상 규칙:
- client가 `MCP-Protocol-Version: 2025-11-25` 요청 → CoreMCP 동일 버전 응답
- client가 `2025-06-18` 요청 → 그대로 응답
- client가 미지원 미래 버전 요청 → 가장 가까운 지원 버전으로 downgrade + warning 로그
- header 누락 → 2025-06-18 가정 (legacy client 호환)

(ADR-029) 참조.

2025-11-25 신규 사항 처리:
- JSON Schema dialect: 2020-12 명시 응답
- tool icons metadata: 캐시에 보존, 그대로 forward
- tasks/* (실험): client → CoreMCP 요청 시 -32601, downstream forward 안 함
- tool name guidance: §3 normalization과 정합
- input validation error: result.isError=true 사용 (JSON-RPC error 아님)

### 2.2 MCP Authorization
- protected resource로 동작
- `/.well-known/oauth-protected-resource` 응답 제공
- 기본 인증: 정적 bearer token
- OAuth flow는 옵션 (Phase P3+, ChatGPT 등 호환 시)
- token 검증: every HTTP request

### 2.3 MCP Methods 지원
- JSON-RPC batch: 지원 (sequential 처리, notification-only batch는 202)
- initialize: 지원
- initialize capabilities: default toolbox active service union 기반 동적 생성
- tools/list: 지원 (pagination cursor 포함)
- tools/call: 지원 (JSON Schema args 사전 검증, idempotency_key, cancellation, per-service quota 포함)
- notifications/initialized: 처리
- notifications/cancelled (client→server): forward to downstream
- notifications/{tools,resources,prompts}/list_changed (server→client): CoreMCP catalog 변경 및 downstream notification fan-in 시 emit
- notifications/progress, notifications/resources/updated: downstream → CoreMCP SSE fan-out
- ping: 응답
- resources/*, prompts/*: proxy 지원
- sampling/* elicitation/* logging/*: -32601 reject
- tasks/*: client → CoreMCP 요청 시 -32601 (Method not found). MCP 2025-11-25 experimental method. downstream으로 forward하지 않음.

### 2.4 Target Clients (우선순위)
1. Codex CLI exec (Mac mini 로컬)
2. Claude Code (옵션: Mac mini 로컬 / MacBook via Tailscale)
3. OpenClaw (one-time token)
4. Claude desktop custom connector (옵션, OAuth 구현 시)
5. ChatGPT custom MCP (옵션, OAuth 구현 시)
6. Cursor / Windsurf (옵션)

상세는 `14-mcp-client-profiles.md`.

## 3. 시스템 구성

```text
[Mac mini host]
├── CoreMCP API (FastAPI, port 8787)
│      ├── /mcp/* (MCP gateway)
│      ├── /v1/* (REST admin API)
│      ├── /.well-known/* (OAuth metadata)
│      ├── /health /ready /live
│      └── BackgroundTasks (validation, refresh)
├── CoreMCP Web (Next.js dev or static, port 3000)
└── Storage
       ├── SQLite (~/.coremcp/data/db.sqlite3)
       ├── macOS Keychain (downstream credentials)
       └── File logs (~/.coremcp/logs/)
```

옵션:
- PostgreSQL via Docker (single-user에도 가능)
- Redis via Docker (multi-process 확장 시)
- Tailscale 또는 Cloudflare Tunnel (외부 노출)

## 4. 서비스 구성

### 4.1 API Server (FastAPI 단일 프로세스)
책임:
- /mcp protocol endpoint
- REST admin API (/v1)
- OAuth metadata endpoint
- BackgroundTasks 기반 worker

확장 시: gunicorn + uvicorn workers (multi-process) → 그 때 in-memory state는 Redis로 이동

### 4.2 Web (Next.js)
책임:
- Dashboard, Services, Toolbox, Logs, Settings UI
- 정적 bearer token을 sessionStorage에 보관
- API 호출에 헤더 첨부

배포 옵션:
- A: `next dev` (개발 모드, hot reload)
- B: `next build && next start` (production)
- C: `next export` + API에서 static serve (가장 단순)

### 4.3 Storage
- **SQLite** 기본: `~/.coremcp/data/db.sqlite3`, WAL 모드, foreign_keys ON
- **PostgreSQL** 옵션: Docker compose 또는 Homebrew, multi-process 확장 시
- **macOS Keychain**: keyring 라이브러리, service prefix `coremcp:*`
- **Files**: logs, validation artifacts, export

## 5. 도메인 모델 (개인용 단순화)

| 엔티티 | 비고 |
|---|---|
| User | 단 1행, bootstrap 시 자동 생성 |
| MCPService | 등록된 downstream MCP |
| ServiceTool | tool schema cache |
| ToolAlias | exposed↔downstream 매핑 (별도 테이블) |
| Toolbox | 보통 1개 default |
| ToolboxItem | toolbox-service 연결 |
| ServiceCredential | downstream 인증 정보 (vault ref) |
| ExternalConnection | Codex CLI exec, Claude Code 등 client 등록 |
| ConnectionToken | one-time token (OpenClaw 등) |
| PersonalAccessToken | per-client token hash (ADR-030) |
| MCPSession | in-memory dict (DB 옵션) |
| ToolInvocation | 실행 기록 |
| AuditLog | 보안/관리 이벤트 |
| ValidationRun | service 검증 기록 |

`workspace`, `workspace_members`는 스키마에 없음 (단일 사용자).
상세는 `05-database-schema.md`.

## 6. MCP Gateway 처리 파이프라인

```text
HTTP /mcp request
  -> request_id 생성/전파
  -> Origin/CORS 검증 (localhost + Tailscale)
  -> Authorization Bearer 검증 (admin token 파일 비교 또는 client token DB hash, ADR-030)
  -> Mcp-Session-Id 검증 (in-memory map)
  -> JSON-RPC parse
  -> method dispatch
       ├── initialize → ServerInfo + capabilities + Mcp-Session-Id
       ├── tools/list → ToolboxResolver + CatalogBuilder
       ├── tools/call → AliasResolver + Policy + Vault + DownstreamClient
       ├── ping → ok
       └── unsupported → -32601
  -> response serialize
  -> audit / invocation log (background)
```

## 7. Downstream Client

### 7.1 Transport
- Remote Streamable HTTP (MVP)
- Remote SSE legacy: read-only fallback 가능
- stdio downstream: MVP 제외 (localhost http로 wrap한 stdio는 가능)

### 7.2 Auth Types (MVP)
- none
- bearer_token (Authorization: Bearer)
- api_key_header (custom header)
- oauth_delegated: Phase P3+
- api_key_query: 차단

### 7.3 Timeout
- connect 3s / read 30s / total 35s
- retry: idempotent annotation tool만 1회 (옵션)

### 7.4 Response 처리
- size limit 5MB
- structuredContent passthrough
- isError 보존
- _meta passthrough (단 coremcp.* prefix는 reserved)

## 8. Tool Catalog Builder

### 8.1 Exposed Name 형식
- `{service_slug}.{tool_name}` (점 구분, ADR-019)
- exposed_name 총 길이는 CoreMCP 정책상 64자 이내. MCP 2025-11-25 spec 자체에는 hard cap 명시 없음 (tool name 64자 cap은 CoreMCP 정책, MCP spec hard cap 아님)
- normalization: trim, NFKC, lowercase, spaces→`_`, unsafe→`_`, zero-width strip

### 8.2 Schema Hash
- sha256(canonical_json({name, description, inputSchema, outputSchema, annotations}))
- canonical_json: RFC 8785 또는 자체 (key sort + UTF-8)
- annotations(destructive/readOnly/idempotent/openWorld/title)도 hash 대상

### 8.3 Cache
- L1 in-process dict TTL 60s
- L2 in-memory (또는 Redis 옵션) TTL 1h
- L3 DB service_tools TTL 24h hard cap
- invalidation: 직접 함수 호출(단일 프로세스) 또는 Redis pub/sub(다중)

### 8.4 Refresh Trigger
- service 등록 직후 (validation)
- credential 변경
- manual refresh
- TTL expiry
- downstream tools/call schema error
- downstream notifications/{tools,resources,prompts}/list_changed (HTTP SSE/STDIO notification fan-in)

## 9. REST API 개요

Base URL: `http://localhost:8787` (또는 `https://macmini.ts.net`)

### Auth
- 모든 `/v1/*`는 정적 bearer 검증 (또는 자체 OAuth)
- `/health`, `/ready`, `/live`는 공개

### Endpoint 그룹
- `/v1/me` (single user, bootstrap)
- `/v1/mcp-services/*`
- `/v1/mcp-services/{id}/credential`
- `/v1/mcp-services/{id}/validate`
- `/v1/mcp-services/{id}/refresh-tools`
- `/v1/mcp-services/{id}/tools`
- `/v1/toolboxes/*`
- `/v1/toolboxes/{id}/items/*`
- `/v1/external-connections/*`
- `/v1/external-connections/one-time-token`
- `/v1/external-connections/exchange`
- `/v1/tool-invocations`
- `/v1/audit-logs`
- `/v1/jobs/{id}`
- `/v1/playground/call` (디버깅용 직접 tool 호출)
- `/v1/settings/token` (정적 token 회전)

상세 schema는 `04-api-spec.md`.

## 10. Error Taxonomy

| Code | HTTP/JSON-RPC | 설명 |
|---|---|---|
| auth_required | 401 | bearer 없음/만료 |
| invalid_token | 401 | token 불일치 |
| tool_not_found | 200/JSON-RPC -32602 | alias 없음 / unknown tool name (ADR-034) |
| tool_not_in_toolbox | result isError | toolbox 비포함 |
| service_disabled | result isError | service 비활성 |
| service_not_connected | result isError | credential 부재 |
| credential_expired | result isError | 인증 만료 |
| downstream_timeout | result isError | downstream timeout |
| downstream_error | result isError | downstream JSON-RPC error |
| schema_stale | result isError | schema mismatch |
| cancelled | result isError | client cancellation |
| invalid_arguments | 200/JSON-RPC -32602 | params schema 위반 |
| method_not_found | 200/JSON-RPC -32601 | 미지원 method (tasks/*, sampling/createMessage, elicitation/create, resources/*, prompts/*, completions/*, logging/setLevel). client → CoreMCP 요청에 대해서만 발생 |
| parse_error | -32700 | JSON parse 오류 |
| internal_error | -32603 | 내부 오류 |
| validation_failed | 400 | service 등록 검증 실패 |
| unsafe_url | 400 | SSRF guard |
| body_too_large | 413 | request/response cap 초과 |
| rate_limited | 429 | rate limit (전역) |

## 11. 환경 변수

```text
# Core
COREMCP_HOST=127.0.0.1
COREMCP_PORT=8787
COREMCP_DATA_DIR=~/.coremcp
COREMCP_LOG_LEVEL=INFO

# Auth
COREMCP_ADMIN_TOKEN_FILE=~/.coremcp/admin-token
# OAuth (옵션, Phase P3+)
OAUTH_ENABLED=false
OAUTH_ISSUER=
JWT_ALGORITHM=RS256
JWT_PRIVATE_KEY_PATH=

# DB
DATABASE_URL=sqlite+aiosqlite:///~/.coremcp/data/db.sqlite3
# 또는 postgresql+asyncpg://localhost/coremcp

# Cache
CACHE_BACKEND=memory
# 또는 redis://localhost:6379/0
CACHE_L1_TTL_SEC=60
CACHE_L2_TTL_SEC=3600

# Worker
WORKER_BACKEND=background_tasks
# 또는 arq

# Secret
SECRET_BACKEND=keychain
# 또는 fernet
FERNET_KEY_FILE=~/.coremcp/secret.key

# Downstream
DOWNSTREAM_CONNECT_TIMEOUT_MS=3000
DOWNSTREAM_READ_TIMEOUT_MS=30000
DOWNSTREAM_MAX_BODY_MB=5
DOWNSTREAM_MAX_REDIRECTS=0

# Rate limit (global)
RATE_LIMIT_GLOBAL_TOOLS_LIST_PER_MIN=600
RATE_LIMIT_GLOBAL_TOOLS_CALL_PER_MIN=300

# Logging
LOG_FILE=~/.coremcp/logs/coremcp.log
LOG_FORMAT=json

# Observability (옵션)
OTEL_EXPORTER_OTLP_ENDPOINT=
SENTRY_DSN=
METRICS_ENABLED=false

# Protocol
MCP_SUPPORTED_VERSIONS=2025-11-25,2025-06-18
MCP_DEFAULT_VERSION=2025-11-25

# Token model (ADR-030)
TOKEN_MODEL=dual               # dual | admin_only
COREMCP_ADMIN_TOKEN_FILE=~/.coremcp/admin-token
# cmcp_admin_*: 파일 보관, root 권한
# cmcp_client_*: personal_access_tokens DB hash, per external_connection

# Auth mode (ADR-032)
AUTH_MODE=static_bearer        # static_bearer | oauth

# Static_bearer 모드 metadata endpoint 노출 정책 (default: 비노출)
EXPOSE_RESOURCE_METADATA_IN_STATIC_MODE=false

# Secret backend operational mode (ADR-031)
SECRET_BACKEND=keychain        # keychain | fernet
# keychain: Mac mini 자동 로그인 환경 권장
# fernet: headless 무인 운영 권장 (FERNET_KEY_FILE 필요)

# SSRF private CIDR allowlist (ADR-033)
ALLOW_PRIVATE_DOWNSTREAM=false
ALLOW_TAILSCALE_DOWNSTREAM=false
ALLOW_LOOPBACK_DOWNSTREAM=true       # localhost http 허용 (fake-mcp 개발용)
ALLOWED_PRIVATE_CIDRS=          # 콤마 구분, 예: "100.64.0.0/10,10.0.0.0/8"
```

## 12. 개발 우선순위

1. SQLite schema + Alembic migration
2. FastAPI app skeleton + auth middleware (정적 bearer)
3. `/mcp` minimal (initialize/tools/list/tools/call with fake downstream)
4. Protocol version negotiation handler (2025-06-18 + 2025-11-25)
5. MCP service registration + URL safety
6. Validation BackgroundTask (initialize + tools/list)
7. Schema cache + tool_aliases
8. Toolbox resolver
9. Downstream proxy executor + credential vault (keyring)
10. Dual token model: admin + per-client revocable
11. AUTH_MODE 분리 (static_bearer 기본, oauth 옵션)
12. Codex CLI MCP smoke + optional Claude Code integration test
13. Audit/invocation logging
14. Web UI (Next.js)
15. listChanged emission
16. One-time connection token
17. OpenTelemetry/Sentry 통합 (옵션)
18. launchd daemon

상세는 `09-implementation-plan.md`.

## 13. Open Questions (Personal Scope)

1. SQLite 기본으로 시작 → Postgres 마이그레이션 시점은? (성능 한계 도달 시)
2. Web UI는 별도 process(Next.js dev)인가, FastAPI에서 static serve인가? (개발 편의 vs 단순성)
3. OAuth flow는 어느 시점에 구현? (ChatGPT/Cursor 사용 시점)
4. Tailscale Serve vs Caddy reverse proxy?
5. Backup 주기 — daily SQLite .backup 또는 Time Machine 의존?
6. 2025-11-25의 `tasks` 실험 기능을 어느 시점에 평가할지?
7. Claude Code가 실제로 2025-11-25를 요청하는지 실측 필요.
8. cmcp_admin_* token을 1Password 등에 백업할지 launchd 환경 변수로만 둘지?
9. 2025-11-25 client capabilities 신규 필드(예: tasks)에 대한 ChatGPT/Claude Code 실제 사용 패턴 검증
