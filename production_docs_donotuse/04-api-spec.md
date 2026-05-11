# CoreMCP API Spec

문서 버전: v0.1

---

## 1. API Groups

CoreMCP API는 두 계층이다.

1. MCP Protocol API: 외부 AI client가 호출하는 `/mcp`
2. Product REST API: 웹앱/콘솔이 호출하는 `/v1/*`

---

## 2. MCP Protocol API

### 2.1 Endpoint

```http
POST /mcp
GET /mcp
DELETE /mcp
```

MVP:

- POST 필수
- GET은 SSE 미지원 시 405 가능
- DELETE는 session termination 지원 권장

Headers:

```http
Authorization: Bearer <coremcp_access_token>
Accept: application/json, text/event-stream
Content-Type: application/json
MCP-Protocol-Version: 2025-06-18
Mcp-Session-Id: <session_id>  # initialize 이후
```

### 2.2 401 Response

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer resource_metadata="https://coremcp.example.com/.well-known/oauth-protected-resource"
```

### 2.3 Protected Resource Metadata

```http
GET /.well-known/oauth-protected-resource
```

Example:

```json
{
  "resource": "https://coremcp.example.com/mcp",
  "authorization_servers": [
    "https://auth.coremcp.example.com"
  ],
  "scopes_supported": [
    "mcp:tools.read",
    "mcp:tools.call",
    "mcp:connections.manage"
  ],
  "bearer_methods_supported": ["header"],
  "resource_documentation": "https://docs.coremcp.example.com/mcp",
  "revocation_endpoint": "https://auth.coremcp.example.com/oauth/revoke",
  "introspection_endpoint": "https://auth.coremcp.example.com/oauth/introspect",
  "registration_endpoint": "https://auth.coremcp.example.com/oauth/register",
  "jwks_uri": "https://auth.coremcp.example.com/.well-known/jwks.json",
  "resource_signing_alg_values_supported": ["RS256"]
}
```

### 2.3.1 Authorization Server Metadata

```http
GET /.well-known/oauth-authorization-server
```

응답 예시:

```json
{
  "issuer": "https://auth.coremcp.example.com",
  "authorization_endpoint": "https://auth.coremcp.example.com/oauth/authorize",
  "token_endpoint": "https://auth.coremcp.example.com/oauth/token",
  "registration_endpoint": "https://auth.coremcp.example.com/oauth/register",
  "revocation_endpoint": "https://auth.coremcp.example.com/oauth/revoke",
  "introspection_endpoint": "https://auth.coremcp.example.com/oauth/introspect",
  "jwks_uri": "https://auth.coremcp.example.com/.well-known/jwks.json",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
  "scopes_supported": [
    "mcp:tools.read",
    "mcp:tools.call",
    "mcp:connections.manage",
    "mcp:profile.read"
  ]
}
```

### 2.3.2 Dynamic Client Registration (RFC 7591)

```http
POST /oauth/register
```

Request:

```json
{
  "client_name": "Claude Code",
  "redirect_uris": ["http://localhost:54321/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none",
  "scope": "mcp:tools.read mcp:tools.call"
}
```

Response:

```json
{
  "client_id": "cmcp_client_...",
  "client_id_issued_at": 1747000000,
  "redirect_uris": ["http://localhost:54321/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_method": "none"
}
```

PKCE S256 mandatory. client_secret은 public client(`none` auth method)에 미발급.

### 2.4 initialize Request

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {
      "name": "claude-code",
      "version": "x.y.z"
    }
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
      "tools": {
        "listChanged": true
      }
    },
    "serverInfo": {
      "name": "CoreMCP",
      "version": "0.1.0"
    }
  }
}
```

Response header:

```http
Mcp-Session-Id: 6c2e32f3-2c9a-4d71-a3de-...
```

### 2.5 tools/list Request

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list",
  "params": {}
}
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
        "inputSchema": {
          "type": "object",
          "properties": {
            "repo": { "type": "string" },
            "title": { "type": "string" },
            "body": { "type": "string" }
          },
          "required": ["repo", "title"]
        }
      }
    ]
  }
}
```

Pagination (2025-06-18):

Request with cursor:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list",
  "params": { "cursor": "eyJvZmZzZXQiOjUwfQ" }
}
```

