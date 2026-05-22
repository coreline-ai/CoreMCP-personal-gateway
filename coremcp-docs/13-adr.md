# CoreMCP Architecture Decision Records (Personal)

문서 버전: v1.0
작성일: 2026-05-11

본 ADR은 개인 Mac mini 운영 컨텍스트에서 확정된 결정 기록이다. SaaS 전환 시 일부는 Superseded로 표시되며, 그 절차는 `15-future-saas-migration.md`.

---

## ADR-001: Product Concept is Toolbox First

Status: Accepted

Decision:
CoreMCP의 사용자-facing 개념은 Gateway가 아니라 Toolbox다.

Rationale:
본인이 사용하더라도 "도구함을 관리한다"는 개념이 "gateway를 설정한다"보다 직관적이고 UX 단순.

Consequences:
- 주요 내비게이션은 Toolbox 중심
- 기술 용어(proxy/aggregator)는 Developer/Settings 탭에서만

---

## ADR-002: Proxy Mode over Meta-tool Mode

Status: Accepted

Decision:
`invoke_tool(service, tool, args)` 메타 도구가 아니라 `github.create_issue` 같은 개별 exposed tool로 노출.

Rationale:
LLM이 자연스럽게 tool을 선택. Claude Code UI에서 tool 목록이 명확.

Consequences:
- tool alias / cache 시스템 필요
- name collision 처리 필요
- dynamic tools/list 구현 필요

---

## ADR-003: Streamable HTTP First

Status: Accepted

Decision:
`/mcp` endpoint는 Streamable HTTP를 우선 지원.

Rationale:
MCP 표준이고 Claude Code 기본 transport. SSE는 deprecated.

Consequences:
- POST 필수
- GET SSE는 listChanged 채널로 사용 (ADR-007)
- session id / MCP-Protocol-Version 헤더 처리

---

## ADR-004: No Token Passthrough

Status: Accepted

Decision:
CoreMCP token은 downstream MCP service에 전달하지 않는다.

Rationale:
audience boundary와 MCP security requirement 준수. token 누설 방지.

Consequences:
- downstream credential vault 필수
- credential resolver 필요
- integration test 필수 (10-test-plan.md §6.1)

---

## ADR-005: Downstream Auth = Bearer / API Key Vault (MVP)

Status: Accepted

Decision:
MVP에서 downstream auth는 bearer_token, api_key_header 두 가지. OAuth-delegated downstream은 Phase P3+.

Rationale:
구현 범위 축소. 본인이 쓸 대부분의 MCP가 bearer / API key 패턴.

Consequences:
- credential vault 보안 중요
- OAuth-delegated는 Phase 보류

---

## ADR-006: Public Marketplace Excluded

Status: Accepted

Decision:
공개 marketplace 기능은 본 프로젝트에서 미구현. service.visibility는 항상 'private'.

Rationale:
외부 사용자 없음. review/abuse 처리 부담 없음.

Consequences:
- visibility 컬럼은 미래용으로만 유지
- Marketplace UI 미구현

---

## ADR-007: GET SSE Minimal Implementation

Status: Accepted

Decision:
`GET /mcp`는 SSE empty stream + 15s keepalive ping. `notifications/tools/list_changed` 채널.

Rationale:
`tools.listChanged: true` capability와 일관성. 405면 Claude Code가 stale catalog.

Consequences:
- 연결 유지 비용 (단일 사용자라 미미)
- 단일 프로세스라 SSE handler in-process 호출

---

## ADR-008: No Stdio Hosting

Status: Superseded by ADR-039

Decision:
stdio MCP 직접 호스팅 안 함. Remote HTTP만 등록 가능.

Rationale:
RCE / sandboxing 위험. 본인이 만든 stdio 서버는 localhost http로 wrap.

Consequences:
- 본인이 stdio MCP를 쓰려면 별도 http wrapper 작성 (mcp-proxy 또는 자체)
- localhost http는 SSRF guard 예외

---

## ADR-009: API + Web Same Workspace, Different Processes

Status: Accepted

Decision:
FastAPI API는 1개 process. Next.js Web은 별도 process (dev / build+serve).
같은 monorepo, 다른 launchd.

Rationale:
개발 편의 + Next.js hot reload. 단일 process에 정적 serve도 옵션이지만 dev 경험 떨어짐.

Consequences:
- 두 launchd plist 관리
- CORS 정책 필요

---

## ADR-010: Store Invocation Metadata, Not Raw Bodies

Status: Accepted

Decision:
tool arguments / output 원문은 기본 미저장. opt-in debug trace 시 24h 한정.

Rationale:
개인 환경이지만 disk 절약 + 민감 데이터 회피.

Consequences:
- debug_traces 테이블 opt-in
- 환경 변수 / Web UI에서 활성

---

## ADR-011: Authorization = Static Bearer (Default), OAuth Optional

Status: Accepted

Decision:
기본 인증은 정적 bearer token. OAuth 2.1 / DCR / PKCE / Resource Indicator는 옵션(Phase P3+).

