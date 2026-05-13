# CoreMCP API Spec (Personal)

문서 버전: v1.0
작성일: 2026-05-11

---

## 1. API Groups

CoreMCP API는 두 계층:
1. **MCP Protocol API** (`/mcp`, `/.well-known/*`) — 외부 AI client용
2. **Admin REST API** (`/v1/*`) — 본인용 웹 UI / CLI

기본 base URL: `http://localhost:8787` (또는 Tailscale 도메인).

---

## 2. MCP Protocol API

### 2.1 Endpoints
```http
POST /mcp
GET /mcp
DELETE /mcp
GET /.well-known/oauth-protected-resource
GET /.well-known/oauth-authorization-server  (옵션, OAuth 활성 시)
GET /.well-known/jwks.json                   (옵션, JWT 활성 시)
```

### 2.1.1 인증 모드 (AUTH_MODE)

CoreMCP는 두 가지 인증 모드를 지원한다 (ADR-032):

| AUTH_MODE | 동작 | 우선 시나리오 |
|---|---|---|
| `static_bearer` (default) | 모든 `/mcp` 요청은 `Authorization: Bearer <token>` 검증. 401 응답은 `WWW-Authenticate: Bearer realm="coremcp"`만 포함. OAuth metadata는 선택 노출. | Claude Code MVP |
| `oauth` | OAuth 2.1 흐름 활성. 401 응답에 `resource_metadata` URL 포함. authorization_servers 응답 채움. | ChatGPT/Cursor 동시 사용 시 |

전환은 환경 변수 `AUTH_MODE`로 무중단 가능. 단 oauth 모드 활성 후에도 static bearer는 호환 유지 (admin token).

