# CoreMCP Implementation Plan (Personal)

문서 버전: v1.0
작성일: 2026-05-11
대상: 1인 개발 (본인), 4~5주 기간

> 2026-05-14 동기화 메모: 이 파일은 초기 phase 설계/계획 문서다. 아래 Phase별 unchecked task는 현재 remaining backlog와 1:1 대응하지 않는다. 실제 구현 상태와 남은 항목 분류는 `../README.md`, `../TESTING.md`, `../dev-plan/implement_20260511_221842.md`, `../dev-plan/implement_20260514_201743.md`, `../dev-plan/implement_20260514_224500.md`를 우선한다.

---

## 1. 개발 전략

"작동하는 end-to-end path"를 가장 먼저 완성한다.

핵심 path:
```text
정적 bearer token
 -> CoreMCP /mcp endpoint (initialize/tools/list/tools/call)
 -> 등록된 MCP service
 -> 캐시된 tool schema
 -> downstream proxy
 -> Codex CLI exec 호출 성공
```

각 Phase 종료 시 본인이 실제 사용할 수 있어야 한다.

---

## Design pattern absorption — personal scope

`coremcp-design-patterns-to-absorb.md`와 `production_docs_donotuse/`는 SaaS 청사진을 포함하지만, 현재 구현 범위는 **개인용 CoreMCP Gateway + 도구함 관리**가 우선이다. 이 문서에서 흡수하는 패턴은 개인 운영 안정성, 도구함 품질, 외부 AI client 연결성, 장애 격리, 관측 가능성을 개선하는 항목으로 제한한다.

현재 scope에 포함 가능한 흡수 대상:
- service registry metadata와 validation 상태를 개인 운영자가 이해하기 쉽게 저장/표시
- token boundary, credential vault, SSRF guard, per-client token recheck 같은 zero-trust gateway 불변식 강화
- 개인 도구함의 service/tool 단위 표시·비활성화·권한 확인
- schema drift, catalog sync 실패, downstream partial failure를 Admin UX와 log에서 추적 가능하게 만들기
- request_id, metrics, safe audit/invocation log로 local observability 보강
- Codex CLI exec/Claude Code/Claude/ChatGPT/Cursor/OpenClaw 등 연결된 AI client 등록 가이드 개선

현재 scope에서 제외하고 장기 backlog로만 유지할 대상:
- team/workspace 멀티테넌시, 멤버/역할/조직 권한 모델
- public marketplace, publisher profile, verified badge, public review queue
- billing, quota, abuse automation
- 외부 plugin package loading/sandbox runtime
- REST/gRPC/Database-to-MCP adapter 실제 구현
- Kubernetes/Enterprise 배포와 SaaS 운영 프로파일 강제 전환

위 제외 항목은 명시적인 사용자 요청과 활성 dev-plan phase/ADR이 생기기 전까지 구현 태스크로 승격하지 않는다.

---

## 2. Tech Stack (확정)

### Backend
- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy 2.0 async
- Alembic (SQLite batch mode)
- httpx async
- structlog
- keyring (macOS Keychain) / cryptography (fernet) — secret backend 선택 (ADR-031)
- python-jose (JWT, 옵션)
- uv 또는 poetry

### Frontend
- Next.js 15+ (App Router)
- TypeScript
- Tailwind CSS
- shadcn/ui
- TanStack Query
- Zod
- react-hook-form
- Pretendard (한글 폰트)

### Infra
- SQLite 3.35+ (~/.coremcp/data/db.sqlite3)
- launchd (Mac mini daemon)
- 옵션: Docker Compose (Postgres + Redis), Tailscale, Caddy

### Monorepo
- pnpm + Turborepo

---

## 3. Repository Structure

