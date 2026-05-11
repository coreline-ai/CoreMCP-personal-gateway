# CoreMCP Implementation Plan

문서 버전: v0.1

---

## 1. 개발 전략

MVP는 “작동하는 end-to-end path”를 가장 먼저 완성한다.

핵심 path:

```text
User login
 -> register remote MCP
 -> validate tools/list
 -> add to toolbox
 -> Claude Code connects CoreMCP
 -> tools/list
 -> tools/call proxy
```

---

## 2. Recommended Tech Stack

### Backend (확정 — ADR-003, ADR-020)

- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy 2.0 async
- Alembic
- httpx async
- Authlib (or Logto SDK)
- Arq (Redis async worker)
- structlog
- OpenTelemetry SDK

### Frontend (확정)

- Next.js 15+ (App Router)
- TypeScript
- Tailwind CSS
- shadcn/ui
- TanStack Query
- Zod
- next-intl (i18n)

### Infra

- PostgreSQL 15+ (RDS)
- Redis 7+ (ElastiCache)
- AWS KMS (ADR-012)
- Logto self-host (ADR-011)
- Docker / Fly.io or Render (TBD, ADR-020 계열)
- Cloudflare WAF
- Sentry (errors) + OpenTelemetry collector

### Monorepo

- pnpm + Turborepo (확정)

---

## 3. Repository Structure

```text
coremcp/
├── apps/
│   ├── api/
│   │   ├── coremcp/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── db/
│   │   │   ├── auth/
│   │   │   ├── mcp_gateway/
│   │   │   ├── registry/
│   │   │   ├── toolbox/
│   │   │   ├── credentials/
│   │   │   ├── proxy/
│   │   │   ├── validation/
│   │   │   ├── audit/
│   │   │   ├── billing/
│   │   │   ├── compliance/  # right-to-erasure, export
│   │   │   └── api/
│   │   ├── alembic/
│   │   └── tests/
│   ├── worker/                 # Arq worker app
│   │   └── coremcp_worker/
│   └── web/
│       ├── app/
│       ├── components/
│       ├── lib/
│       ├── messages/           # i18n
│       └── tests/
├── packages/
│   ├── shared-types/           # TS + Python (codegen)
│   ├── mcp-test-fixtures/      # OSS — fake downstream MCP servers
│   └── client-profiles/        # ClientProfile data
├── infra/
│   ├── terraform/
│   └── docker/
├── docs/                       # mirror of coremcp-docs/
└── docker-compose.yml
```

---

## 4. Milestone Plan

### 4.0 Milestone Dependency & Parallelization

| Milestone | 의존 | 병렬 가능 | 1인-주 추정 |
|---|---|---|---|
| M0 Bootstrap | — | — | 0.5 |
| M1 Auth/Toolbox | M0 + (D-001 AS 결정) | — | 1 |
| M2 Service Registry | M1 | M3, M5와 부분 병렬 | 1.5 |
| M3 Validation Worker | M2 (schema 정의 후) | M4와 병렬 | 1.5 |
| M4 Toolbox Management | M2 | M3와 병렬 | 1 |
| M5 MCP Gateway Minimal | M0 (fake downstream) | M2 초기 함께 가능 | 2 |
| M6 OAuth | M1 + (D-001 결정) | M5와 부분 병렬 | 2 |
| M7 Downstream Proxy | M2 + M3 + M5 + M6 | — | 2 |
| M8 Claude Code Integration | M7 | — | 1 |
| M9 One-Time Token | M6 | M8과 병렬 | 1.5 |
| M10 Hardening + Beta | M1~M9 모두 | — | 2~4 |

권장 병렬화:

- M2 + M3 + M4: 2주 동시 진행 가능 (2명 분담 시)
- M5 + M6 초기 setup 병렬
- M8 + M9 병렬

Blocking 결정 (M0 종료 전 확정 필수):

- D-001 OAuth AS = Logto (ADR-011)
- D-002 KMS = AWS KMS (ADR-012)
- D-003 백엔드 = FastAPI (ADR-003)
- D-004 Worker = Arq (ADR-020)

### Milestone 0: Project Bootstrap

기간: 2-3일
의존: 없음

Tasks:

- [ ] repo 생성
- [ ] Docker Compose: api, web, postgres, redis
- [ ] FastAPI health check
- [ ] Next.js shell
- [ ] Alembic setup
- [ ] config/env loader
- [ ] CI lint/test skeleton

Exit Criteria:

- `docker compose up`으로 local 실행
- `/health` 200
- web dashboard placeholder 접근 가능

---

### Milestone 1: Auth + User + Toolbox Foundation

