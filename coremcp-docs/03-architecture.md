# CoreMCP Architecture (Personal)

문서 버전: v1.0
작성일: 2026-05-11

---

## 1. Architecture Goals

1. 하나의 `/mcp` endpoint로 여러 downstream MCP를 사용한다.
2. CoreMCP access token과 downstream credential을 분리한다.
3. downstream 장애가 CoreMCP 전체 장애로 전파되지 않는다.
4. 단일 프로세스로 출시하되, multi-process 확장 가능하도록 모듈 경계를 명확히 둔다.
5. SQLite ↔ PostgreSQL, in-memory ↔ Redis, BackgroundTasks ↔ Arq 전환 비용이 낮도록 설계한다.

## 2. Logical Architecture

```text
+--------------------------------------------+
| External AI Clients                        |
| Codex CLI exec / Claude Code (optional)    |
| / OpenClaw / (optional ChatGPT/Cursor)     |
+----------------------+---------------------+
                       |
                       | MCP Streamable HTTP
                       | + Bearer / OAuth (옵션)
                       v
+--------------------------------------------+
| CoreMCP Edge                               |
| - /mcp POST/GET/DELETE                     |
| - /.well-known/oauth-protected-resource    |
| - bearer auth middleware                   |
| - session manager (in-memory dict)         |
| - request parser                           |
+----------------------+---------------------+
                       |
                       v
+--------------------------------------------+
| CoreMCP Domain                             |
| - user resolver (single user)              |
| - toolbox resolver                         |
| - tool catalog builder                     |
| - alias resolver                           |
| - policy checker (간소화)                  |
| - proxy executor                           |
| - validation worker (BackgroundTasks)      |
| - audit / invocation logger                |
+----------------------+---------------------+
                       |
       +---------------+----------------+
       |                                |
       v                                v
+----------------+              +----------------+
| Storage        |              | Downstream MCP |
| SQLite / PG    |              | (HTTP)         |
| Keychain       |              | - GitHub MCP   |
| Files          |              | - Notion MCP   |
+----------------+              | - custom MCP   |
                                +----------------+
```

## 3. Process Topology

### 3.1 MVP: 단일 프로세스
```text
launchd → uvicorn coremcp.main:app
  ├── FastAPI HTTP server
  ├── BackgroundTasks (validation, refresh)
  ├── in-memory caches
  └── SQLite via aiosqlite
```

### 3.2 옵션: 분리 배포
```text
launchd → uvicorn (API + Gateway)
launchd → arq worker (validation, schedule refresh)
docker  → postgres
docker  → redis
```

## 4. Component Details

### 4.1 MCP Gateway 모듈
```text
McpHttpController        # POST/GET/DELETE /mcp
McpAuthMiddleware        # bearer/JWT 검증
McpSessionManager        # in-memory or Redis
McpJsonRpcDispatcher
InitializeHandler
ToolsListHandler
ToolsCallHandler
PingHandler
NotificationsHandler     # cancelled forward, listChanged emit
McpErrorMapper
McpProtocolMetadata      # /.well-known/*
McpProtocolNegotiator    # 2025-06-18 ↔ 2025-11-25 협상
TokenBoundaryEnforcer    # admin vs client token 분리
```

### 4.2 Service Registry 모듈
```text
McpServiceRepository
McpServiceService
UrlSafetyChecker         # SSRF
ServiceValidationService # BackgroundTask로 호출
ToolSchemaCacheService
ToolMetadataScanner      # poisoning regex + Unicode
```

상태 머신:
```text
draft → validating → active|error
active ↔ disabled
* → deleted (soft)
```

### 4.3 Tool Catalog Builder 모듈
```text
ToolCatalogBuilder
ToolAliasService
ToolCollisionResolver
```

### 4.4 Proxy Executor 모듈
```text
DownstreamMcpClient      # httpx async
CredentialResolver       # vault에서 secret 가져옴
ProxyRequestBuilder
ProxyResponseNormalizer
ProxyTimeoutPolicy
IdempotencyCache
CancellationBridge
```

