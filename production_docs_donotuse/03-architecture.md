# CoreMCP Architecture

문서 버전: v0.1

---

## 1. Architecture Goals

CoreMCP는 다음 아키텍처 목표를 가진다.

1. 하나의 remote MCP endpoint로 여러 downstream MCP를 사용할 수 있다.
2. 사용자별 toolbox에 따라 tool catalog가 달라진다.
3. CoreMCP access token과 downstream credential은 분리된다.
4. downstream MCP 장애가 CoreMCP 전체 장애로 전파되지 않는다.
5. marketplace, team workspace, policy engine으로 확장 가능해야 한다.

---

## 2. Logical Architecture

```text
+------------------------------------------------------+
| External AI Clients                                  |
| Claude Code | Claude | ChatGPT | OpenClaw | Cursor   |
+-----------------------------+------------------------+
                              |
                              | MCP Streamable HTTP
                              | OAuth/Bearer/Connection Token
                              v
+------------------------------------------------------+
| CoreMCP Edge                                         |
| - /mcp endpoint                                      |
| - OAuth protected resource metadata                  |
| - auth middleware                                    |
| - session manager                                    |
| - request parser                                     |
+-----------------------------+------------------------+
                              |
                              v
+------------------------------------------------------+
| CoreMCP Domain Core                                  |
| - user resolver                                      |
| - toolbox resolver                                   |
| - tool catalog builder                               |
| - alias resolver                                     |
| - policy checker                                     |
| - downstream proxy executor                          |
| - audit logger                                       |
+-----------------------------+------------------------+
                              |
            +-----------------+------------------+
            |                                    |
            v                                    v
+--------------------------+        +---------------------------+
| Data Layer               |        | Downstream MCP Services   |
| PostgreSQL               |        | Remote MCP server A       |
| Redis                    |        | Remote MCP server B       |
| KMS/Vault                |        | Internal MCP server       |
+--------------------------+        +---------------------------+
```

---

## 3. Deployment Architecture

### 3.1 MVP

```text
Browser / MCP Client
   |
Cloudflare / WAF
   |
Load Balancer
   |
ASGI App: API + MCP Gateway
   |        \
   |         -> Redis
   |         -> PostgreSQL
   |         -> KMS/Vault
   |
Worker
```

MVP에서는 API 서버와 MCP Gateway를 같은 배포 단위로 둔다. `/mcp` traffic이 증가하면 별도 service로 분리한다.

### 3.2 Production Target

```text
Web App CDN
   |
API Gateway ----------------------+
   |                              |
Admin/API Service                 MCP Gateway Service
   |                              |
PostgreSQL Primary/Replica        Redis Cluster
   |                              |
Worker Pool                       Secret Vault/KMS
   |                              |
Validation Sandbox/Egress Proxy --+--> Downstream MCP
```

---

## 4. Component Details

### 4.1 MCP Gateway

책임:

- MCP Streamable HTTP 준수
- JSON-RPC method dispatch
- OAuth resource server enforcement
- session id generation/validation
- user toolbox tool catalog exposure
- tools/call proxy

주요 클래스/모듈:

```text
McpHttpController
McpAuthMiddleware
McpSessionManager
McpJsonRpcDispatcher
McpToolCatalogService
McpToolCallRouter
McpErrorMapper
```

### 4.2 Service Registry

책임:

- downstream MCP service CRUD
- URL validation
- auth type metadata
- visibility/status 관리

상태:

```text
draft
validating
active
error
disabled
review_pending
public
rejected
```

### 4.3 Validation Service

책임:

- endpoint reachability
- MCP initialize
- tools/list
- schema validation
- security scan
- validation report 저장

Validation stages:

```text
url_safety_check
http_reachability
mcp_initialize
tools_list
schema_validation
tool_metadata_scan
credential_check
```

### 4.4 Tool Catalog Builder

책임:

- service_tools 조회
- toolbox_items 적용
- policy filtering
- exposed tool schema 생성
- description augmentation
- name collision 방지

Description augmentation 예:

```text
[Service: GitHub]
Create a new GitHub issue in an authorized repository.
```

단, 과도한 prompt 조작 문구를 추가하지 않는다.

### 4.5 Proxy Executor

책임:

- downstream endpoint selection
- credential resolution
- HTTP client 호출
- JSON-RPC request 생성
- timeout/retry
- response normalization
- invocation log

모듈:

```text
DownstreamMcpClient
CredentialResolver
ProxyRequestBuilder
ProxyResponseNormalizer
ProxyTimeoutPolicy
```

### 4.6 Credential Vault

책임:

- secret encryption
- credential masking
- rotation
- revoke
- expiry monitoring