기간: 3-5일
의존: M0, ADR-011 결정

Tasks:

- [ ] auth provider 결정 및 연동
- [ ] users table migration
- [ ] workspaces table migration
- [ ] toolboxes table migration
- [ ] first login hook
- [ ] default workspace/toolbox 생성
- [ ] `/v1/me`
- [ ] dashboard user 표시

Exit Criteria:

- 로그인 후 default toolbox가 생성된다.
- `/v1/me`가 user/default_toolbox_id를 반환한다.

---

### Milestone 2: MCP Service Registry

기간: 5-7일
의존: M1

Tasks:

- [ ] mcp_services table
- [ ] service_tools table
- [ ] validation_runs table
- [ ] POST /v1/mcp-services
- [ ] GET /v1/mcp-services
- [ ] URL safety checker
- [ ] credential input mask
- [ ] New MCP Service UI
- [ ] Service List UI

Exit Criteria:

- 사용자가 Remote MCP URL을 등록할 수 있다.
- unsafe URL은 거부된다.

---

### Milestone 3: MCP Validation Worker

기간: 5-7일
의존: M2 schema 정의 완료

Tasks:

- [ ] async validation job
- [ ] downstream MCP initialize client
- [ ] downstream tools/list client
- [ ] tool schema parser
- [ ] schema hash calculator
- [ ] metadata scanner MVP
- [ ] validation report 저장
- [ ] validation report UI

Exit Criteria:

- 등록된 MCP의 tools/list가 DB에 캐시된다.
- validation report에서 단계별 결과가 보인다.

---

### Milestone 4: Toolbox Management

기간: 3-5일
의존: M2 (M3와 병렬 가능)

Tasks:

- [ ] toolbox_items table
- [ ] add service to toolbox API
- [ ] enable/disable item API
- [ ] toolbox detail API
- [ ] My Toolbox UI
- [ ] service card status

Exit Criteria:

- service를 default toolbox에 추가/삭제할 수 있다.
- enabled=false면 tool catalog에서 제외된다.

---

### Milestone 5: MCP Gateway Minimal

기간: 7-10일
의존: M0 (fake downstream으로 M2 진행 중 시작 가능)

Tasks:

- [ ] `/mcp` POST endpoint
- [ ] JSON-RPC parser/serializer
- [ ] initialize handler
- [ ] tools/list handler
- [ ] tools/call handler with fake downstream
- [ ] MCP session manager
- [ ] `Mcp-Session-Id` header
- [ ] `MCP-Protocol-Version` handling
- [ ] error mapper
- [ ] unit tests

Exit Criteria:

- MCP client가 initialize/tools/list/tools/call을 호출할 수 있다.
- fake tool call이 성공한다.

---

### Milestone 6: OAuth Protected MCP

기간: 5-10일
의존: M1, ADR-011 + ADR-022

Tasks:

- [ ] protected resource metadata endpoint
- [ ] authorization server metadata 연결
- [ ] bearer token validation middleware
- [ ] audience/resource validation
- [ ] scope validation
- [ ] 401 `WWW-Authenticate` response
- [ ] external_connections table
- [ ] connected client record

Exit Criteria:

- token 없이 `/mcp` 호출 시 401 + metadata
- valid token으로 initialize/tools/list 가능

---

### Milestone 7: Downstream Proxy Executor

기간: 7-10일
의존: M2 + M3 + M5 + M6

Tasks:

- [ ] tool alias resolver
- [ ] user toolbox membership check
- [ ] credential resolver
- [ ] downstream tools/call client
- [ ] timeout handling
- [ ] response normalization
- [ ] tool_invocations table
- [ ] invocation logging
- [ ] service_not_connected error
- [ ] downstream timeout/error mapping

Exit Criteria:

- Claude Code -> CoreMCP -> downstream MCP tool call 성공
- 실패/성공 로그가 남는다.

---

### Milestone 8: Claude Code Integration

기간: 3-5일
의존: M7

Tasks:

- [ ] connection guide UI
- [ ] Claude Code command generation
- [ ] end-to-end manual test
- [ ] bearer header fallback guide
- [ ] troubleshooting guide
- [ ] integration test script

Exit Criteria:

- Claude Code에서 CoreMCP 하나만 등록해 toolbox tool 호출 가능

---

### Milestone 9: One-Time Token

기간: 5-7일
의존: M6 (M8과 병렬)

Tasks:

- [ ] connection_tokens table
- [ ] one-time token generator
- [ ] hash storage
- [ ] token exchange API
- [ ] external connection creation
- [ ] connected clients UI
- [ ] revoke API
- [ ] audit logs

Exit Criteria:

- 10분 만료 one-time token으로 external connection 생성 가능
- revoke 후 사용 불가

---

### Milestone 10: Hardening + Beta

기간: 7-14일
의존: M1~M9

Tasks:

- [ ] rate limits
- [ ] request/response size limits
- [ ] logs secret redaction
- [ ] SSRF integration tests
- [ ] tool poisoning scanner improvement
- [ ] Sentry/OTel
- [ ] admin metrics
- [ ] backup policy
- [ ] beta onboarding docs

Exit Criteria:

- Private beta users 5-10명 onboard 가능

---

## 5. Backend Module Tasks

각 모듈은 다음 layer 구조:

- `repository.py`: DB access
- `service.py`: domain logic
- `controller.py` 또는 `router.py`: HTTP routes
- `schemas.py`: Pydantic DTOs
- `errors.py`: domain-specific exceptions

모듈 간 interface는 service 레이어 함수 시그니처로 정의 (코드 자체가 contract).
shared types는 packages/shared-types/ 에서 codegen.

### 5.1 `auth/`

- `TokenValidator`
- `ScopeChecker`
- `CurrentUserResolver`
- `OAuthMetadataController`
- `ExternalConnectionService`

### 5.2 `mcp_gateway/`

- `McpHttpController`
- `JsonRpcEnvelope`
- `McpDispatcher`
- `InitializeHandler`
- `ToolsListHandler`
- `ToolsCallHandler`
- `McpSessionManager`
- `McpErrorMapper`

### 5.3 `registry/`

- `McpServiceRepository`
- `McpServiceService`
- `UrlSafetyChecker`
- `ServiceValidationService`
- `ToolSchemaCacheService`

### 5.4 `toolbox/`

- `ToolboxRepository`
- `ToolboxService`
- `ToolCatalogBuilder`
- `ToolAliasService`

### 5.5 `credentials/`

- `CredentialVault`
- `CredentialResolver`
- `SecretEncryptor`
- `SecretMasker`

### 5.6 `proxy/`

- `DownstreamMcpClient`
- `ProxyExecutor`
- `ProxyRequestBuilder`
- `ProxyResponseNormalizer`
- `TimeoutPolicy`

### 5.7 `audit/`

- `AuditLogger`
- `InvocationLogger`
- `LogRedactor`

### 5.8 `billing/`

- `SubscriptionRepository`
- `UsageCounterService`
- `StripeWebhookHandler`
- `QuotaEnforcer`

### 5.9 `compliance/`

- `RightToErasureService`
- `DataExportService`
- `ConsentManager`
- `AuditExporter`

---

## 6. First Working Vertical Slice

가장 먼저 완성할 vertical slice:

```text
1. hardcoded user
2. hardcoded toolbox
3. hardcoded downstream no-auth MCP
4. /mcp initialize
5. /mcp tools/list returns downstream cached tool
6. /mcp tools/call proxies to downstream
```

그 다음 auth/db/ui를 붙인다.

---

## 7. Definition of Done

각 feature의 DoD:

- unit test 있음
- integration test 있음
- error case 처리
- audit/invocation log 필요 시 기록
- secret redaction 확인
- API docs 업데이트
- UI empty/loading/error state 있음
- security checklist 통과
- accessibility check (WCAG AA, lighthouse 90+)
- i18n key 추가 (en + ko)
- error/loading/empty state UI 검증
- audit_logs 이벤트 추가 (해당 시)
- right-to-erasure 영향 검토 (해당 시)
- migration online-safe (expand-contract)
- cache invalidation 영향 검토
- 17-mcp-client-profiles 영향 검토 (해당 시)

---

## 8. Beta Launch Criteria

- [ ] 3개 이상의 sample downstream MCP 등록 성공
- [ ] Claude Code end-to-end 성공
- [ ] 95% tools/list success rate in staging
- [ ] 90% tools/call success rate with healthy downstream
- [ ] no known token passthrough
- [ ] no plaintext credentials in DB/logs
- [ ] SSRF guard tested
- [ ] connected client revoke works
- [ ] onboarding guide complete
- [ ] 17-mcp-client-profiles Claude Code 매트릭스 P0 항목 통과
- [ ] DCR + PKCE + Resource Indicator 검증
- [ ] RLS 정책 적용 + cross-user isolation 테스트
- [ ] right-to-erasure 30d grace flow 동작
- [ ] data export endpoint 동작
- [ ] R-013 ~ R-024 risk mitigation 확인 (11-risk-review)
- [ ] privacy policy / ToS / subprocessors 공개
- [ ] status page 활성
- [ ] runbook 7.5 ~ 7.8 작성 완료