```text
coremcp/
├── apps/
│   ├── api/
│   │   ├── coremcp/
│   │   │   ├── main.py              # FastAPI app
│   │   │   ├── config.py
│   │   │   ├── bootstrap.py         # 최초 실행 시 user/toolbox 생성
│   │   │   ├── db/
│   │   │   │   ├── base.py
│   │   │   │   ├── models/
│   │   │   │   └── session.py
│   │   │   ├── auth/
│   │   │   │   ├── bearer.py        # 정적 token 검증
│   │   │   │   └── oauth/           # 옵션 OAuth AS
│   │   │   ├── mcp_gateway/
│   │   │   │   ├── routes.py        # /mcp POST/GET/DELETE
│   │   │   │   ├── dispatcher.py    # JSON-RPC method dispatch
│   │   │   │   ├── handlers.py      # initialize/tools/list/tools/call/ping
│   │   │   │   ├── session.py       # in-memory map
│   │   │   │   ├── sse.py           # listChanged emit
│   │   │   │   └── metadata.py      # /.well-known/*
│   │   │   ├── registry/
│   │   │   │   ├── service.py
│   │   │   │   ├── repository.py
│   │   │   │   ├── routes.py
│   │   │   │   ├── ssrf.py          # URL safety
│   │   │   │   ├── validation.py    # BackgroundTask
│   │   │   │   └── scanner.py       # tool poisoning
│   │   │   ├── toolbox/
│   │   │   │   ├── service.py
│   │   │   │   ├── repository.py
│   │   │   │   ├── catalog.py       # builder + cache
│   │   │   │   ├── alias.py
│   │   │   │   └── routes.py
│   │   │   ├── credentials/
│   │   │   │   ├── vault.py         # backend abstraction
│   │   │   │   ├── keychain.py
│   │   │   │   ├── fernet.py
│   │   │   │   └── routes.py
│   │   │   ├── proxy/
│   │   │   │   ├── client.py        # httpx async wrapper
│   │   │   │   ├── executor.py
│   │   │   │   ├── idempotency.py
│   │   │   │   └── normalizer.py
│   │   │   ├── external_conn/
│   │   │   │   ├── service.py
│   │   │   │   ├── one_time_token.py
│   │   │   │   └── routes.py
│   │   │   ├── audit/
│   │   │   │   ├── logger.py
│   │   │   │   └── routes.py
│   │   │   ├── invocations/
│   │   │   │   ├── logger.py
│   │   │   │   └── routes.py
│   │   │   ├── jobs/
│   │   │   │   ├── manager.py
│   │   │   │   └── routes.py
│   │   │   ├── playground/
│   │   │   │   └── routes.py
│   │   │   ├── settings/
│   │   │   │   └── routes.py
│   │   │   ├── logging_config.py
│   │   │   └── api/                 # version-prefixed routers
│   │   ├── alembic/
│   │   │   ├── env.py
│   │   │   └── versions/
│   │   ├── tests/
│   │   └── pyproject.toml
│   ├── web/
│   │   ├── app/                     # Next.js App Router
│   │   │   ├── (auth)/token/
│   │   │   ├── (main)/page.tsx      # dashboard
│   │   │   ├── (main)/services/
│   │   │   ├── (main)/toolbox/
│   │   │   ├── (main)/clients/
│   │   │   ├── (main)/playground/
│   │   │   ├── (main)/logs/
│   │   │   └── (main)/settings/
│   │   ├── components/
│   │   ├── lib/
│   │   │   ├── api.ts               # fetch wrapper + token
│   │   │   ├── auth.ts
│   │   │   └── queries.ts           # TanStack Query
│   │   ├── messages/                # i18n ko/en
│   │   └── package.json
│   └── fake-mcp/                    # 테스트용 fake downstream
│       └── ...
├── packages/
│   ├── shared-types/                # OpenAPI codegen
│   └── client-profiles/
├── infra/
│   ├── launchd/
│   │   ├── com.coremcp.api.plist
│   │   └── com.coremcp.web.plist
│   ├── docker/
│   │   └── docker-compose.yml       # 옵션 Postgres/Redis
│   └── scripts/
│       ├── bootstrap.sh
│       ├── rotate-token.sh
│       └── backup.sh
├── coremcp-docs/                    # 본 문서팩
├── production_docs_donotuse/                 # SaaS 청사진
└── pnpm-workspace.yaml
```

---

## 4. Phases

총 4 phase, 약 4~5주 (1인 기준).

### Phase P0 — Vertical Slice (1주)

목표: Codex CLI exec에서 CoreMCP client token으로 fake tool 호출까지의 가장 단순한 end-to-end.

Tasks:
- [ ] Repository scaffold (pnpm + apps/api + apps/web + apps/fake-mcp)
- [ ] FastAPI app + uvicorn 실행
- [ ] SQLite + Alembic 초기 migration (users, toolboxes, mcp_services, service_tools, tool_aliases, tool_invocations, audit_logs) — partial unique index 적용 (ADR-035)
  - personal_access_tokens 테이블은 migration에는 포함하되 코드 사용은 P1