Secret storage options:

1. KMS envelope encryption + DB encrypted blob
2. HashiCorp Vault
3. Cloud provider secret manager

MVP 권장:

```text
KMS envelope encryption + encrypted DB column
```

### 4.7 Policy Checker

MVP checks:

```text
user owns toolbox?
toolbox item enabled?
service active?
tool active?
credential connected?
scope allowed?
rate limit ok?
```

Phase 3+:

```text
workspace role
plan quota
tool risk level
write action approval
admin allowlist/denylist
```

---

## 5. Sequence Diagrams

### 5.1 Service Registration

```text
User -> Web App: Submit MCP endpoint
Web App -> API: POST /v1/mcp-services
API -> UrlSafetyChecker: validate URL
API -> DB: create service(status=validating)
API -> Worker: enqueue validation job
Worker -> Downstream MCP: initialize
Worker -> Downstream MCP: tools/list
Worker -> DB: save service_tools + validation report
Worker -> DB: service(status=active/error)
Web App -> API: poll validation status
```

### 5.2 Claude Code Connection

```text
User -> Web App: Open connection guide
Web App -> User: claude mcp add command
User -> Claude Code: register CoreMCP URL
Claude Code -> CoreMCP: GET/POST /mcp without/with auth
CoreMCP -> Claude Code: 401 + WWW-Authenticate metadata URL if needed
Claude Code -> Auth Server: OAuth flow
Auth Server -> Claude Code: access token
Claude Code -> CoreMCP: initialize with bearer token
CoreMCP -> Claude Code: InitializeResult + Mcp-Session-Id
```

### 5.3 tools/list

```text
Client -> CoreMCP /mcp: tools/list
CoreMCP -> Auth: validate token
CoreMCP -> DB: resolve user default toolbox
CoreMCP -> DB: toolbox_items + service_tools + aliases
CoreMCP -> Policy: filter
CoreMCP -> Client: tools list
```

### 5.4 tools/call

```text
Client -> CoreMCP /mcp: tools/call github.create_issue
CoreMCP -> Auth: validate token
CoreMCP -> DB: alias lookup
CoreMCP -> DB: toolbox membership check
CoreMCP -> DB/Vault: credential resolve
CoreMCP -> Downstream MCP: tools/call create_issue
Downstream MCP -> CoreMCP: result/error
CoreMCP -> DB: tool_invocation log
CoreMCP -> Client: result/error
```

---

## 6. Data Flow Boundaries

### 6.1 CoreMCP Token Boundary

CoreMCP access token은 다음에만 사용된다.

- CoreMCP user identification
- CoreMCP tool authorization
- CoreMCP external client session

사용 금지:

- downstream MCP Authorization header
- logs
- query string
- tool arguments

### 6.2 Downstream Credential Boundary

Downstream credential은 다음에만 사용된다.

- CoreMCP server-side downstream MCP request

사용 금지:

- browser exposure
- external AI client exposure
- logs
- audit raw body

---

## 7. Failure Isolation

### 7.1 Downstream tools/list 실패

- 기존 cache가 있으면 stale로 표시하고 계속 사용
- cache가 없으면 해당 service 제외
- service health status 갱신

### 7.2 Downstream tools/call 실패

- JSON-RPC error로 변환
- invocation log에 downstream_error 저장
- 사용자에게 service reconnect/credential check 안내 가능

### 7.3 Auth server 장애

- 기존 valid token 검증이 local JWT로 가능하면 계속 처리
- introspection 의존 시 degraded mode

### 7.4 DB 장애

- tools/list/call 불가
- health check fail
- read replica fallback은 Phase 3

---

## 8. Scaling Plan

### 8.1 Short Term

- in-memory tool catalog cache + Redis invalidation
- async httpx client
- worker based validation

### 8.2 Mid Term

- per-user materialized tool catalog
- MCP Gateway separate autoscaling
- egress proxy pool
- rate limit by user/workspace/service

### 8.3 Long Term

- multi-region read cache
- regional MCP Gateway edges
- marketplace indexing/search
- dedicated validation sandbox

---

## 9. Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Transport | Streamable HTTP first | Remote/cloud MCP 표준 방향 |
| Product model | Toolbox first | 사용자 가치가 명확함 |
| Tool exposure | Proxy mode | LLM tool selection에 유리 |
| Downstream auth MVP | bearer/api-key vault | delegated OAuth보다 빠름 |
| Public marketplace | later | moderation/security 복잡도 큼 |
| Stdio hosting | excluded | RCE/security risk 큼 |
| Session auth | forbidden | session id는 인증 수단 아님 |