### 4.5 Credential Vault 모듈
```text
CredentialVault          # backend abstraction
KeychainBackend          # keyring 라이브러리
FernetBackend            # ~/.coremcp/secret.key
SecretMasker             # UI 표시용
```

### 4.6 External Connection 모듈
```text
ExternalConnectionService
ConnectionTokenService   # one-time token
ConnectionTokenHasher
```

### 4.7 Audit / Invocation 모듈
```text
AuditLogger              # background task
InvocationLogger
LogRedactor
```

## 5. Sequence Diagrams

### 5.1 Service Registration
```text
Web → API: POST /v1/mcp-services
API → UrlSafetyChecker: validate
API → DB: insert (status=validating)
API → BackgroundTasks: run_validation(id)
[bg] Worker → Downstream: initialize
[bg] Worker → Downstream: tools/list
[bg] Worker → MetadataScanner: scan
[bg] Worker → DB: insert service_tools + validation_run + alias
[bg] Worker → DB: update service status=active
Web → API: poll GET /v1/jobs/{job_id} or service status
```

### 5.2 Codex CLI exec Connection (Mac mini local)
```text
User: make codex-install && infra/scripts/codex-exec-coremcp.sh "..."
Codex CLI → CoreMCP: POST /mcp initialize (client bearer from COREMCP_CLIENT_TOKEN)
CoreMCP → Auth: verify bearer
CoreMCP → DB: insert mcp_sessions (or in-memory)
CoreMCP → Codex CLI: InitializeResult + Mcp-Session-Id
Codex CLI → CoreMCP: tools/list
CoreMCP → DB+Cache: catalog build
CoreMCP → Codex CLI: tools array
```

### 5.3 tools/call
```text
Codex CLI → CoreMCP: tools/call github.create_issue
CoreMCP → Auth: bearer
CoreMCP → DB: alias lookup → service_tool
CoreMCP → DB: toolbox membership
CoreMCP → DB: service active
CoreMCP → Vault: resolve credential
CoreMCP → Downstream: tools/call create_issue + downstream cred
Downstream → CoreMCP: result
CoreMCP → DB: tool_invocations insert (bg)
CoreMCP → Codex CLI: result
```

### 5.4 listChanged Emission (요약)
```text
User: toolbox에 service 추가
Web → API: POST /v1/toolboxes/{id}/items
API → DB: insert
API → CatalogInvalidator: invalidate user
API → SseEmitter: send notifications/tools/list_changed to active sessions
[active GET /mcp SSE handlers] → MCP client: notification
MCP client → CoreMCP: tools/list (재요청)
```

상세 흐름 및 트리거는 아래 §5.5 참조.

### 5.5 listChanged Emission

```text
User → Web UI: Toolbox에 service 추가 / disable / enable
Web UI → API /v1/toolboxes/.../items: PATCH or POST
API → DB: update toolbox_items
API → CatalogInvalidator: invalidate L1 cache for user_id
API → DomainEventBus: emit ToolboxChanged{user_id}
DomainEventBus → SseEmitter: notify active sessions for user_id
SseEmitter → /mcp GET SSE handler: notifications/tools/list_changed event
MCP client → CoreMCP /mcp: tools/list (재요청)
CoreMCP → MCP client: 새 catalog
```

발생 트리거 (07-mcp-proxy-spec.md §12 참조):
1. toolbox_items add/remove/enable/disable
2. service status active ↔ disabled
3. service_tools.schema_hash 변경 (validation 후)
4. credential 변경으로 service reachable 상태 변경

debounce: 동일 user에 대해 1초당 1회.

### 5.6 Client Token Issue and Revoke