Token 종류 (ADR-030):
- `cmcp_admin_*`: 관리자/web root. 파일(`~/.coremcp/admin-token`)에 보관. /v1/* admin API 전용.
- `cmcp_client_*`: external_connection 단위. DB의 `personal_access_tokens.token_hash` 비교. /mcp 호출 전용.

### 2.2 Headers
```http
Authorization: Bearer <coremcp_client_token>
Accept: application/json, text/event-stream
Content-Type: application/json
MCP-Protocol-Version: 2025-06-18
Mcp-Session-Id: <session_id>          # initialize 이후
Idempotency-Key: <uuid>               # tools/call 권장 (write 작업)
```

### 2.3 401 Response

static_bearer mode:

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer realm="coremcp", error="invalid_token"
```

oauth mode:

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer realm="coremcp",
  resource_metadata="http://localhost:8787/.well-known/oauth-protected-resource",
  error="invalid_token"
```

### 2.4 Protected Resource Metadata
```http
GET /.well-known/oauth-protected-resource
```

static_bearer mode (default) 응답:
- **기본 동작: 404 Not Found** — OAuth client 혼선 방지
- 환경 변수 `EXPOSE_RESOURCE_METADATA_IN_STATIC_MODE=true`로 활성 시에만 다음 응답 (운영자 명시 동의 필요):

```json
{
  "resource": "http://localhost:8787/mcp",
  "bearer_methods_supported": ["header"],
  "scopes_supported": ["mcp:tools.read", "mcp:tools.call", "mcp:connections.manage"]
}
```

(authorization_servers 필드는 omit — OAuth client가 oauth flow를 잘못 시도하지 않도록) (ADR-032)

oauth mode 응답:
```json
{
  "resource": "http://localhost:8787/mcp",
  "authorization_servers": ["http://localhost:8787"],
  "bearer_methods_supported": ["header"],
  "scopes_supported": ["mcp:tools.read", "mcp:tools.call", "mcp:connections.manage"]
}
```

`registration_endpoint`, `jwks_uri`는 Authorization Server Metadata 응답에만 둔다. RFC 9728(Protected Resource Metadata)과 RFC 8414(Authorization Server Metadata) 분리 준수.

### 2.4.1 Authorization Server Metadata

```http
GET /.well-known/oauth-authorization-server
```

oauth mode 응답:
```json
{
  "issuer": "http://localhost:8787",
  "authorization_endpoint": "http://localhost:8787/oauth/authorize",
  "token_endpoint": "http://localhost:8787/oauth/token",
  "registration_endpoint": "http://localhost:8787/oauth/register",
  "revocation_endpoint": "http://localhost:8787/oauth/revoke",
  "introspection_endpoint": "http://localhost:8787/oauth/introspect",
  "jwks_uri": "http://localhost:8787/.well-known/jwks.json",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none"],
  "registration_endpoint_auth_methods_supported": ["none"],
  "client_id_metadata_document_supported": true,
  "client_id_metadata_document_required": false
}
```

`client_id_metadata_document_supported`는 CIMD spec 표준화 진행 중 권장 필드명이다 (ADR-036). client_id로 HTTPS URL을 받았을 때 CIMD flow가 활성됨을 알린다. spec 호환 client가 인식할 수 있도록 custom 필드(`cimd_supported`) 대신 표준 권장 필드명을 사용한다.

static_bearer mode에서는 위 endpoint 모두 404 또는 503 반환.

### 2.5 initialize
Request:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": { "name": "claude-code", "version": "x.y.z" }
  }
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-06-18",
    "capabilities": {
      "tools": { "listChanged": true }
    },
    "serverInfo": { "name": "CoreMCP", "version": "1.0.0" }
  }
}
```

Response header: `Mcp-Session-Id: <uuid>`

미선언 capabilities: `resources`, `prompts`, `logging`, `completions`, `sampling`, `elicitation`. downstream에서 이들 요청 시 -32601 반환.

Protocol version 협상 (ADR-029):
- request `protocolVersion`이 `2025-11-25` → response 동일
- request `2025-06-18` → response 동일
- 미지원 미래 버전 → response에 지원 가능한 최신(`2025-11-25`) + 로그 warning
- 누락 → 2025-06-18 가정 (Claude Code 호환)

2025-11-25 추가 capabilities 처리:
- `tasks` 실험 capability: 미선언 (downstream 요청 시 -32601)

### 2.6 tools/list
Request (no cursor):
```json
{ "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {} }
```

Request with cursor:
```json
{ "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": { "cursor": "eyJvZmZzZXQiOjUwfQ" } }
```

Response:
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "github.create_issue",
        "title": "Create GitHub Issue",
        "description": "Create a new issue in a connected GitHub repository.",
        "icons": [
          {
            "src": "https://github.com/icon.svg",
            "mimeType": "image/svg+xml",
            "sizes": "48x48"
          }
        ],
        "inputSchema": {
          "type": "object",
          "properties": {
            "repo": { "type": "string" },
            "title": { "type": "string" },
            "body": { "type": "string" }
          },
          "required": ["repo", "title"]
        },
        "annotations": {
          "destructiveHint": false,
          "readOnlyHint": false,
          "idempotentHint": false,
          "openWorldHint": true
        }
      }
    ],
    "nextCursor": null
  }
}
```

페이지 크기: 기본 100, 최대 500 (개인 사용 시 보통 1 페이지로 끝).

`icons`는 MCP 2025-11-25 tool top-level optional field. 각 icon은 `{src, mimeType, sizes?}` 형식 (HTML `<img>` 표준 align). CoreMCP는 downstream에서 받은 icons를 `service_tools.icons_json`에 저장하고 tools/list에서 top-level로 노출한다. `annotations` 안에 두지 않는다. 정책상 데이터 size 32KB cap, content-type allowlist (image/png, image/webp, image/svg+xml).

JSON Schema dialect:
- inputSchema 응답은 2020-12 dialect 가정. downstream이 draft-07이면 그대로 forward (검증 client 측 책임).

### 2.7 tools/call
Request:
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "github.create_issue",
    "arguments": { "repo": "me/personal", "title": "Test", "body": "..." }
  }
}
```

Success Response:
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{ "type": "text", "text": "Issue created: https://github.com/me/personal/issues/1" }],
    "isError": false,
    "_meta": { "coremcp": { "invocation_id": "inv_..." } }
  }
}
```

Tool-level Error:
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{ "type": "text", "text": "GitHub credential expired. Reconnect in CoreMCP Settings." }],
    "isError": true,
    "_meta": {
      "coremcp": {
        "error_code": "credential_expired",
        "connect_url": "http://localhost:3000/services/github/credential"
      }
    }
  }
}
```

Protocol Error:
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "error": {
    "code": -32602,
    "message": "Invalid tool arguments",
    "data": { "coremcp_error_code": "invalid_arguments" }
  }
}
```

### 2.7.1 Error 분류 (ADR-034)

| 상황 | 처리 |
|---|---|
| unknown tool name (alias 없음) | JSON-RPC `-32602 Invalid params` |
| malformed params (schema 위반) | JSON-RPC `-32602` |
| tool 내부 input 검증 실패 (downstream이 isError 반환) | result.isError=true forward |
| downstream timeout / 5xx / business error | result.isError=true |
| auth/scope 부족 | HTTP 401/403 |

원칙: protocol level 오류는 JSON-RPC error, 도구 실행 결과 오류는 result.isError. 2025-11-25 spec guidance와 일치.

### 2.8 notifications (server → client)
SSE stream(GET /mcp)으로 전달:
- `notifications/tools/list_changed`: toolbox 변경 / schema 변경 시 emit
- `notifications/progress`: downstream → CoreMCP → client forward
- `notifications/cancelled`: downstream cancel forward

### 2.9 ping
Request:
```json
{ "jsonrpc": "2.0", "id": 4, "method": "ping" }
```
Response: `{ "jsonrpc": "2.0", "id": 4, "result": {} }`

### 2.10 DELETE /mcp
Mcp-Session-Id에 해당하는 session 종료. response 204 No Content.

### 2.11 OAuth Client Registration (AUTH_MODE=oauth 활성 시)

CoreMCP는 OAuth client 등록을 다음 우선순위로 처리한다 (ADR-036):

1. **Pre-registered**: `oauth_clients` 테이블에 본인이 미리 등록한 client.
2. **CIMD (Client ID Metadata Documents)**: client_id가 HTTPS URL인 경우 fetch + 검증 + 캐시.
3. **DCR (Dynamic Client Registration, RFC 7591)**: 위 둘 모두 미해당 시 fallback.

활성 시 노출되는 endpoint:
```http
GET  /.well-known/oauth-authorization-server
GET  /.well-known/jwks.json
GET  /oauth/authorize    (PKCE S256 mandatory, Resource Indicator(RFC 8707) 강제)
POST /oauth/token        (JWT RS256 token format)
POST /oauth/revoke       (RFC 7009)
POST /oauth/introspect   (RFC 7662, internal)
POST /oauth/register     # DCR fallback
```

본인용이므로 single-user authorize는 자동 승인 페이지로 단순화 가능. PKCE S256 강제.

#### 2.11.1 CIMD Discovery / Validation / Cache Flow

client가 authorize 요청에 `client_id`로 HTTPS URL을 보내면 CoreMCP가 해당 URL을 fetch한다:

```http
GET https://client.example.com/.well-known/oauth-client
Accept: application/json
```

응답 (client metadata):
```json
{
  "client_id": "https://client.example.com/.well-known/oauth-client",
  "client_name": "ChatGPT Custom MCP",
  "redirect_uris": ["https://chatgpt.com/oauth/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none",
  "scope": "mcp:tools.read mcp:tools.call"
}
```

CoreMCP 검증:
- HTTPS 강제 (CIMD URL)
- SSRF guard 통과 (06-security-auth §7.5)
- response size ≤ 32KB
- content-type: `application/json` 또는 `application/json; charset=<utf-8|utf8|UTF-8>` 등 charset 파라미터 포함 허용
- redirect_uris의 host가 client_id URL의 host와 일치 (또는 known good list)
- grant_types / response_types가 CoreMCP 지원 범위 내
- code_challenge_methods 등은 검증 시 명시
- **fetched metadata의 `client_id` 필드가 요청한 URL과 byte-exact 일치** (case-sensitive)

캐싱:
- in-memory cache (P3 MVP), TTL 1h
- cache key: `cimd:<client_id_url>`
- 캐시 hit 시 fetch 생략
- DB 영구 저장은 옵션 (05-database-schema §13 OAuth 테이블 참조)
- HTTP cache 정책: TTL 1h **fixed** (CoreMCP 정책, downstream Cache-Control / Expires 무시) — 외부 cache 헤더 신뢰 시 무한 fetch 또는 stale 위험 (ADR-036)

#### 2.11.2 CIMD Fetch Error 처리

| 상황 | 응답 |
|---|---|
| fetch timeout (>5s) | 400 invalid_client_metadata |
| response > 32KB | 400 invalid_client_metadata |
| content-type ≠ application/json | 400 invalid_client_metadata |
| JSON parse 실패 | 400 invalid_client_metadata |
| metadata 필수 필드 누락 | 400 invalid_client_metadata |
| SSRF guard 차단 (private IP 등) | 400 unsafe_client_id |
| 5xx from CIMD URL | 503 cimd_unavailable + retry hint |
| fetched metadata의 client_id != 요청 URL | 400 client_id_mismatch |

#### 2.11.3 POST /oauth/register (DCR, RFC 7591 — Fallback)

CIMD 미지원 client용. ChatGPT/Cursor 일부 버전이 DCR 사용.

Request:
```json
{
  "client_name": "Generic MCP Client",
  "redirect_uris": ["http://localhost:54321/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none",
  "scope": "mcp:tools.read mcp:tools.call"
}
```

Response (registered):
```json
{
  "client_id": "cmcp_oauth_client_xxxx",
  "client_id_issued_at": 1747000000,
  "redirect_uris": ["http://localhost:54321/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_method": "none",
  "scope": "mcp:tools.read mcp:tools.call"
}
```

Rate limit: 10/hour/IP. PKCE S256 mandatory.

#### 2.11.4 Lookup 우선순위 구현

```python
def resolve_client(client_id: str) -> ClientRecord:
    # 1. Pre-registered
    if row := db.find_oauth_client(client_id):
        return row
    # 2. CIMD (HTTPS URL)
    if is_https_url(client_id):
        if cached := cimd_cache.get(client_id):
            return cached
        metadata = fetch_cimd(client_id)  # SSRF guard + validation
        cimd_cache.set(client_id, metadata, ttl=3600)
        return metadata
    # 3. DCR result (prefix cmcp_oauth_client_*)
    if row := db.find_dcr_client(client_id):
        return row
    raise InvalidClient()
```

#### 2.11.5 GET /oauth/authorize, POST /oauth/token, POST /oauth/revoke, POST /oauth/introspect

- `GET /oauth/authorize`  (PKCE S256 mandatory, Resource Indicator(RFC 8707) 강제)
- `POST /oauth/token`     (JWT RS256 token format)
- `POST /oauth/revoke`    (RFC 7009)
- `POST /oauth/introspect` (RFC 7662, internal)

OAuth 2.1 spec에 따라 authorize endpoint는 GET 표준. 상세는 06-security-auth §3.2 시리즈 참조.

---

## 3. Admin REST API

### 3.1 Common
Headers:
```http
# /v1/* admin endpoint
Authorization: Bearer <coremcp_admin_token>     # cmcp_admin_*

# /mcp endpoint
Authorization: Bearer <coremcp_client_token>    # cmcp_client_* (권장)
Authorization: Bearer <coremcp_admin_token>     # admin token도 fallback 허용 (ADR-030 §3.4)

Content-Type: application/json
X-Request-Id: <uuid>
Idempotency-Key: <uuid>   # write 권장
```

Response headers:
```http
X-Request-Id: <echo>
X-RateLimit-Limit: ...
X-RateLimit-Remaining: ...
```

### 3.2 Error Format
```json
{
  "error": {
    "code": "validation_failed",
    "message": "MCP endpoint validation failed",
    "details": { "stage": "tools_list", "reason": "HTTP 401 from downstream" }
  },
  "request_id": "req_..."
}
```

### 3.3 Pagination
list endpoint 표준:
- `?cursor=<opaque>&limit=<int>` (기본 50, 최대 500)
- 응답에 `next_cursor` (null = 끝)

---

## 4. User API

### GET /v1/me
Response:
```json
{
  "id": "usr_local",
  "email": "me@local",
  "name": "Personal",
  "locale": "ko",
  "default_toolbox_id": "tbx_...",
  "bootstrap_completed_at": "2026-05-11T00:00:00Z",
  "created_at": "..."
}
```

bootstrap: 최초 실행 시 자동 생성.

### PATCH /v1/me
수정 가능: `name`, `locale`.

---

## 5. MCP Service API

### POST /v1/mcp-services
Request:
```json
{
  "name": "GitHub MCP",
  "slug": "github",
  "description": "GitHub issue tools",
  "endpoint_url": "https://api.example.com/mcp",
  "auth_type": "bearer_token",
  "credential": { "type": "bearer_token", "value": "ghp_xxx" }
}
```

응답:
```json
{
  "id": "svc_...",
  "slug": "github",
  "status": "validating",
  "validation_job_id": "job_..."
}
```

credential 평문은 즉시 vault로, DB는 secret_ref만 저장.

### GET /v1/mcp-services
Query: `?status=active&cursor=&limit=`
Response:
```json
{
  "items": [{
    "id": "svc_...",
    "name": "GitHub MCP",
    "slug": "github",
    "status": "active",
    "tool_count": 6,
    "last_validated_at": "...",
    "updated_at": "..."
  }],
  "next_cursor": null
}
```

### GET /v1/mcp-services/{id}
service detail + cached tools count + credential status (값 없음).

### PATCH /v1/mcp-services/{id}
수정 가능: `name`, `description`, `slug`(rename 시 alias 처리), `disabled`(true/false).

### DELETE /v1/mcp-services/{id}
soft-delete. 202 Accepted.

### POST /v1/mcp-services/{id}/validate
async re-validation 트리거.
```json
{ "job_id": "job_...", "status": "queued" }
```

### POST /v1/mcp-services/{id}/refresh-tools
schema 강제 재캐싱.

### GET /v1/mcp-services/{id}/validation-report
```json
{
  "service_id": "svc_...",
  "status": "success",
  "stages": [
    { "name": "url_safety_check", "status": "success", "latency_ms": 2 },
    { "name": "mcp_initialize", "status": "success", "latency_ms": 120 },
    { "name": "tools_list", "status": "success", "latency_ms": 260 },
    { "name": "metadata_scan", "status": "success", "warnings": [] }
  ],
  "tools_found": 6,
  "warnings": []
}
```

### GET /v1/mcp-services/{id}/tools
```json
{
  "items": [{
    "id": "tool_...",
    "original_name": "create_issue",
    "exposed_name": "github.create_issue",
    "description": "...",
    "input_schema": {...},
    "output_schema": null,
    "annotations": {...},
    "schema_hash": "sha256:...",
    "status": "active"
  }]
}
```

### GET /v1/mcp-services/{id}/health
```json
{
  "service_id": "svc_...",
  "last_validation": "...",
  "last_call": "...",
  "error_rate_24h": 0.0,
  "stale_cache": false
}
```

---

## 6. Credential API

### PUT /v1/mcp-services/{id}/credential
Request:
```json
{
  "credential_type": "api_key_header",
  "header_name": "X-API-Key",
  "secret": "sk_xxx"
}
```

Response:
```json
{
  "status": "connected",
  "masked": "sk_••••xxxx",
  "updated_at": "..."
}
```

### GET /v1/mcp-services/{id}/credential
값 없이 status / masked / last_rotated만.

### POST /v1/mcp-services/{id}/credential/rotate
새 secret 입력 → validation → 성공 시 교체.

### DELETE /v1/mcp-services/{id}/credential
credential 삭제. service status → auth_required.

---

## 7. Toolbox API

### GET /v1/toolboxes
```json
{
  "items": [{
    "id": "tbx_...",
    "name": "Default",
    "is_default": true,
    "item_count": 3
  }]
}
```

### POST /v1/toolboxes
신규 toolbox. body: `{ "name": "..." }`.

### GET /v1/toolboxes/{id}
detail + items[] + tools count.

### POST /v1/toolboxes/{id}/items
```json
{ "service_id": "svc_...", "enabled": true }
```

### PATCH /v1/toolboxes/{id}/items/{item_id}
`{ "enabled": false }`

### DELETE /v1/toolboxes/{id}/items/{item_id}
soft-delete.

### POST /v1/toolboxes/{id}/items/bulk
여러 service 한 번에 추가/삭제 (편의).

---

## 8. External Connection API

### GET /v1/external-connections
```json
{
  "items": [{
    "id": "ext_...",
    "client_type": "codex_cli",
    "client_name": "Codex CLI exec (local)",
    "status": "active",
    "last_used_at": "...",
    "created_at": "..."
  }]
}
```

### POST /v1/external-connections
client 수동 등록 (정적 bearer 모드에서 metadata 저장용).
```json
{ "client_type": "codex_cli", "client_name": "Codex CLI exec (local)" }
```

### POST /v1/external-connections/one-time-token
OpenClaw 등 OAuth 미지원 client 연결용.
Request:
```json
{
  "client_type": "openclaw",
  "toolbox_id": "tbx_...",
  "requested_scopes": ["mcp:tools.read", "mcp:tools.call"]
}
```
Response:
```json
{
  "token": "cmcp_otk_...",
  "expires_at": "2026-05-11T00:10:00Z",
  "connection_prompt": "Connect CoreMCP using this one-time token: cmcp_otk_..."
}
```

### POST /v1/external-connections/exchange
Request:
```json
{
  "one_time_token": "cmcp_otk_...",
  "client_type": "openclaw",
  "client_name": "OpenClaw on MacBook"
}
```
Response:
```json
{
  "access_token": "cmcp_client_<derived>",
  "expires_in": null,
  "connection_id": "ext_..."
}
```

note: 정적 bearer 모드에서는 access_token이 connection별 `cmcp_client_*` token으로 발급된다 (ADR-030). OAuth 모드에서는 JWT.

### DELETE /v1/external-connections/{id}
revoke. 향후 해당 connection의 access_token은 invalid.

---

## 9. Logs API

### GET /v1/tool-invocations
Query: `?service_id=&exposed_tool_name=&status=&from=&to=&cursor=&limit=`
Response:
```json
{
  "items": [{
    "id": "inv_...",
    "request_id": "req_...",
    "service_id": "svc_...",
    "exposed_tool_name": "github.create_issue",
    "status": "success",
    "latency_ms": 820,
    "downstream_latency_ms": 700,
    "error_code": null,
    "created_at": "..."
  }],
  "next_cursor": null
}
```

### GET /v1/tool-invocations/{id}
detail (input_size, output_size, error_message 등).

### GET /v1/audit-logs
Query: `?action=&resource_type=&from=&to=&cursor=&limit=`
Response:
```json
{
  "items": [{
    "id": "aud_...",
    "action": "service.create",
    "resource_type": "mcp_service",
    "resource_id": "svc_...",
    "ip": "127.0.0.1",
    "user_agent": "...",
    "metadata": {},
    "created_at": "..."
  }],
  "next_cursor": null
}
```

---

## 10. Playground API

본인 디버깅용 — 도구 직접 호출.

### POST /v1/playground/tools/call
```json
{
  "exposed_name": "github.create_issue",
  "arguments": { "repo": "me/test", "title": "test" }
}
```
Response: tool_call 결과 그대로.

### GET /v1/playground/tools/list
현재 default toolbox의 tool catalog 미리보기.

---

## 11. Jobs API

### GET /v1/jobs/{id}
async job 상태:
```json
{
  "id": "job_...",
  "kind": "service_validation",
  "status": "running|success|failed|queued",
  "progress": 0.6,
  "result": null,
  "error": null,
  "started_at": "...",
  "finished_at": null
}
```

---

## 12. Settings API

### GET /v1/settings
```json
{
  "admin_token_masked": "cmcp_admin_••••abcd",
  "client_token_count": 2,
  "auth_mode": "static_bearer",
  "oauth_enabled": false,
  "secret_backend": "keychain",
  "tailscale_enabled": false,
  "cache_backend": "memory"
}
```

### POST /v1/settings/admin-token/rotate
admin token 회전. 새 정적 token 생성 → 응답 본문에 평문 1회 노출 (재표시 안 함):
```json
{ "new_token": "cmcp_admin_<new>", "expires_at": null }
```

기존 admin token은 즉시 invalid.

### POST /v1/settings/client-tokens
Request:
```json
{
  "external_connection_id": "ext_...",
  "name": "Mac mini Claude Code",
  "scopes": ["mcp:tools.read", "mcp:tools.call"]
}
```

Response (1회만 평문 노출):
```json
{
  "id": "pat_...",
  "token": "cmcp_client_xxxxxxxxxxxxxxxxxxxxxxxx",
  "token_prefix": "cmcp_client_xxxxxx",
  "expires_at": null
}
```

### GET /v1/settings/client-tokens
List (masked만):
```json
{
  "items": [
    {
      "id": "pat_...",
      "external_connection_id": "ext_...",
      "token_prefix": "cmcp_client_xxxxxx",
      "scopes": ["mcp:tools.read", "mcp:tools.call"],
      "last_used_at": "...",
      "created_at": "...",
      "revoked_at": null
    }
  ]
}
```

### DELETE /v1/settings/client-tokens/{id}
revoke. 202 Accepted.

---

## 13. Health / Metrics

### GET /health
`{ "status": "ok" }`

### GET /ready
DB / Vault / (옵션) Redis 점검.

### GET /live
process liveness.

### GET /metrics (옵션, METRICS_ENABLED=true)
Prometheus format.

---

## 14. API Security Rules

1. 모든 `/v1/*` mutating endpoint는 bearer 검증.
2. service endpoint URL은 등록 전 SSRF guard 통과.
3. credential value는 응답에 절대 포함하지 않음.
4. audit log에 raw body 저장 금지.
5. response body 5MB cap, request body 1MB cap.
6. mutating endpoint에 Idempotency-Key 권장.
7. list endpoint는 cursor pagination.
8. `/mcp` POST/GET/DELETE는 단일 Authorization 정책 (헤더 외 쿼리/바디에서 token 미허용).
9. CORS: localhost와 Tailscale 도메인만 허용.
10. Tailscale 외부 노출 시 HTTPS 강제 (Caddy 또는 Tailscale Serve).
11. AUTH_MODE는 환경 변수로만 변경 가능. UI에서 즉시 전환은 미허용 (재시작 필요).
12. `cmcp_admin_*` 토큰은 DB에 절대 저장하지 않는다 (파일 + chmod 600) (ADR-030).
13. `cmcp_client_*` 토큰은 `personal_access_tokens.token_hash`에만 저장한다. 평문은 발급 응답에서 1회 노출 (ADR-030).
14. OAuth metadata는 RFC 9728(Protected Resource)과 RFC 8414(Authorization Server) 응답을 분리한다.
15. icons metadata는 tool top-level이며 size 32KB cap.
16. CIMD URL fetch는 SSRF guard 통과 필수, 응답 size 32KB cap, content-type application/json만 허용 (ADR-036, 06-security-auth §7.5).
17. OAuth client lookup 우선순위: pre-registered > CIMD > DCR fallback.

---

## 15. OpenAPI

FastAPI는 `/openapi.json` 자동 생성. dev 모드 `/docs` 활성. production daemon은 `/docs` 비활성 권장.