Rationale:
단일 사용자라 OAuth 복잡도 불필요. ChatGPT/Cursor 등 OAuth 강제 client 사용 시점에 OAuth 모드 활성.

Consequences:
- 토큰 파일 관리 (chmod 600, .gitignore)
- 회전은 파일 재작성 + client 재등록
- OAuth 활성 시 oauth_* 테이블 사용

---

## ADR-012: Credential Vault = macOS Keychain (Default), Fernet Fallback

Status: Accepted

Decision:
downstream credential은 macOS Keychain(keyring)에 저장. fernet은 headless / 자동 운영 시 fallback.

Rationale:
OS native, iCloud Keychain 동기화 가능, 잠금 모델 우수. AWS KMS / Vault는 단일 호스트 과잉.

Consequences:
- keychain unlock 의존 (R-106)
- secret_ref 형식: `keychain:coremcp:svc_<id>:<type>` 또는 `fernet:<row_id>`

---

## ADR-013: MVP MCP Capability = tools-only

Status: Accepted

Decision:
MVP는 tools만 노출. resources / prompts / completions / logging / sampling / elicitation 미지원, server capabilities에 omit.

Rationale:
범위 축소. tools만으로 본인 사용 가치 충분.

Consequences:
- downstream에서 sampling/elicitation 요청 시 -32601
- Phase P3+에서 검토

---

## ADR-014: Sampling / Elicitation = Reject

Status: Accepted

Decision:
server-to-client `sampling/createMessage`, `elicitation/create`는 -32601 Method not found 반환.

Rationale:
PII 위험 + client capability 협상 복잡도. MVP 가치 적음.

Consequences:
- downstream MCP가 sampling 의존하면 호환성 떨어짐
- service registration 시 capability scan으로 경고

---

## ADR-015: tool_aliases Separate Table

Status: Accepted

Decision:
`service_tools.exposed_name` 컬럼 대신 별도 `tool_aliases` 테이블 운영. exposed_name immutable, slug rename은 deprecated alias로.

Rationale:
service slug 변경 시 LLM이 기억한 tool name 불변. 2주 grace.

Consequences:
- 추가 테이블 / lookup 우선순위(primary alias)
- cleanup job 필요

---

## ADR-016: Tool Catalog Cache = 3-tier (Single Process 단순화)

Status: Accepted

Decision:
L1 in-process dict (60s) + L2 in-memory(또는 Redis 옵션, 1h) + L3 PostgreSQL/SQLite service_tools (24h hard cap).
단일 프로세스에서는 L1만으로도 충분. Redis는 multi-process 확장 시.

Rationale:
빈번한 list 호출 처리. SQLite 락 회피.

Consequences:
- single process에서는 invalidate가 직접 함수 호출
- multi-process 확장 시 Redis pub/sub

---

## ADR-017: Tool Naming Format = Dotted

Status: Accepted

Decision:
exposed tool name은 `{service_slug}.{tool_name}`. underscore fallback은 client 비호환 시.

Rationale:
ChatGPT/Claude/Cursor UI namespace 인식 + 가독성.

Consequences:
- dot 처리 비호환 client 발견 시 client_profile에 underscore 변형
- service_slug에 special char 회피

---

## ADR-018: Worker = FastAPI BackgroundTasks (Default), Arq Optional

Status: Accepted

Decision:
async job은 FastAPI BackgroundTasks. Celery/RQ 미사용. Arq는 multi-process 확장 시.

Rationale:
single process라 충분. asyncio 일관. 운영 단순.

Consequences:
- 긴 job(>1min)이 API 프로세스 점유
- 확장 시 Arq 도입

---

## ADR-019: DB = SQLite (Default), PostgreSQL Optional

Status: Accepted

Decision:
SQLite 3.35+ (WAL 모드). PostgreSQL은 Docker 옵션.

Rationale:
단일 사용자에 SQLite 충분. WAL로 동시성 양호. backup이 file copy.

Consequences:
- SQLAlchemy 양쪽 dialect 호환 DDL 유지
- 마이그레이션 시 type 변환 (JSON vs JSONB 등)

---

## ADR-020: Data Region = Local Mac mini (Personal)

Status: Accepted

Decision:
모든 데이터는 Mac mini 로컬 디스크. region/replication/multi-AZ 무관.
외부 노출은 Tailscale 또는 Cloudflare Tunnel 옵션.

Rationale:
타겟 사용자는 본인 1명. 한국어 우선. SaaS region 결정 불필요.

Consequences:
- 16-compliance 같은 docs는 적용 안 됨
- 향후 SaaS 전환 시 본 ADR을 Superseded로 표시 (`15-future-saas-migration.md`)

---

## ADR-021: Pricing Model = None (Personal)

Status: Accepted

Decision:
과금 시스템 미구현. billing/quota 관련 테이블 미생성.

Rationale:
본인 사용. 과금 무관.

Consequences:
- production_docs_donotuse/14-pricing.md는 reference만
- workspace.plan 컬럼은 future-proof로 'free' 고정

---

## ADR-022: License = Private Repository (Personal)