- [ ] bootstrap.py: 첫 실행 시 user/toolbox 생성 + admin token 파일 생성 안내
- [ ] auth/admin.py: admin token (파일) 검증 (hmac.compare_digest)
  - client token 검증 로직은 P1
- [ ] /mcp POST 라우트 + JSON-RPC dispatcher
- [ ] initialize / tools/list / tools/call / ping handler
- [ ] Protocol version negotiation (2025-06-18 + 2025-11-25 양쪽 응답, ADR-029)
  - request의 `protocolVersion` echo 또는 downgrade
  - 헤더 누락 시 2025-06-18 fallback
  - 응답 protocolVersion 항상 명시
- [ ] notifications/initialized 수신
- [ ] Mcp-Session-Id in-memory map
- [ ] fake downstream MCP (apps/fake-mcp): no-auth + 2~3개 tool
- [ ] hardcoded service 1개 + toolbox 1개를 seed
- [ ] downstream client httpx async + fake tool call
- [ ] tool_invocations 1줄 기록
- [ ] Codex CLI MCP 등록 (`make codex-install`) + tools/list / tools/call 성공 확인

Exit Criteria:
- `make codex-install && make codex-smoke` 후 Codex client token으로 MCP initialize/tools-list 성공
- invocation log 한 줄
- CoreMCP admin token이 fake-mcp에 전달되지 않음 (token boundary 확인)
- 2025-11-25 또는 2025-06-18 protocol version 요청 모두 정상 응답

### Phase P1 — Real Service & Per-Client Token (1.5주)

목표: Per-client token + 실제 MCP 등록 + credential vault.

Tasks:
- [ ] personal_access_tokens 활용 시작 (테이블은 P0에서 migration)
- [ ] auth/client_token.py: sha256 hash 비교
- [ ] /v1/settings/client-tokens (POST/GET/DELETE) endpoint
- [ ] external_connections CRUD
- [ ] external_connection revoke 시 client_token CASCADE
- [ ] mcp_services REST API (POST/GET/PATCH/DELETE) + URL safety + allowlist 환경 변수
- [x] credential vault (KeychainBackend primary, FernetBackend encrypted fallback)
- [ ] service_credentials API (PUT/GET/DELETE/rotate)
- [ ] validation BackgroundTask (initialize + tools/list + metadata_scan)
- [ ] tool_aliases 생성 / slug rename 처리
- [ ] schema_hash 계산 (canonical JSON)
- [ ] service_tools.icons_json 채우기
- [ ] toolbox_items API
- [ ] Tool catalog builder + L1 in-memory dict cache
- [ ] tools/list user toolbox 기반 동적 반환 (icons top-level)
- [ ] tools/call alias 해석 + credential resolve + downstream 실제 호출
- [ ] audit_logs 기록
- [ ] 실제 MCP 1개 (GitHub MCP 또는 자체) 연결 성공
- [ ] SSRF allowlist 환경 변수 검증
- [ ] tool poisoning scanner regex + Unicode
- [ ] Protocol version negotiation 회귀 테스트 (downgrade, 미래 버전, 누락 케이스)
- [ ] icons top-level forward 동작 (annotations 안에 없음 확인)

Exit Criteria:
- Mac mini 로컬 + MacBook(Tailscale) 각각 별도 client token으로 동작
- 한쪽 revoke → 한쪽만 401
- 실제 MCP tool 호출 성공
- credential 평문이 DB / log에 없음

### Phase P2 — Web Admin UI (1~2주)

목표: 본인용 admin 콘솔.

Tasks:
- [x] Next.js scaffold (App Router) + Tailwind
- [x] Admin Token 입력 화면 + sessionStorage
- [x] API client wrapper (fetch)
- [x] Dashboard page (services count, recent invocations, system health)
- [x] Services list / new route shell
- [x] Toolbox page 기본 controls
- [x] Playground page (tool 직접 호출)
- [x] Logs invocations / audit
- [x] Connected clients 목록 + revoke
- [ ] Connect Codex CLI exec 가이드 (`make codex-install` + wrapper command generator)
- [ ] Settings: token rotate, locale, debug trace
- [x] Settings / Tokens 페이지 shell (Admin + Client 분리, ADR-030)
- [x] Connected Clients에 client token prefix + revoke 버튼
- [x] Client token 발급 modal (평문 1회 노출)
- [x] 401 시 sessionStorage clear
- [ ] Dark mode + ko 우선 i18n

