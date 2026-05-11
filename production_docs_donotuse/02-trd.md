# CoreMCP TRD

문서 버전: v0.1  
대상: Backend, Frontend, Infra, Security Engineering  
제품 유형: MCP Toolbox + Authenticated MCP Gateway SaaS

---

## 1. 기술 목표

CoreMCP는 HTTP 기반 protected MCP server로 동작한다. 외부 MCP client는 CoreMCP `/mcp` endpoint 하나만 연결한다. CoreMCP는 access token으로 사용자를 식별하고, 사용자 toolbox에 담긴 downstream MCP services의 cached tool schema를 병합해 tool catalog를 제공한다. tools/call 요청은 alias map을 통해 downstream service/tool로 라우팅한다.

---

## 2. 표준/호환성 기준

### 2.1 MCP Transport

- 우선 지원: Streamable HTTP
- endpoint: `/mcp`
- JSON-RPC 2.0 payload 처리
- POST: client → server message
- GET: minimal SSE 구현 (empty stream + 15s keepalive ping). ADR-015. `notifications/tools/list_changed` emission 채널로 사용.
- DELETE: session termination 지원
- `Mcp-Session-Id` 지원
- `MCP-Protocol-Version` header 처리

### 2.2 MCP Authorization

- CoreMCP는 OAuth 2.1 resource server로 동작
- protected resource metadata 제공
- authorization server metadata 제공 또는 외부 AS discovery 연결
- access token은 `Authorization: Bearer <token>` header로 수신
- every HTTP request에서 token 검증
- audience/resource 검증 필수
- token passthrough 금지
- Dynamic Client Registration (RFC 7591) endpoint 제공 (ADR-022)
- PKCE S256 mandatory
- Resource Indicator (RFC 8707) — `resource=https://coremcp.example.com/mcp` authorize와 token request 양쪽에서 강제
- JWKS endpoint 제공 (token signature 검증용)
- Token introspection (RFC 7662) + JTI denylist (Redis)
- Token revocation endpoint (RFC 7009)
- Refresh token rotation enabled
- Access token format: JWT RS256 (ADR-021)

### 2.3 Target Clients

MVP 우선순위:

1. Claude Code remote HTTP MCP
2. Claude custom connector
3. ChatGPT custom MCP app/developer mode
4. OpenClaw/로컬 agent one-time token
5. Cursor/Windsurf 등 기타 MCP client

---

## 3. 시스템 아키텍처

```text
[External AI Client]
  Claude Code / Claude / ChatGPT / OpenClaw
        |
        | MCP Streamable HTTP + OAuth/Bearer/Connection Token
        v
[CoreMCP Edge]
  - HTTP routing
  - Auth middleware
  - MCP request parser
  - Session manager
        |
        v
[CoreMCP Domain]
  - Toolbox resolver
  - Tool catalog builder
  - Policy checker
  - Tool alias resolver
  - Proxy executor
  - Audit logger
        |
        v
[Data Layer]
  - PostgreSQL
  - Redis
  - Secret Vault/KMS
        |
        v
[Downstream MCP Services]
  - User registered remote MCP
  - Public marketplace MCP
  - Internal MCP
```

---

## 4. 서비스 구성

### 4.1 Web App

기술: Next.js

책임:

- login/signup UI
- dashboard
- MCP registry UI
- toolbox UI
- developer console
- validation report
- playground/test tool call
- connected clients settings
- connection guide

### 4.2 API Server

기술: FastAPI (확정, ADR-003 후속). Pydantic v2, SQLAlchemy 2.0 async, Alembic, Authlib.

책임:

- REST API
- admin/developer console API
- auth callback/helper
- service validation
- toolbox management
- credential management
- logs API

### 4.3 MCP Gateway Server

기술: FastAPI + MCP SDK 또는 독립 ASGI app

책임:

- `/mcp` endpoint
- JSON-RPC request handling
- initialize/tools/list/tools/call
- session id handling
- OAuth token validation
- user-specific tool catalog
- downstream proxy

MVP에서는 API Server와 동일 프로세스 가능. Production에서는 분리 가능.

### 4.4 Worker

기술: Arq (Redis 기반, asyncio native). ADR-020.

책임:

- service validation async job
- tool schema refresh
- marketplace scanning
- credential expiry check
- audit export
- usage aggregation

### 4.5 Storage

- PostgreSQL: primary relational data
- Redis: cache, rate limit, job queue, session ephemeral state
- KMS/Vault: secret encryption/decryption
- Object storage: logos, validation artifacts, exports

---

## 5. 도메인 모델

### 5.1 User

CoreMCP 계정 주체.

### 5.2 Workspace

MVP에서는 optional. DB에는 선반영 권장.

### 5.3 MCP Service

등록된 downstream MCP server.

속성:

- owner
- endpoint URL
- auth type
- visibility
- status
- validation status
- category
- metadata

### 5.4 Service Tool

MCP service에서 수집된 tool schema cache.

### 5.5 Toolbox

사용자가 외부 AI에서 사용하려는 MCP service 모음.

### 5.6 Toolbox Item

toolbox와 MCP service의 연결.

### 5.7 Tool Alias

외부 AI에 노출되는 tool name과 downstream tool name의 매핑.

### 5.8 User Service Connection

사용자와 MCP service 사이의 credential/auth 연결 상태.

### 5.9 External Connection

Claude Code, Claude, ChatGPT, OpenClaw 등 외부 client 연결 상태.

### 5.10 Tool Invocation

하나의 tools/call 실행 기록.

---

## 6. MCP Gateway 요청 처리

### 6.1 Request Pipeline

```text
HTTP request
  -> request_id 생성/전파
  -> Origin 검증
  -> Authorization header 검증
  -> token audience/scope/user 검증
  -> MCP session 검증
  -> JSON-RPC parse
  -> method dispatch
  -> policy check
  -> response serialize
  -> audit/invocation log
```

### 6.2 initialize

처리:

1. client protocol version 확인
2. client info 저장
3. server capabilities 반환
4. optional `Mcp-Session-Id` 생성

MVP server capabilities:

```json
{
  "tools": {
    "listChanged": true
  }
}
```

### 6.3 tools/list

처리:

1. user_id 식별
2. default toolbox 조회
3. enabled toolbox items 조회
4. service_tools cache 조회
5. policy filter 적용
6. exposed tools 반환

장애 정책:

- 특정 service cache missing: 해당 service 제외 + warning log
- 전체 tool empty: CoreMCP 관리 tool만 반환 가능
- unauthorized: HTTP 401
- insufficient scope: HTTP 403 또는 JSON-RPC error

### 6.4 tools/call

처리:

1. tool name normalize
2. tool_alias 조회
3. toolbox membership 확인
4. service status 확인
5. user_service_connection 확인
6. credential resolve
7. downstream MCP client 호출
8. result normalize
9. invocation log 기록
10. idempotency_key 헤더 처리 (write 작업 재시도 안전)
11. cancellation token 처리 (client가 도중 취소)
12. progress notification forward (downstream → client)

cancellation 정책:

- client가 `notifications/cancelled`를 보내면 downstream으로 forward
- invocation status는 'cancelled'로 기록 (05 schema 보강 필요)
- 부분 부작용은 사용자 책임

idempotency 정책:

- `Idempotency-Key` 헤더가 있으면 24h 동안 동일 결과 캐시
- annotation `idempotentHint: true` tool에 대해서만 자동 활성 (옵트인)

---

## 7. Downstream MCP Client

### 7.1 지원 transport

MVP:

- Remote Streamable HTTP
- Remote SSE는 deprecated지만 read-only fallback 검토 가능

MVP 제외:

- stdio MCP hosting
- local command execution

### 7.2 Downstream auth types

```text
none
bearer_token
api_key_header
api_key_query - MVP에서는 비권장/기본 차단
oauth_delegated - Phase 3
service_account - Phase 2+
```

### 7.3 Credential Injection

예시:

```text
bearer_token:
  Authorization: Bearer <downstream_token>

api_key_header:
  X-API-Key: <secret>
```

절대 금지:

```text
Authorization: Bearer <CoreMCP access token>
```

### 7.4 Timeout/Retry

기본값:

- connect timeout: 3s
- read timeout: 30s
- total timeout: 35s
- retry: idempotent read/list only 1회
- tools/call retry: 기본 off

Client별 timeout 차이 매트릭스:

| Client | 권장 max latency |
|---|---|
| Claude Code | 60s |
| Claude desktop/web | 60s |
| ChatGPT custom MCP | 30s (보수적) |
| OpenClaw | 90s |
| Cursor/Windsurf | 60s |

CoreMCP는 client_type에 따라 downstream timeout을 동적 조정한다 (17-mcp-client-profiles.md).

### 7.5 Result Normalization

Downstream MCP response를 그대로 반환하되, gateway-level error는 JSON-RPC error로 매핑한다.

---

## 8. Tool Catalog Builder

### 8.1 Exposed Tool Naming

기본:

```text
{service_slug}.{tool_name}
```

예:

```text
github.create_issue
notion.search_page
calendar.create_event
```

Fallback:

```text
{service_slug}__{tool_name}
```

### 8.2 충돌 처리

충돌 조건:

- 동일 toolbox 내 exposed tool name 중복
- downstream tool name에 unsafe character 포함
- service slug 변경으로 alias 변경 가능성

정책:

1. service slug unique 보장
2. tool name sanitize
3. alias table에 fixed exposed_name 저장
4. slug 변경 시 기존 alias 유지
5. 수동 rename 지원은 Phase 2

### 8.3 Schema Hash

계산 대상:

- original tool name
- description
- input schema
- output schema

예:

```text
sha256(canonical_json({name, description, inputSchema, outputSchema}))
```

canonical_json은 RFC 8785 (JSON Canonicalization Scheme) 기반:

- UTF-8 정렬
- 객체 key는 lexicographic sort
- 숫자 표현 표준화
- whitespace 제거

annotations(destructiveHint, readOnlyHint, idempotentHint, openWorldHint, title)도 hash 대상에 포함.

```text
sha256(canonical_json({name, description, inputSchema, outputSchema, annotations}))
```

### 8.4 Cache Refresh

트리거:

- service create
- credential update
- manual refresh
- TTL refresh
- tools/call schema error
- downstream notifications/tools/list_changed 수신 시

MVP TTL:

- private service: 1h (기존 24h → 단축, listChanged 즉시 invalidate 가능하므로)
- public service: 30min
- failed service: exponential backoff 1min → 32min
- listChanged notification 수신 시: 즉시 invalidate
- toolbox 변경 시: per-user L1/L2 cache 즉시 invalidate

Cache 구조 (ADR-018):

- L1: in-process LRU per pod (TTL 60s)
- L2: Redis per user (TTL 1h)
- L3: PostgreSQL service_tools (TTL 24h hard cap)
- invalidation fan-out: Redis pub/sub channel `cache:invalidate:user:{user_id}` / `cache:invalidate:service:{service_id}`

---

## 9. REST API 개요

Base URL: `https://api.coremcp.example.com`

### Auth/User

- `GET /v1/me`
- `GET /v1/sessions`
- `DELETE /v1/sessions/{id}`

### MCP Services

- `POST /v1/mcp-services`
- `GET /v1/mcp-services`
- `GET /v1/mcp-services/{service_id}`
- `PATCH /v1/mcp-services/{service_id}`
- `DELETE /v1/mcp-services/{service_id}`
- `POST /v1/mcp-services/{service_id}/validate`
- `POST /v1/mcp-services/{service_id}/refresh-tools`

### Toolbox

- `GET /v1/toolboxes`
- `POST /v1/toolboxes`
- `GET /v1/toolboxes/{toolbox_id}`
- `POST /v1/toolboxes/{toolbox_id}/items`
- `PATCH /v1/toolboxes/{toolbox_id}/items/{item_id}`
- `DELETE /v1/toolboxes/{toolbox_id}/items/{item_id}`

### Credentials

- `PUT /v1/mcp-services/{service_id}/credential`
- `DELETE /v1/mcp-services/{service_id}/credential`
- `POST /v1/mcp-services/{service_id}/credential/rotate`

### Client Connections

- `GET /v1/external-connections`
- `POST /v1/external-connections/one-time-token`
- `DELETE /v1/external-connections/{connection_id}`

### Logs

- `GET /v1/tool-invocations`
- `GET /v1/audit-logs`

---

## 10. Error Taxonomy