Status: Accepted

Decision:
GitHub Private repo. 라이선스 파일 없음 (All rights reserved). 공개 시 MIT/Apache 2.0 검토.

Rationale:
외부 공개/배포 계획 없음. ToS/Privacy/DPA 불필요.

Consequences:
- 외부 기여 수용 없음
- public marketplace 무관

---

## ADR-023: Frontend = Next.js + shadcn/ui

Status: Accepted

Decision:
Admin Web UI는 Next.js 15 App Router + TypeScript + Tailwind + shadcn/ui + TanStack Query.

Rationale:
shadcn 커스터마이즈 자유, Tailwind 빠름, TanStack Query 캐시/refetch 우수.

Consequences:
- Pretendard 폰트 (한글 우선)
- 단일 SPA 라우팅

---

## ADR-024: Workspace Pre-Baked Nullable

Status: Accepted

Decision:
workspace 개념은 단일 사용자라 미사용이지만, `workspace_id` 컬럼은 mcp_services / toolboxes 등에 nullable로 선반영.

Rationale:
SaaS 확장 시 마이그레이션 비용 절감.

Consequences:
- 모든 코드는 owner_user_id 기준으로 동작
- workspace_id는 NULL

---

## ADR-025: Logging = structlog JSON to file

Status: Accepted

Decision:
structlog JSON 포맷, `~/.coremcp/logs/coremcp.log`. Sentry/OTel는 옵션.

Rationale:
file은 grep/jq 친화. JSON으로 통합 분석.

Consequences:
- daily rotation script 필요 (12-operations §3.5)
- key redaction 필수

---

## ADR-026: One-Time Connection Token Retained

Status: Accepted

Decision:
OAuth 비활성이라도 OpenClaw 등 OTT가 필요한 client용 endpoint 유지.

Rationale:
사용 빈도 적어도 client 다양성 hedge.

Consequences:
- connection_tokens 테이블 유지
- IP/UA binding 정책 (06 §5)

---

## ADR-027: Right-to-Erasure = Manual (Personal)

Status: Accepted

Decision:
right-to-erasure 자동 endpoint 미제공. 본인이 직접 `rm -rf ~/.coremcp/` + repo 삭제.

Rationale:
본인 데이터, 본인 머신. GDPR/개인정보보호법 적용 외.

Consequences:
- /v1/me DELETE 미구현
- 데이터 export도 SQLite 직접 접근

---

## ADR-028: Single Process MVP, Multi-Process Future

Status: Accepted

Decision:
MVP는 FastAPI single process (uvicorn 1 worker). 확장 시 gunicorn + multi worker, 그 때 in-memory state → Redis.

Rationale:
운영 단순. 단일 사용자 부하 적음.

Consequences:
- session/cache가 process 종료 시 사라짐 (재초기화 OK, DB는 영속)
- 확장 시 Redis 도입

---

## ADR-029: Protocol Version Support = 2025-06-18 + 2025-11-25

Status: Accepted

Decision:
CoreMCP `/mcp`는 MCP protocol 2025-06-18과 2025-11-25 두 버전을 지원한다. default 응답 버전은 client가 요청한 버전 중 가장 최신을 사용하며, 누락 시 2025-06-18을 가정한다. 미래 버전 요청 시 지원 가능한 최신으로 downgrade + warning 로그.

Rationale:
Claude Code 호환성 유지(2025-06-18)와 최신 spec 기능(icons metadata, tool name guidance, JSON Schema 2020-12) 채택을 동시에 만족한다. 2025-11-25의 tasks는 실험적이므로 미지원.

