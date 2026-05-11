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

Status: Accepted

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