Exit Criteria:
- Web에서 새 MCP 등록 → validation → toolbox 추가 → playground 호출까지 마우스만으로 가능.

### Phase P3 — Polish & Daemon (1주)

목표: 무인 운영 + 옵션 기능.

Tasks:
- [x] launchd plist (com.coremcp.api / com.coremcp.web / com.coremcp.backup / com.coremcp.logrotate)
- [ ] Mac mini boot 후 자동 시작 검증 — 실제 reboot 외부환경 검증 필요
- [x] daily SQLite backup script + launchd schedule
- [x] log rotation (daily 00:15 launchd label, 7일 보관)
- [ ] Tailscale Serve 또는 Caddy reverse proxy 설정 (옵션) — 현재 머신 CLI 없음, 외부환경 검증 필요
- [x] listChanged SSE emission (toolbox 변경 / schema 변경 시)
- [x] cancellation notification logging + downstream forward
- [x] idempotency_key 캐시
- [x] one-time connection token 발급 / exchange (OpenClaw용)
- [x] /v1/playground/tools/call
- [x] OAuth 2.1 자체 AS (옵션, AUTH_MODE=oauth)
- [x] CIMD endpoint handler (AUTH_MODE=oauth 활성 시, ADR-036)
- [x] CIMD client metadata fetch + 캐시 (ADR-036)
- [x] DCR fallback handler
- [ ] icons CDN 캐시 (옵션)
- [ ] (옵션) PostgreSQL 마이그레이션 dry-run
- [x] (옵션) Prometheus /metrics endpoint — `METRICS_ENABLED=true`일 때 노출
- [ ] (옵션) Sentry / OTel 통합
- [ ] Mac mini 재부팅 후 5분 이내 서비스 정상화 확인

Exit Criteria:
- Mac mini 무인 운영 1주 + Codex CLI exec 통한 일상 사용 안정.
- 본인이 MacBook(Tailscale)에서도 동일 도구함 사용.

---

## 5. Backend Module Tasks

각 모듈은 다음 layer 구조를 따른다:
- `repository.py`: DB access (SQLAlchemy)
- `service.py`: domain logic
- `routes.py`: FastAPI router
- `schemas.py`: Pydantic DTOs
- `errors.py`: domain exceptions

### 5.1 auth/
- `BearerVerifier`
- `(옵션) JwtVerifier`
- `(옵션) OAuthAuthorizationServer`

### 5.2 mcp_gateway/
- `McpHttpController`
- `JsonRpcEnvelope`
- `McpDispatcher`
- `InitializeHandler` / `ToolsListHandler` / `ToolsCallHandler` / `PingHandler`
- `McpSessionManager`
- `McpErrorMapper`
- `SseEmitter` (listChanged)

### 5.3 registry/
- `McpServiceRepository`
- `McpServiceService`
- `UrlSafetyChecker`
- `ServiceValidationService`
- `ToolMetadataScanner`

### 5.4 toolbox/
- `ToolboxRepository`
- `ToolboxService`
- `ToolCatalogBuilder` + `ToolCatalogCache` (L1)
- `ToolAliasService`

### 5.5 credentials/
- `CredentialVault` (abstract)
- `KeychainBackend`
- `FernetBackend`
- `CredentialResolver`
- `SecretMasker`

### 5.6 proxy/
- `DownstreamMcpClient` (httpx async)
- `ProxyExecutor`
- `IdempotencyCache`
- `CancellationBridge`
- `ProxyResponseNormalizer`

### 5.7 external_conn/
- `ExternalConnectionService`
- `ConnectionTokenService`
- `ConnectionTokenHasher`

### 5.8 audit/, invocations/, jobs/
- `AuditLogger` / `InvocationLogger`
- `LogRedactor`
- `JobManager` (BackgroundTasks wrapper)

### 5.9 playground/, settings/
- `PlaygroundService`
- `SettingsService`

---

## 6. Frontend Module Tasks