#### 5.6.1 Issue
```text
User → Web UI Settings/Tokens: "+ Generate new client token"
Web UI → API /v1/settings/client-tokens: POST
  body: { external_connection_id, name, scopes }
API → BearerAuth: verify admin token
API → ExternalConnectionRepository: external_connection 존재 확인
API → TokenGenerator: cmcp_client_<random>
API → DB: INSERT personal_access_tokens (token_hash=sha256, status='active')
API → AuditLogger: client_token.issue
API → Web UI: { token: "cmcp_client_..." (1회 평문), token_prefix, id }
Web UI → User: modal에 평문 + Copy 버튼 (모달 닫으면 재조회 불가)
```

#### 5.6.2 Revoke + CASCADE
```text
User → Web UI Connected Clients: external_connection delete or token revoke
Web UI → API /v1/external-connections/{id}: DELETE
API → BearerAuth: verify admin token
API → DB: UPDATE external_connections SET revoked_at=now()
API → DB: ON DELETE CASCADE 또는 trigger로
        UPDATE personal_access_tokens
          SET status='revoked', revoked_at=now()
          WHERE external_connection_id = ?
API → AuditLogger: external_connection.revoke + client_token.revoke
API → ActiveSessions: terminate all sessions for affected tokens
API → Web UI: 202

[다음 /mcp 요청]
Client → /mcp: Authorization: Bearer <revoked_client_token>
McpAuthMiddleware → DB: lookup token_hash
DB: row.status='revoked' → returns None
McpAuthMiddleware → Client: 401 + WWW-Authenticate
```

CASCADE 일관성 CHECK: 05-database-schema §9.3의 `chk_pat_revoked_consistency` 참조 (ADR-030).

### 5.7 CIMD Discovery and Cache (AUTH_MODE=oauth 활성 시)

```text
[ChatGPT 또는 Cursor가 새 OAuth client로 접근]
Client → CoreMCP /oauth/authorize:
  client_id=https://chatgpt.com/.well-known/oauth-client
  resource=http://localhost:8787/mcp
  code_challenge_method=S256

OAuth AS → CimdResolver: resolve client_id (HTTPS URL)
CimdResolver → Cache: lookup cimd:<client_id_url>
Cache: MISS (첫 등장)
CimdResolver → SsrfGuard: validate URL (06-security §7.5)
SsrfGuard → CimdResolver: OK (HTTPS, public IP)
CimdResolver → External HTTPS: GET /well-known/oauth-client
External → CimdResolver: 200 application/json (≤32KB) {redirect_uris, grant_types, ...}
CimdResolver → Validator: check redirect_uris host == client_id host (or known good)
Validator → CimdResolver: OK
CimdResolver → Cache: set cimd:<client_id_url> TTL=1h
CimdResolver → OAuth AS: ClientRecord

[이후 동일 client 재요청]
OAuth AS → CimdResolver: resolve same client_id
CimdResolver → Cache: HIT (TTL 만료 전)
CimdResolver → OAuth AS: ClientRecord (fetch 생략)

[캐시 만료 후]
CimdResolver → Cache: MISS (TTL 1h 경과)
CimdResolver → External HTTPS: GET (재fetch + 재검증)
CimdResolver → Cache: set with new TTL
```

CIMD fetch 실패 시 (timeout/size cap/content-type/SSRF):
```text
CimdResolver → AuditLogger: oauth.cimd_fetch_failed
CimdResolver → OAuth AS: raise InvalidClient
OAuth AS → Client: 400 invalid_client_metadata
```

DCR fallback은 별도 흐름 (POST /oauth/register). ADR-036, 06-security-auth §4.4.2 참조.

### 5.8 Credential Rotation and Vault Re-encryption

```text
User → Web UI Service Detail / Credential: "Rotate"
User → Web UI form: 새 secret 입력
Web UI → API /v1/mcp-services/{id}/credential/rotate: POST
API → BearerAuth: verify admin token
API → VaultBackend (Keychain or Fernet): encrypt new secret
VaultBackend → DB: temporary new secret_ref (status='rotating')
API → ServiceValidationService: validate with new secret (background)
ServiceValidationService → Downstream MCP: initialize + tools/list
Downstream MCP → ServiceValidationService: 200 + tool schemas
ServiceValidationService → DB:
  UPDATE service_credentials
    SET secret_ref = new_ref,
        status = 'connected',
        rotated_at = now(),
        masked_value = new_masked
  WHERE service_id = ?
API → VaultBackend: destroy old secret_ref
API → AuditLogger: credential.rotate
API → SseEmitter: notify if service status changed
API → Web UI: 200 { status, masked, rotated_at }
```