Consequences:
- initialize handler에 version negotiation 로직 필수
- service_tools.icons_json (top-level) 컬럼에 icons 저장 (05-database-schema §6.2)
- icons는 MCP 2025-11-25 tool top-level optional field. annotations 안에 두지 않음
- icons field schema: `{src, mimeType, sizes?}` — `src`는 MCP spec 표준 (HTML <img> align), `url`이 아님
- tools/* 외 tasks/* 등 신규 메서드는 -32601
- 환경 변수 MCP_SUPPORTED_VERSIONS, MCP_DEFAULT_VERSION
- 14-mcp-client-profiles 매트릭스에 두 버전 컬럼
- icons size cap 32KB, content-type allowlist (image/png, image/svg+xml, image/webp). SVG 보안 정책은 05/07/08 문서 참조 (P2 이슈로 별도 정리됨).
- icons object는 `{src, mimeType, sizes?}` schema. CoreMCP는 downstream이 `url` field를 보내면 sanitize 단계에서 `src`로 정정

---

## ADR-030: Token Model = Dual (Admin File + Per-Client DB Hash)

Status: Accepted

Decision:
CoreMCP는 두 종류의 personal token을 운영한다.
- `cmcp_admin_*`: root 관리자 권한. `~/.coremcp/admin-token` 파일에 평문 보관. DB 미저장. /v1/* admin API 전용 (또한 /mcp fallback).
- `cmcp_client_*`: external_connection 단위로 발급. DB의 personal_access_tokens.token_hash(sha256)로 비교. /mcp 호출용. 평문은 발급 응답에서 1회만 노출.

Rationale:
정적 token 1개로는 connection별 revoke가 불가능하다. external_connections.revoke가 실제로 의미 있으려면 token이 connection에 binding되어야 한다. 동시에 root 관리자 token은 항상 작동해야 admin이 가능하다.

Consequences:
- 신규 테이블 personal_access_tokens (05-database-schema §9.3)
- external_connections ON DELETE CASCADE로 토큰 자동 정리
- Web UI: connection 생성 시 토큰 평문 1회 노출
- 모든 client token은 prefix `cmcp_client_`, admin은 `cmcp_admin_`
- 환경 변수 COREMCP_ADMIN_TOKEN_FILE

---

## ADR-031: Secret Backend Operational Mode

Status: Accepted

Decision:
SECRET_BACKEND 환경 변수로 두 모드 중 하나를 선택:
- `keychain` (default): Mac mini 자동 로그인 활성 환경
- `fernet`: headless / 무인 부팅 우선 환경 (FERNET_KEY_FILE 필요)

Rationale:
keychain은 OS native 보안과 iCloud Keychain 동기화가 강점이지만 login.keychain 잠금 의존 risk가 있다 (R-106). fernet은 master key 파일이 무인 운영에 즉시 사용 가능하다. 두 모드를 코드로 분리하면 운영자가 환경에 맞게 선택 가능.

Consequences:
- credentials/vault.py에 backend abstraction 구현
- secret_ref prefix로 backend 식별 (`keychain:...`, `fernet:...`)
- 전환 절차: credential 재입력 또는 마이그레이션 스크립트
- 향후 SaaS 전환 시 AWS KMS 추가 (15-future-saas-migration)

---

## ADR-032: Auth Mode = static_bearer Default, OAuth Optional

Status: Accepted

Decision:
AUTH_MODE 환경 변수로 두 모드 중 하나:
- `static_bearer` (default): bearer 검증만, OAuth metadata에 authorization_servers omit
- `oauth`: 자체 AS 활성, RFC 9728 full metadata, /oauth/* 활성

Rationale:
정적 bearer만으로 Claude Code 호환되며 MVP에 충분하다. ChatGPT/Cursor 등 OAuth 강제 client를 쓸 때 oauth 모드로 전환한다. authorization_servers: [] 같은 모호한 응답을 피하기 위해 omit으로 명확히.

Consequences:
- 401 응답 헤더가 모드 별로 다름
- /.well-known/oauth-protected-resource 응답 분기
- AUTH_MODE 변경은 재시작 필요
- audit_log에 auth_mode.change 기록
- static_bearer 모드는 `/.well-known/oauth-protected-resource` 응답을 default 404. `EXPOSE_RESOURCE_METADATA_IN_STATIC_MODE=true`로만 활성 (운영자 명시 opt-in)

---

## ADR-033: SSRF Private CIDR Allowlist

Status: Accepted

Decision:
SSRF guard는 기본 private/loopback/CGNAT 전부 차단. 환경 변수 allowlist로 개별 허용:
- ALLOW_PRIVATE_DOWNSTREAM (default false): 10/172/192 사설망
- ALLOW_TAILSCALE_DOWNSTREAM (default false): 100.64.0.0/10
- ALLOW_LOOPBACK_DOWNSTREAM (default true): 127.0.0.1, ::1 (fake-mcp 개발용)
- ALLOWED_PRIVATE_CIDRS: 명시 CIDR 콤마 구분

169.254.169.254 (cloud metadata)은 어떤 옵션으로도 허용 불가.

Rationale:
"모든 private 차단 + localhost만 예외" 정책은 Tailscale (CGNAT 100.64/10) 사용 시 잘못 차단한다. allowlist 모델이 안전한 default + 운영자 명시 동의의 균형.

Consequences:
- registry/ssrf.py에 allowlist 로직
- env 검증 (잘못된 CIDR 형식 → 부팅 실패)
- 14-mcp-client-profiles 호환성 매트릭스에 영향 (Tailscale 시나리오)

---

## ADR-034: Error Mapping = Protocol vs Tool Result Separation

Status: Accepted

Decision:
오류는 두 계층으로 분리:
- **JSON-RPC error (-32602 등)**: unknown tool name, malformed params, parse error 같은 protocol level 오류
- **result.isError=true**: downstream이 보낸 도구 실행 오류, input 검증 실패, timeout, business error 등

Rationale:
MCP 2025-11-25 spec guidance와 일치한다. unknown tool은 protocol error로 처리해야 client가 LLM context로 잘못 전달하지 않는다. 도구 실행 결과 오류는 isError로 client에 LLM이 retry 판단 가능하게 노출.

Consequences:
- 07-mcp-proxy-spec.md §8 매핑표 갱신
- ToolsCallHandler에서 alias lookup 실패는 -32602 반환
- downstream의 isError=true는 그대로 forward
- 04-api-spec.md §2.7 에 분류 규약 명시

---

## ADR-035: Soft-delete Partial Unique Index

Status: Accepted

Decision:
soft-delete를 지원하는 테이블의 모든 UNIQUE 제약은 partial unique index(`WHERE deleted_at IS NULL` 또는 동등 조건)로 구현한다.

대상:
- mcp_services(owner_user_id, slug)
- toolbox_items(toolbox_id, service_id)
- tool_aliases(exposed_name)
- service_credentials(service_id) [WHERE revoked_at IS NULL]
- personal_access_tokens(token_hash) [WHERE revoked_at IS NULL]

Rationale:
일반 UNIQUE는 soft-delete 후 동일 키 재생성을 막아 사용자가 실수로 막힌다. partial unique는 active row만 unique를 강제해 운영 편의 + 데이터 보존 동시 달성.

Consequences:
- 05-database-schema.md DDL의 모든 해당 UNIQUE를 partial index로 변경
- cleanup job: deleted_at < now() - 30d 기준 hard-delete
- SQLite 3.8+, PostgreSQL 모두 호환
- audit_logs에는 deleted row의 historical reference 유지

---

## ADR-036: OAuth Client Registration = CIMD First, DCR Fallback

Status: Accepted (적용 시점: AUTH_MODE=oauth 활성 시)

Decision:
AUTH_MODE=oauth 모드에서 OAuth client 등록 우선순위는 다음과 같다.
1. Pre-registered: oauth_clients 테이블의 사전 등록 client
2. CIMD (Client ID Metadata Documents): client가 자신의 metadata를 well-known URL로 노출, CoreMCP가 fetch + 캐시 (TTL 1h)
3. DCR (Dynamic Client Registration): CIMD 미지원 client의 fallback

Rationale:
최신 MCP authorization / OpenAI ChatGPT Apps / Anthropic Claude Connectors가 CIMD를 권장 형식으로 채택. DCR은 abuse 위험이 크고 client metadata 검증이 약하다. CIMD는 client URL이 곧 identity이므로 brand impersonation 방어가 자연스럽다.

Consequences:
- AUTH_MODE=oauth 활성 시 CIMD endpoint 처리 우선
- CIMD metadata fetch는 SSRF guard 통과 (06-security-auth §7.5)
- CIMD 응답 size cap 32KB, content-type application/json
- DCR rate limit 10/hour, CIMD fetch rate limit 30/hour
- static_bearer 모드(default)에서는 본 ADR 적용 안 함
- 04-api-spec.md, 06-security-auth.md §4.4.1 참조
- CIMD response 검증 디테일: (1) fetched client_id == 요청 URL byte-exact, (2) content-type charset 허용, (3) TTL fixed 1h (downstream cache header 무시). 상세 06-security-auth §4.4.2

---

## ADR-037: Personal-first, SaaS-pattern selective absorption

Status: Accepted

Decision:
현재 저장소의 제품/구현 기준은 **personal CoreMCP gateway + 도구함 관리**다. `coremcp-design-patterns-to-absorb.md`와 `production_docs_donotuse/`에 포함된 SaaS 패턴은 참고 자료이며, 현재 구현 지시로 간주하지 않는다.

선별 흡수 가능한 패턴은 다음으로 제한한다.
- 개인 service registry와 validation metadata
- per-client token, credential vault, SSRF guard, request마다 bearer auth 재검증
- 개인 도구함의 service/tool 단위 제어
- schema drift, catalog sync, downstream partial failure 가시성
- local request_id/metrics/log 기반 observability
- 연결된 AI client 등록/검증 UX

team/workspace 멀티테넌시, public marketplace, publisher profile, verified badge, public review queue, billing/quota/abuse automation은 명시적인 사용자 요청과 활성 dev-plan phase/ADR이 생기기 전까지 장기 backlog 또는 제외 범위로만 유지한다.

Rationale:
CoreMCP는 먼저 본인이 24/7 사용할 수 있는 안정적인 개인 gateway가 되어야 한다. SaaS 문서의 패턴을 무비판적으로 구현하면 범위가 커지고, token boundary·credential boundary·운영 검증 같은 현재 핵심 안전 요건이 흐려진다. 반대로 registry metadata, tool-level control, drift visibility, local observability 같은 패턴은 personal scope에서도 직접적인 사용자 가치가 있다.

Consequences:
- 구현 계획과 dev-plan은 personal scope 섹션을 우선 해석한다.
- 신규 SaaS 패턴 문서는 설계 참고/장기 backlog로 읽고, 즉시 구현 태스크로 승격하지 않는다.
- SaaS 전환이 필요해지면 본 ADR을 Superseded 또는 Amended로 표시하고 `15-future-saas-migration.md`와 새 dev-plan phase에서 범위를 다시 연다.
- AGENTS.md의 편집 규칙은 이 ADR을 근거로 SaaS 기능 구현 확장을 차단한다.

---


## ADR-038: Bidirectional RPC Policy = Explicit Reject by Default

Status: Accepted

Decision:
Personal CoreMCP gateway는 bidirectional RPC를 기본 미지원으로 유지하고, 다음 server-to-client 또는 upstream-to-client 성격의 method를 명시적으로 reject한다.

- `sampling/createMessage`
- `elicitation/*`
- `roots/list`

현재 policy는 capability에 노출하지 않고, 수신 시 JSON-RPC `-32601 Method not found` 또는 동등한 unsupported-method 응답으로 종료하는 것이다. `Mcp-Session-Id`는 routing hint일 수는 있어도 인증 수단이 아니며, `/mcp` request마다 bearer auth를 다시 확인한다. CoreMCP admin/client token은 downstream 또는 다른 client 방향으로 전달하지 않는다.

Rationale:
Bidirectional RPC는 personal gateway에서도 다음 경계를 동시에 건드린다.

- **multi-client routing ambiguity**: 하나의 CoreMCP에 Claude Code, Codex CLI, Web Playground 등 여러 연결된 AI client가 붙을 때 downstream의 `sampling/createMessage` 또는 `roots/list` 요청을 어느 client/session으로 되돌려야 하는지 명확하지 않다.
- **LLM trust boundary**: downstream MCP가 gateway 뒤의 LLM에게 임의 prompt 생성을 요청하게 되면, 도구 제공자와 사용자의 의도/권한 경계가 흐려진다.
- **user consent / permission model 미정**: elicitation은 사용자 입력과 승인 UI가 필요한데, 현재 personal gateway에는 method별 동의 UX와 audit policy가 충분히 정의되어 있지 않다.
- **token boundary 유지**: bidirectional callback을 구현하면서 CoreMCP admin/client token, OAuth token, downstream credential이 잘못 섞이거나 재전송될 위험을 피해야 한다.

Future opt-in 조건:
Bidirectional RPC를 구현하려면 별도 ADR과 dev-plan phase에서 최소 다음 조건을 모두 만족해야 한다.

1. per-client allowlist: 연결된 AI client별로 허용 method와 downstream service를 제한한다.
2. explicit session binding: downstream request가 특정 authenticated client session에 명시적으로 묶여야 한다.
3. audit: 요청자, 대상 client, method, decision, user consent 결과를 원문 body 없이 기록한다.
4. user consent: elicitation/sampling/roots 노출 전 사용자 승인 UX와 revoke 경로를 제공한다.
5. admin toggle: global default-off toggle과 service/client 단위 override를 제공한다.
6. route policy: multi-client 상황에서 deny/fallback/timeout/error mapping을 문서화하고 테스트로 고정한다.

Consequences:
- ADR-014의 `sampling`/`elicitation` reject 원칙을 확장해 `roots/list`까지 포함한다.
- downstream MCP가 sampling, elicitation, roots callback에 의존하면 현재 CoreMCP에서는 incompatible 또는 degraded로 표시한다.
- service registration/capability scan은 관련 capability를 발견하면 경고하되, 자동으로 활성화하지 않는다.
- 외부 LLM API dependency를 추가하지 않는다. CoreMCP는 gateway이며 LLM service가 아니다.
- 구현자가 bidirectional RPC를 추가하려면 본 ADR을 Amended/Superseded로 바꾸고 위 opt-in 조건을 먼저 충족해야 한다.

---

## ADR-039: STDIO Transport with Command Allowlist

Status: Accepted

Decision:
CoreMCP는 personal gateway scope에서 STDIO downstream MCP를 지원하되, `stdio_command`는 basename allowlist를 통과해야 한다. 기본 allowlist는 `npx,uvx,python,python3,node,docker,deno`이며 `COREMCP_STDIO_ALLOWED_COMMANDS`로 운영자가 확장할 수 있다.

적용 지점:
- `/v1/mcp-services` create/update validation
- runtime STDIO client construction
- audit action `service.stdio_command_rejected`

Rationale:
초기 ADR-008은 stdio 직접 호스팅을 금지했지만, CoreMCP가 multi-MCP personal gateway로 발전하면서 community MCP 흡수를 위해 STDIO transport가 필요해졌다. 다만 STDIO는 host process 실행면을 만들기 때문에 absolute path 검증만으로는 부족하다. LiteLLM MCP management endpoint의 command injection 계열 취약 사례처럼 attacker-controlled command/args가 preview 또는 management path로 들어오는 위험을 줄이려면 command allowlist가 defense-in-depth로 필요하다.

Consequences:
- 기본 설정에서 `/bin/sh`, `bash`, `zsh`, `curl` 같은 shell/downloader command는 차단된다.
- 운영자가 필요한 runtime을 추가하려면 command basename만 `COREMCP_STDIO_ALLOWED_COMMANDS`에 명시한다.
- 거부 응답과 audit에는 full path/args/env를 남기지 않고 basename과 reason만 기록한다.
- STDIO는 여전히 admin-controlled 기능이며, env sanitize, process cap, idle timeout, crash-state persistence와 함께 사용한다.
- macOS sandbox/container isolation은 별도 ADR 전까지 장기 backlog다.

---

## ADR-040: Plugin Hook Failure Policy = Fail Closed

Status: Accepted

Decision:
CoreMCP의 in-process Plugin Framework는 기본 empty registry로 동작하며, plugin hook 예외가 발생하면 해당 `tools/call`을 fail-closed 처리한다.

정책:
- `before_tool_call` 실패: downstream 호출 전에 차단한다.
- `after_tool_response` 실패: downstream 결과를 client에 전달하지 않고 차단한다.
- client 응답은 JSON-RPC protocol error가 아니라 tool-level `isError=true` 결과로 반환한다.
- audit action은 `plugin.error`를 사용한다.
- audit metadata에는 `tool`, `plugin_name`, `stage`, `error_type`만 남긴다.
- raw tool arguments/result는 audit/log에 저장하지 않는다.

Rationale:
Plugin은 redaction, deny-list, policy enforcement처럼 trust boundary에 가까운 위치에서 실행될 수 있다. Plugin 실패 시 fail-open으로 downstream 호출이나 미검증 결과 전달을 계속하면 redaction 우회 또는 policy bypass가 될 수 있다. 반대로 unhandled exception으로 500을 내면 MCP client 경험과 invocation audit가 불안정해진다. 따라서 fail-closed tool error + sanitized audit를 기본 정책으로 고정한다.

Consequences:
- 잘못된 plugin은 해당 tool call을 차단하므로 운영자는 audit에서 `plugin.error`를 확인하고 plugin을 수정/비활성화해야 한다.
- Plugin failure는 downstream failure가 아니므로 circuit breaker failure로 기록하지 않는다.
- `after_tool_response` 실패는 downstream 호출 자체는 성공한 것으로 간주하지만, client에는 결과를 반환하지 않는다.
- 외부 plugin loading과 built-in plugin 도입은 별도 dev-plan과 보안 검토 전까지 추가하지 않는다.

---

## ADR-041: Plugin Built-in Adoption Guardrails

Status: Accepted

Decision:
CoreMCP는 Plugin Framework를 closed-by-default boundary로 유지한다. Built-in plugin을 도입하기 전에는 plugin별 ADR 또는 dev-plan phase에서 다음 조건을 먼저 만족해야 한다.

선행 조건:
- **default-off**: built-in plugin은 기본 비활성 상태로 추가한다.
- **allowlist activation**: 활성화 가능한 plugin 이름과 hook stage를 명시적으로 allowlist한다.
- **fail-closed inheritance**: ADR-040의 `PluginExecutionError` + tool-level `isError=true` 정책을 그대로 따른다.
- **no raw payload persistence**: raw tool arguments/result, resource contents, prompt messages를 audit/log/debug trace에 저장하지 않는다.
- **redaction-first audit**: audit metadata는 plugin name, stage, decision, reason code, error type 같은 구조화된 최소 정보만 포함한다.
- **deterministic order**: 여러 plugin이 활성화될 경우 실행 순서와 short-circuit 규칙을 테스트로 고정한다.
- **scope boundary**: 외부 plugin loading, dynamic Python import, network fetch, marketplace/plugin registry는 별도 ADR 전까지 금지한다.

예외 처리 정책:
- built-in plugin은 직접 임의 exception을 밖으로 노출하지 않고 `PluginExecutionError`로 감싼다.
- client 응답의 reason은 stable code만 사용하고 Python exception message, stack trace, raw payload fragment는 노출하지 않는다.
- audit에는 `plugin_name`, `stage`, `decision`, `reason_code`, `error_type`만 남긴다.
- plugin error는 downstream health/circuit breaker 실패로 기록하지 않는다.
- plugin이 redaction/policy 판단을 완료하지 못하면 fail-closed가 기본이다.

Hook 범위 정책:
- 현재 안정화 단계의 hook 적용 범위는 `tools/call`로 제한한다.
- `resources/read`, `prompts/get`은 raw content와 prompt message를 직접 다루므로 built-in plugin별 보안 ADR과 전용 테스트가 생기기 전까지 hook 확장을 보류한다.
- hook 범위를 넓히는 경우 content size limit, redaction ordering, audit minimization, idempotency/cache 상호작용을 먼저 테스트로 고정한다.
- Plugin Framework는 personal gateway 내부 확장점이지 SaaS plugin marketplace가 아니다.

Rationale:
Plugin은 도구 호출 경계에서 request/response를 관찰하거나 수정할 수 있으므로 작은 built-in이라도 policy bypass, data retention, prompt injection, credential exposure 위험을 만든다. 현재 CoreMCP는 personal gateway이며 SaaS plugin ecosystem이 아니므로, built-in plugin은 기능 확장보다 안정성과 명시적 운영 제어를 우선해야 한다.

Consequences:
- 첫 built-in plugin을 추가하려면 본 ADR의 선행 조건을 dev-plan 체크리스트에 포함한다.
- Plugin hook 범위를 `resources/read`, `prompts/get` 등으로 넓힐 경우 raw content 보존 금지와 fail-closed 동작을 먼저 테스트한다.
- built-in plugin 도입 전까지 resources/prompts hook은 의도적으로 비활성 상태를 유지한다.
- Q-2 검토 결과(2026-05-16): `resources_handlers.py` / `prompts_handlers.py`에 default empty registry hook만 연결하는 것도 hook 범위 확장이므로, built-in plugin별 보안 ADR 및 raw content/prompt message fail-closed 테스트 전까지 코드 구현을 보류한다.
- 외부 plugin loading은 personal scope 안정화 이후에도 기본 제외 범위로 유지한다.
- Plugin 관련 보안 회귀 테스트는 `tests/test_plugins.py` 또는 해당 hook 통합 테스트에 추가한다.

---
## ADR-042: main.py decomposition into mcp_gateway sub-modules

Status: Accepted (2026-05-22)

Context:
`apps/api/coremcp/main.py` 가 1,955 lines 까지 성장했다 (HTTP middleware + MCP dispatch + stdio process pool + rate limiter + OAuth + 헬스 프로브 + service drift detector 등). 단일 파일에 70+ top-level helper 가 응집해 있어 리뷰 가능성, 단위 테스트 가능성, hot reload 안정성이 모두 손상되었다. 직전 프로덕션 품질 리뷰 (`dev-plan/implement_20260522_183112.md`) 에서 아키텍처 영역 7.2/10 의 주요 감점 사유로 지목됨.

Decision:
main.py 를 **동작 보존 우선** 으로 `coremcp/mcp_gateway/*.py` 하위 모듈에 점진 분해한다. 시그니처 / 응답 shape / 로그 메시지 / 라우트 경로는 모두 동일하게 유지한다.

첫 cycle 의 분해 범위 (2026-05-22):
- **`coremcp/mcp_gateway/responses.py`** 신설 — `jsonrpc_result`, `jsonrpc_error`, `api_error`, `accepted`, `not_found`, `tool_error_result`, `JSONRPC_VERSION`. 외부 의존 없는 pure response builder. main.py 1,955 → 1,915 lines.

Next cycle 후보 (이번 cycle 에서 시도했으나 app-facade refactor 가 선행되어야 안전):
- `coremcp/mcp_gateway/health_probe.py` — `_probe_service_health`, `_run_service_health_probe_once`, `_run_service_health_probe_loop`. `_request_service_rpc`, `_persist_stdio_state`, `_detect_service_tool_schema_drift`, `validate_service` 모두 `app.state` 결합 → DI container 또는 facade 가 선행 필요.
- `coremcp/mcp_gateway/stdio_pool.py` — `_ensure_stdio_client_capacity_locked`, `_close_stdio_client_for_service`, `_stdio_client_for_config`, `_persist_stdio_state` 등. 동일하게 app facade 선행 필요.

Constraints:
- 각 추출 후 `make test` (209 tests) 통과 필수.
- 함수 시그니처 변경 금지 (호출 사이트 안정성 보장).
- 보안 모듈 (`proxy/security.py`, `proxy/stdio.py` 의 command allowlist) 은 본 분해 범위 밖.

Rationale:
god module 은 personal gateway 단계에서는 작동했지만, 다음 cycle 의 분산 rate limiter / response_model / scope decorator 도입 시 매번 main.py 의 깊은 부분을 건드려야 해서 회귀 위험이 누적된다. dispatch helpers 부터 안전 패턴을 확립한다.

Consequences:
- main.py 의 helper 가 mcp_gateway/* 하위 모듈로 이동될 때마다 import 만 추가되고 정의는 한 곳에 둔다 (중복 정의 금지).
- 다음 cycle 의 첫 작업은 `app.state` 를 감싸는 facade (e.g., `AppContext`) 를 도입해 health_probe / stdio_pool 추출을 가능하게 한다.
- 본 ADR 은 personal scope 의 internal refactor 이며 SaaS plugin / multi-tenant 와 무관하다.

---
## Superseded / Future Migration

다음 ADR은 SaaS 전환 시 Superseded:
- ADR-020 (Data Region)
- ADR-021 (Pricing)
- ADR-022 (License)
- ADR-027 (Right-to-Erasure)

전환 절차: `15-future-saas-migration.md`.

production_docs_donotuse/13-adr.md의 ADR-011 (Logto), ADR-012 (AWS KMS), ADR-013 (tools-only), ADR-014 (sampling reject), ADR-022 (DCR), ADR-016 (tool_aliases), ADR-017 (RLS), ADR-018 (3-tier cache), ADR-019 (dot naming), ADR-021 (JWT RS256), ADR-023 (region), ADR-024 (pricing), ADR-025 (license) 중 일부는 본 ADR-011/012/013/014/015/016/017/020/021/022로 매핑되었다.

production_docs_donotuse/13-adr.md의 ADR-008 (No Token Passthrough), ADR-014 (sampling reject)는 본 ADR-004와 ADR-014로 연속 적용 중. 본 문서팩에 신규 추가된 ADR-029~035는 P0 검토 결과 보완분.
ADR-036 (CIMD)은 AUTH_MODE=oauth 활성 시점에 발효되며, 그 전까지는 latent state.