```text
apps/web/
├── lib/
│   ├── api.ts          # fetch wrapper, X-Request-Id, Bearer
│   ├── auth.ts         # sessionStorage token store
│   ├── queries.ts      # useServices, useToolbox, useInvocations
│   ├── i18n.ts
│   └── types/          # OpenAPI codegen 결과
├── components/
│   ├── ui/             # shadcn primitives
│   ├── services/
│   ├── toolbox/
│   ├── logs/
│   └── playground/
└── app/
    ├── (auth)/token/   # token 입력
    └── (main)/
        ├── layout.tsx  # nav + theme + auth guard
        ├── page.tsx    # dashboard
        ├── services/
        ├── toolbox/
        ├── clients/
        ├── playground/
        ├── logs/
        └── settings/
```

---

## 7. First Working Vertical Slice (Phase P0 detail)

```text
1. apps/fake-mcp 띄움 (port 9999, no auth, tools: echo, add)
2. seed.py로 mcp_services 1행 (slug=fake, endpoint=http://localhost:9999/mcp, auth_type=none)
3. seed.py로 service_tools 2행 + tool_aliases 2행 (fake.echo, fake.add)
4. seed.py로 toolbox 1개 + toolbox_items 1행
5. ~/.coremcp/admin-token 생성 (`cmcp_admin_<random>`)
6. uvicorn 실행 with COREMCP_ADMIN_TOKEN_FILE
7. make codex-install
8. infra/scripts/codex-exec-coremcp.sh "fake.add 사용해서 1+2 계산해" → 3 응답
```

---

## 8. Definition of Done (per task)

- [ ] unit test (pytest)
- [ ] integration test (FastAPI TestClient + httpx mock 또는 fake-mcp)
- [ ] error case 처리
- [ ] audit/invocation log 기록 (해당 시)
- [ ] secret redaction 확인
- [ ] FastAPI OpenAPI 자동 생성 정상
- [ ] UI: empty/loading/error 상태
- [ ] 401 처리
- [ ] migration online-safe (expand-contract)
- [ ] cache invalidation 영향 검토
- [ ] CHANGELOG 한 줄 추가

---

## 9. 환경 변수 (개발 vs 운영)

dev (`.env.local`):
```text
COREMCP_HOST=127.0.0.1
COREMCP_PORT=8787
DATABASE_URL=sqlite+aiosqlite:///./dev.sqlite3
COREMCP_ADMIN_TOKEN_FILE=./dev-admin-token
AUTH_MODE=static_bearer
SECRET_BACKEND=fernet    # ADR-031: headless dev 환경
FERNET_KEY_FILE=./dev-secret.key
LOG_LEVEL=DEBUG
```

prod (`~/.coremcp/.env`):
```text
COREMCP_HOST=127.0.0.1
COREMCP_PORT=8787
DATABASE_URL=sqlite+aiosqlite:////Users/me/.coremcp/data/db.sqlite3
COREMCP_ADMIN_TOKEN_FILE=/Users/me/.coremcp/admin-token
AUTH_MODE=static_bearer
SECRET_BACKEND=keychain  # ADR-031: 자동 로그인 환경
LOG_LEVEL=INFO
LOG_FILE=/Users/me/.coremcp/logs/coremcp.log
```

---

## 10. Phase별 Milestone Demo

각 phase 끝에 본인이 다음을 demo:

- P0: Codex CLI exec → fake tool 호출 성공 로그/스크린샷
- P1: 실제 GitHub MCP 연결 후 issue 생성 영상
- P1: Codex CLI exec(client token) → optional Claude Code(client token) → 동일 도구함 동작 + 한쪽 revoke 시연
- P2: Web UI에서 새 MCP 등록 → 즉시 사용 영상
- P3: Mac mini 재부팅 후 5분 내 정상화 + MacBook(Tailscale)에서 동일 도구함 사용 영상

---

## 11. Long-term Backlog / 현재 제외 범위

다음 항목은 현재 personal gateway 구현 태스크가 아니며, P3 이후 재검토 또는 SaaS 전환 시에만 별도 dev-plan/ADR로 승격한다:
- Multi-toolbox 활성 UI
- Tool-level enable/disable
- Schema diff viewer
- Workspace 활성 (15-future-saas-migration.md)
- Marketplace browse (SaaS 전환 시)
- Pricing/Billing (SaaS 전환 시)
- Multi-region (SaaS 전환 시)