| Code | HTTP | 설명 |
|---|---:|---|
| `auth_required` | 401 | token 없음/만료 |
| `insufficient_scope` | 403 | scope 부족 |
| `tool_not_found` | 200/JSON-RPC | alias 없음 |
| `service_not_connected` | 200/JSON-RPC | credential 연결 안 됨 |
| `service_disabled` | 200/JSON-RPC | service 비활성화 |
| `downstream_timeout` | 200/JSON-RPC | downstream timeout |
| `downstream_error` | 200/JSON-RPC | downstream JSON-RPC error |
| `schema_stale` | 200/JSON-RPC | schema mismatch |
| `policy_denied` | 200/JSON-RPC | policy deny |
| `rate_limited` | 429 | rate limit |
| `validation_failed` | 400 | MCP 등록 검증 실패 |
| `unsafe_url` | 400 | SSRF guard 차단 |
| `cancelled` | 200/JSON-RPC | client cancellation |
| `idempotency_conflict` | 409 | idempotency key 중복 |
| `body_too_large` | 413 | request/response body > 5MB |
| `protocol_version_unsupported` | 200 | initialize negotiation 실패 |

---

## 11. 배포 구조

### 11.1 MVP 단일 리전

```text
Cloudflare/WAF
  -> Load Balancer
  -> API/MCP Gateway ASGI app
  -> PostgreSQL
  -> Redis
  -> KMS/Vault
  -> Worker
```

### 11.2 환경

- local
- dev
- staging
- production

### 11.3 필수 환경 변수

```text
APP_ENV
PUBLIC_BASE_URL
DATABASE_URL
REDIS_URL
OIDC_ISSUER
OIDC_CLIENT_ID
OIDC_CLIENT_SECRET
JWT_AUDIENCE
KMS_KEY_ID
SECRET_ENCRYPTION_KEY
MCP_RESOURCE_URI
MCP_ENDPOINT_URL
JWT_ALGORITHM=RS256
JWT_PRIVATE_KEY_PATH
JWT_PUBLIC_KEY_JWKS_URL
OAUTH_RESOURCE_INDICATOR
OAUTH_DCR_ENABLED
OAUTH_PKCE_REQUIRED=S256
OAUTH_REVOCATION_ENDPOINT
OAUTH_INTROSPECTION_ENDPOINT
DOWNSTREAM_CONNECT_TIMEOUT_MS=3000
DOWNSTREAM_READ_TIMEOUT_MS=30000
DOWNSTREAM_MAX_BODY_MB=5
DOWNSTREAM_MAX_REDIRECTS=0
EGRESS_PROXY_URL
EGRESS_PROXY_ALLOW_PORTS=443
CACHE_L1_TTL_SEC=60
CACHE_L2_TTL_SEC=3600
CACHE_L3_TTL_HOURS=24
RATE_LIMIT_TOOLS_LIST_PER_MIN=120
RATE_LIMIT_TOOLS_CALL_PER_MIN=60
OTEL_EXPORTER_OTLP_ENDPOINT
SENTRY_DSN
KMS_PROVIDER=aws
KMS_KEY_ROTATION_DAYS=365
DATA_REGION
```

---

## 12. 개발 우선순위

1. DB schema + migration
2. Auth integration
3. MCP Gateway minimal initialize/tools/list/tools/call
4. MCP service registration + validation
5. tool schema cache
6. toolbox resolver
7. downstream proxy executor
8. Claude Code integration test
9. audit/invocation logs
10. dashboard UI
11. security hardening
12. one-time connection token

---

## 13. TRD Open Questions

1. MCP SDK를 직접 사용할지, JSON-RPC handler를 직접 구현할지 결정 필요. — 결정: FastAPI + 직접 JSON-RPC handler (ADR-003)
2. OAuth Authorization Server를 내장할지 외부 provider를 쓸지 결정 필요. — 결정: Logto self-host (ADR-011)
3. ChatGPT custom app manifest 구조는 별도 문서로 분리 필요. — 분리: 17-mcp-client-profiles.md §4
4. Downstream MCP session을 사용자 request마다 새로 만들지, connection pool/session cache를 둘지 결정 필요. — 결정: cached 10min + per-call fallback (ADR-018)
5. SSE streaming tool result를 MVP에서 지원할지 결정 필요. — Phase 3 결정