Response with next page:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [],
    "nextCursor": "eyJvZmZzZXQiOjEwMH0"
  }
}
```

페이지 크기: 기본 50, 최대 200. cursor는 server-issued opaque token.

### 2.6 tools/call Request

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "github.create_issue",
    "arguments": {
      "repo": "acme/app",
      "title": "Bug from Claude Code",
      "body": "Steps to reproduce..."
    }
  }
}
```

Success Response:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Issue created: https://github.com/acme/app/issues/123"
      }
    ],
    "isError": false
  }
}
```

Tool-level Error Response:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "The GitHub service is not connected. Open CoreMCP and connect GitHub."
      }
    ],
    "isError": true,
    "_meta": {
      "coremcp_error_code": "service_not_connected",
      "connect_url": "https://app.coremcp.example.com/services/github/connect"
    }
  }
}
```

Protocol-level Error Response:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "error": {
    "code": -32602,
    "message": "Invalid tool arguments",
    "data": {
      "coremcp_error_code": "invalid_arguments"
    }
  }
}
```

---

## 3. Product REST API

### 3.1 Authentication

Web API는 session cookie 또는 bearer JWT를 사용할 수 있다.

Headers:

```http
Authorization: Bearer <web_api_access_token>
Content-Type: application/json
X-Request-Id: <uuid>
Idempotency-Key: <uuid>  # mutating endpoint 권장
```

응답 헤더 표준:

```http
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1747000060
Retry-After: 32  # 429 응답 시
X-Request-Id: req_...
```

### 3.2 Common Error

```json
{
  "error": {
    "code": "validation_failed",
    "message": "MCP endpoint validation failed",
    "details": {
      "stage": "tools_list",
      "reason": "HTTP 401 from downstream"
    }
  },
  "request_id": "req_..."
}
```

---

## 4. User API

### GET /v1/me

Response:

```json
{
  "id": "usr_123",
  "email": "user@example.com",
  "name": "User",
  "default_toolbox_id": "tbx_123",
  "created_at": "2026-05-11T00:00:00Z"
}
```

### PATCH /v1/me

- name, avatar_url, locale 수정

### DELETE /v1/me

- soft-delete 후 30일 grace period
- 응답 202 Accepted

### POST /v1/me/email/verify/send

### POST /v1/me/email/verify/confirm

### GET /v1/me/sessions

### DELETE /v1/me/sessions/{session_id}

### POST /v1/me/export

- GDPR Art. 20 / 개인정보보호법 §35
- async job, S3 signed URL로 결과 전달

---

## 5. MCP Service API

### POST /v1/mcp-services

Request:

```json
{
  "name": "GitHub MCP",
  "slug": "github",
  "description": "GitHub issue and repository tools",
  "endpoint_url": "https://mcp.example.com/mcp",
  "auth_type": "bearer_token",
  "visibility": "private",
  "credential": {
    "type": "bearer_token",
    "value": "ghp_xxx"
  }
}
```

Response:

```json
{
  "id": "svc_123",
  "name": "GitHub MCP",
  "slug": "github",
  "status": "validating",
  "validation_job_id": "job_123"
}
```

### GET /v1/mcp-services

Query:

```text
?visibility=private&status=active&cursor=...
```

Response:

```json
{
  "items": [
    {
      "id": "svc_123",
      "name": "GitHub MCP",
      "slug": "github",
      "status": "active",
      "visibility": "private",
      "tool_count": 6,
      "updated_at": "2026-05-11T00:00:00Z"
    }
  ],
  "next_cursor": null
}
```

### POST /v1/mcp-services/{service_id}/validate

Response:

```json
{
  "job_id": "job_456",
  "status": "queued"
}
```

### GET /v1/mcp-services/{service_id}/validation-report

Response:

```json
{
  "service_id": "svc_123",
  "status": "success",
  "stages": [
    { "name": "url_safety_check", "status": "success", "latency_ms": 2 },
    { "name": "mcp_initialize", "status": "success", "latency_ms": 120 },
    { "name": "tools_list", "status": "success", "latency_ms": 260 }
  ],
  "tools_found": 6,
  "warnings": []
}
```

### GET /v1/mcp-services/{service_id}/tools

Response:

```json
{
  "items": [
    {
      "id": "tool_123",
      "original_name": "create_issue",
      "exposed_name": "github.create_issue",
      "description": "Create an issue",
      "schema_hash": "sha256:...",
      "status": "active"
    }
  ]
}
```

### GET /v1/jobs/{job_id}

- async validation/refresh job 상태 조회

### GET /v1/mcp-services/{service_id}/credential

- status, masked, last_rotated_at만 반환 (값 없음)

### GET /v1/mcp-services/{service_id}/health

- last validation, last call, error rate

---

## 6. Toolbox API

### GET /v1/toolboxes

Response:

```json
{
  "items": [
    {
      "id": "tbx_123",
      "name": "Default Toolbox",
      "is_default": true,
      "item_count": 3
    }
  ]
}
```

### POST /v1/toolboxes/{toolbox_id}/items

Request:

```json
{
  "service_id": "svc_123",
  "enabled": true
}
```

Response:

```json
{
  "id": "tbi_123",
  "toolbox_id": "tbx_123",
  "service_id": "svc_123",
  "enabled": true
}
```

### PATCH /v1/toolboxes/{toolbox_id}/items/{item_id}

Request:

```json
{
  "enabled": false
}
```

---

## 7. Credential API

### PUT /v1/mcp-services/{service_id}/credential

Request:

```json
{
  "credential_type": "api_key_header",
  "header_name": "X-API-Key",
  "secret": "sk_live_..."
}
```

Response:

```json
{
  "status": "connected",
  "masked": "sk_live_••••1234",
  "updated_at": "2026-05-11T00:00:00Z"
}
```

---

## 8. External Connection API

### POST /v1/external-connections/one-time-token

Request:

```json
{
  "client_type": "openclaw",
  "toolbox_id": "tbx_123",
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
  "access_token": "...",
  "refresh_token": "...",
  "expires_in": 3600,
  "connection_id": "ext_123"
}
```

### GET /v1/external-connections

Response:

```json
{
  "items": [
    {
      "id": "ext_123",
      "client_type": "claude_code",
      "client_name": "Claude Code",
      "status": "active",
      "last_used_at": "2026-05-11T00:00:00Z"
    }
  ]
}
```

### DELETE /v1/external-connections/{connection_id}

Response:

```json
{
  "status": "revoked"
}
```

---

## 9. Logs API

### GET /v1/tool-invocations

Query:

```text
?service_id=svc_123&status=error&from=2026-05-01&to=2026-05-11
```

Response:

```json
{
  "items": [
    {
      "id": "inv_123",
      "request_id": "req_123",
      "service_id": "svc_123",
      "exposed_tool_name": "github.create_issue",
      "status": "success",
      "latency_ms": 820,
      "created_at": "2026-05-11T00:00:00Z"
    }
  ]
}
```

### GET /v1/tool-invocations/{invocation_id}

- 단일 invocation detail

### GET /v1/audit-logs

Query:

```text
?action=service.create&resource_type=mcp_service&from=...&to=...&cursor=...
```

Response:

```json
{
  "items": [
    {
      "id": "aud_...",
      "actor_user_id": "usr_...",
      "action": "service.create",
      "resource_type": "mcp_service",
      "resource_id": "svc_...",
      "ip": "203.0.113.42",
      "created_at": "..."
    }
  ],
  "next_cursor": null
}
```

### POST /v1/audit-logs/export

- async export to S3 (NDJSON), signed URL 응답
- workspace admin 전용

---

## 10. API Security Rules

1. 모든 mutating API는 CSRF 또는 bearer token protection 필요.
2. service endpoint URL은 등록 전에 SSRF guard를 통과해야 한다.
3. credential value는 response에 절대 포함하지 않는다.
4. audit log에 request body 원문 저장 금지.
5. admin API는 workspace role 또는 global admin role 필요.
6. mutating endpoint는 Idempotency-Key 권장.
7. 모든 list endpoint는 cursor pagination.
8. response body는 5MB 초과 시 truncate + warning.
9. API versioning: /v1, /v2 병행 운영 시 12개월 sunset 공지.
10. 401/403 응답은 일관된 JSON error body 사용.