실패 시:
```text
ServiceValidationService → Downstream MCP: initialize/tools/list 실패
ServiceValidationService → DB:
  - 기존 secret_ref 유지
  - 새 secret_ref destroy
  - status='error', last_error_code=auth_failed
API → AuditLogger: credential.rotate_failed
API → Web UI: 400 + error 정보 (rotate 미적용)
```

Vault backend 전환 시 (Keychain → Fernet 또는 역): 06-security-auth §6.2.3 운영 모드 결정표 참조.

## 6. Data Flow Boundaries

### 6.1 CoreMCP token boundary
CoreMCP는 두 종류의 personal token을 사용한다 (ADR-030):
- Admin token: ~/.coremcp/admin-token 파일, /v1/* 및 /mcp fallback
- Client token: personal_access_tokens DB hash, external_connection 단위 발급, /mcp 호출용

두 token 모두 downstream MCP에 절대 전달되지 않는다 (ADR-004).

### 6.2 Downstream credential boundary
- secret_ref만 DB
- 평문은 Keychain/fernet
- logs/audit/invocation에 평문 금지

## 7. Failure Isolation

### 7.1 Downstream tools/list 실패
- stale cache가 있으면 stale로 표시 후 사용
- cache 없으면 해당 service 제외 + warning log
- service health 갱신

### 7.2 Downstream tools/call 실패
- JSON-RPC error로 매핑
- invocation log에 downstream_error 저장
- UI에 reconnect / refresh-tools 안내

### 7.3 Keychain unlock 안 됨
- launchd boot 직후 keychain 잠금 시 발생 가능
- credential resolve 실패 → 503 또는 service_not_connected
- 사용자가 Mac mini login 후 해소

### 7.4 DB 락 (SQLite)
- WAL 모드 사용
- write 동시성 낮으므로 영향 적음
- 락 발견 시 backoff retry

## 8. Scaling Plan (필요 시)

### 8.1 단기: 단일 프로세스
- in-memory cache
- BackgroundTasks
- SQLite

### 8.2 중기: multi-process
- Redis로 cache/session 이동
- Arq worker 분리
- SQLite → PostgreSQL

### 8.3 장기: SaaS 전환
- `production_docs_donotuse/` 참고
- `15-future-saas-migration.md` 절차

## 9. Architecture Decisions (개인 컨텍스트)

| Decision | Choice | Rationale |
|---|---|---|
| Transport | Streamable HTTP | MCP 표준 |
| Product model | Toolbox first | 사용자 가치 명확 |
| Tool exposure | Proxy mode | LLM 선택 자연 |
| Downstream auth | bearer/api_key vault | 빠른 구현 |
| Token format | static bearer 기본, JWT 옵션 | 단일 사용자 |
| DB | SQLite 기본 | 단일 사용자 충분 |
| Cache | in-memory dict | single process |
| Worker | BackgroundTasks | single process |
| Secret | macOS Keychain | OS native |
| Process | single | 운영 단순 |
| Public marketplace | 제외 | 외부 노출 없음 |
| Stdio hosting | 제외 | RCE 위험 |
| Session auth | 금지 | 모든 request bearer |
| Token model | dual: admin file + client DB | revocable per-connection (ADR-030) |
| Protocol versions | 2025-06-18 + 2025-11-25 | Codex CLI/Claude Code 호환 + 최신 (ADR-029) |
| AUTH_MODE | static_bearer default, oauth optional | ChatGPT 등 OAuth 강제 client 시 활성 (ADR-032) |
| OAuth client registration | CIMD first, DCR fallback | brand impersonation 방어 (ADR-036) |

상세는 `13-adr.md`.
