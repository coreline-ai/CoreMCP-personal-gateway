# CoreMCP Architecture Decision Records

문서 버전: v0.1

---

## ADR-001: Product Concept is Toolbox First

Status: Accepted

Decision:

CoreMCP의 사용자-facing 개념은 Gateway가 아니라 Toolbox로 정의한다.

Rationale:

사용자는 gateway를 구매하지 않는다. 사용자는 “Claude Code/ChatGPT에서 내 MCP 도구들을 한 번에 쓰는 것”을 원한다. Gateway는 내부 구현 용어로 유지한다.

Consequences:

- 주요 내비게이션은 Toolbox 중심
- Marketplace/Developer Console은 Toolbox를 보조
- 카피에서 “proxy”, “aggregator”보다 “toolbox”, “connect once”를 우선

---

## ADR-002: Proxy Mode over Meta-tool Mode

Status: Accepted

Decision:

CoreMCP는 `invoke_tool` 하나를 제공하는 meta-tool mode가 아니라, downstream tools를 개별 exposed tools로 재노출하는 proxy mode를 사용한다.

Rationale:

LLM이 개별 tool을 자연스럽게 선택할 수 있다. 외부 AI client에서 사용자가 tool catalog를 명확히 볼 수 있다.

Consequences:

- tool alias/cache 시스템 필요
- name collision 처리 필요
- dynamic tools/list 구현 필요

---

## ADR-003: Streamable HTTP First

Status: Accepted

Decision:

CoreMCP MCP endpoint는 Streamable HTTP를 우선 지원한다.

Rationale:

Remote/cloud MCP 서버에는 HTTP transport가 가장 적합하다. SSE는 deprecated 흐름이므로 MVP의 primary target이 아니다.

Consequences:

- `/mcp` POST 필수
- GET SSE는 optional
- session id/header 처리 필요

---

## ADR-004: No Token Passthrough

Status: Accepted

Decision:

CoreMCP access token은 downstream MCP service에 전달하지 않는다.

Rationale:

OAuth audience boundary와 MCP security requirement를 지키기 위함이다.

Consequences:

- downstream credential vault 필수
- credential resolver 필요
- integration test 필수

---

## ADR-005: MVP Downstream Auth is Bearer/API Key Vault

Status: Accepted

Decision:

MVP에서는 delegated OAuth보다 bearer token/API key vault를 먼저 지원한다.

Rationale:

구현 범위를 줄이고 end-to-end product value를 빠르게 검증하기 위함이다.

Consequences:

- OAuth-based downstream services는 Phase 3로 미룸
- credential vault 보안 중요도 증가

---

## ADR-006: Public Marketplace Not in First MVP

Status: Accepted

Decision:

첫 MVP는 private MCP registry + toolbox + external client connection에 집중한다.

Rationale:

Public marketplace는 review, abuse, security, ranking, legal 문제가 크다.

Consequences:

- visibility field는 선반영
- public listing UI는 later
- review_pending flow는 설계만 반영

---

## ADR-007: No Stdio Hosting in MVP

Status: Accepted

Decision:

CoreMCP MVP는 stdio MCP 서버를 직접 호스팅하지 않는다.

Rationale:

stdio/local command 실행은 RCE와 sandboxing 문제가 크다.

Consequences:

- Remote HTTP MCP만 등록 가능
- local MCP는 사용자가 별도 remote wrapper를 만들어야 함

---

## ADR-008: API Server and MCP Gateway Same Process for MVP

Status: Accepted

Decision:

MVP에서는 API Server와 MCP Gateway를 같은 FastAPI app에 둔다.

Rationale:

개발 속도와 배포 단순성을 우선한다.

Consequences:

- traffic 증가 시 분리 필요
- code module boundary를 명확히 유지해야 함

---

## ADR-009: Store Invocation Metadata, Not Raw Bodies

Status: Accepted

Decision:

tool arguments/output 원문은 기본 저장하지 않는다.

Rationale:

개인정보, secret, 업무 데이터 유출 위험을 줄인다.

Consequences:

- debugging에는 제한이 있음
- opt-in debug trace 기능은 추후 추가

---

## ADR-010: Workspace Schema Pre-Baked

Status: Accepted

Decision:

MVP가 개인용이어도 DB에는 workspace 구조를 선반영한다.

Rationale:

팀/기업 기능으로 확장할 때 migration 비용을 줄인다.

Consequences:

- 초기 구현은 owner_user_id 중심
- workspace_id는 nullable 또는 default workspace 사용

---

## ADR-011: Authorization Server = Logto Self-hosted

Status: Accepted

Decision:

OAuth 2.1 AS는 Logto를 self-host로 운영하고, CoreMCP는 resource server로 동작한다.

Rationale:

Claude Code/Claude/ChatGPT/Cursor가 DCR(RFC 7591)을 요구하는데 Logto가 native 지원한다. PKCE/custom scope/JWKS rotation 모두 OOTB로 제공된다. Auth0 대비 비용이 낮고, Clerk/Supabase Auth는 DCR을 지원하지 않는다.

Consequences:

- Logto deployment 운영 부담
- JWKS rotation 정책 수립 필요
- 자체 consent screen 구성 필요

---

## ADR-012: KMS Provider = AWS KMS Envelope Encryption

Status: Accepted

Decision:

credential vault는 AWS KMS envelope encryption + DB ciphertext 컬럼 구조로 구현한다.

Rationale:

managed rotation, HSM-backed, multi-region replica가 가능하다. libsodium app-side는 key rotation이 약하고 Vault는 운영 부담이 크다.

Consequences:

- AWS lock-in
- KMS API call latency 10-50ms 감안 필요
- cross-region 복제 정책 수립 필요

---

## ADR-013: MVP MCP Capability = tools-only

Status: Accepted

Decision:

MVP는 tools만 노출한다. resources/prompts/completions/logging/sampling/elicitation은 미지원으로 server capabilities에 명시적으로 omit 또는 null declare한다.

Rationale:

scope 축소로 빠른 출시가 가능하다. resources/prompts는 user value 검증이 부족하고, sampling/elicitation은 PII 위험이 크다.

Consequences:

- downstream에서 sampling/elicitation 요청 시 `-32601` 반환
- Phase 3 이후 확장 검토

---

## ADR-014: Sampling / Elicitation Policy = Reject

Status: Accepted

Decision:

server-to-client `sampling/createMessage`, `elicitation/create`는 MVP에서 JSON-RPC `-32601 Method not found`로 reject한다.

Rationale:

client capabilities 협상 복잡도와 PII leak 위험이 크다. tools만으로 MVP 가치는 충분하다.

Consequences:

- downstream MCP가 sampling에 의존하면 호환성 떨어짐
- service registration 시 capability scan으로 warning 노출

---

## ADR-015: GET SSE Minimal Implementation

Status: Accepted

Decision:

`GET /mcp`는 SSE empty stream + keepalive(15s ping)만 구현한다. server-initiated notifications(tools/list_changed)는 이 채널로 emit한다.

Rationale:

`tools.listChanged: true` capability 선언과 일관성을 맞춘다. 405 반환 시 client가 stale catalog를 갖게 된다. 완전한 SSE 구현은 부담이 크다.

Consequences:

- 연결 유지 비용
- pod 당 동시 SSE 연결 수 모니터링 필요

---

## ADR-016: tool_aliases Separate Table

Status: Accepted

Decision:

`service_tools.exposed_name`은 immutable한 첫 노출명으로 고정하고, rename은 별도 `tool_aliases` 테이블로 관리한다. 외부 AI가 학습한 tool name은 불변으로 유지한다.

Rationale:

service slug 변경 시에도 LLM이 기억한 tool name이 유효해야 한다.

Consequences:

- 추가 테이블 운영
- lookup 우선순위(primary alias) 결정 필요
- migration 시 backfill 필요

---

## ADR-017: PostgreSQL Row-Level Security Enabled

Status: Accepted

Decision:

user-owned 모든 테이블에 RLS 정책을 적용한다. Application은 connection 직후 `SET LOCAL app.user_id`를 실행한다.

Rationale:

IDOR(R-004)에 대한 deep defense다. application `WHERE` 누락 시에도 DB 레벨에서 차단된다.

Consequences:

- connection pooling 시 SET 비용
- superuser query 우회 주의
- migration 부담 증가

---

## ADR-018: Tool Catalog 3-tier Cache

Status: Accepted

Decision:

L1 in-process LRU(60s) + L2 Redis(1h, per user) + L3 PostgreSQL service_tools(24h)로 3-tier cache를 구성한다. invalidation은 Redis pub/sub로 fan-out한다.

Rationale:

pod 간 cache coherence와 단일 user의 빈번한 list 호출을 동시에 처리해야 한다.

Consequences:

- Redis 의존성 증가
- cache key 표준 `cache:catalog:user:{user_id}`
- invalidation 누락 시 stale 위험

---

## ADR-019: Tool Naming Format = Dotted

Status: Accepted

Decision:

exposed tool name 형식은 `{service_slug}.{tool_name}`으로 한다. underscore fallback은 client 비호환 발견 시에만 적용한다.

Rationale:

ChatGPT/Claude/Cursor UI에서 namespace로 인식되고 가독성이 우수하다. LLM tool selection에서 service 의도가 명확하다.

Consequences:

- dot을 split하는 client 발견 시 underscore 변형 필요
- client profile 테스트 필수

---

## ADR-020: Worker Queue = Arq

Status: Accepted

Decision:

async background job은 Arq(Redis 기반, asyncio native)로 처리한다.

Rationale:

Celery는 prefork 모델로 asyncio와 충돌하고, RQ는 sync 기반이다. FastAPI/httpx async 스택과 일관된다.

Consequences:

- Redis 단일 의존성
- Arq 생태계가 작음

---

## ADR-021: Token Format = JWT RS256

Status: Accepted

Decision:

CoreMCP access token은 RS256 JWT를 사용한다. claims는 iss/sub/aud/exp/jti/scope/external_connection_id/protocol_version으로 구성한다.

Rationale:

stateless 검증으로 latency가 낮고, audience binding이 명확하며, jti로 revocation denylist가 가능하다.

Consequences:

- JWKS endpoint 운영 필요
- private key rotation 정책 필요
- revocation은 jti denylist Redis에 의존

---

## ADR-022: Dynamic Client Registration Enabled

Status: Accepted

Decision:

`POST /oauth/register` (RFC 7591)을 enable한다. client_secret은 hash로 저장한다.

Rationale:

Claude Code/Claude/ChatGPT/Cursor가 사전 등록 없이 DCR로 client를 생성한다. 미제공 시 호환성 실패가 발생한다.

Consequences:

- DCR abuse 위험 → rate limit + client_metadata 검증 필요
- 정기 정리(unused after 90d) 필요

---

## ADR-023: Data Region = Local Mac mini (Personal Project)

Status: Accepted

Decision:

본 프로젝트는 개인 프로젝트로, Mac mini 단일 호스트에서 실행한다. 모든 데이터(PostgreSQL 또는 SQLite, Redis 또는 in-memory, secrets, logs)는 로컬 디스크에 저장한다. region/replication/multi-AZ 개념을 적용하지 않는다. 외부 노출이 필요할 경우 Tailscale 또는 Cloudflare Tunnel을 선택적으로 사용한다.

Rationale:

타겟 사용자는 본인 1명, 운영 환경은 Mac mini, 한국어 우선. SaaS급 region 결정은 적용 불가하다. 향후 다인 사용 SaaS로 확장할 경우 본 ADR을 재검토한다.

Consequences:

- 16-compliance.md의 data residency 절은 적용 불가 (단일 사용자, 본인 데이터)
- multi-region / DR / cross-region backup 무관
- 15-personal-implementation.md가 실제 실행 명세
- 향후 SaaS 전환 시 본 ADR을 Superseded로 표시하고 새 ADR 작성

---

## ADR-024: Pricing Model = None (Personal Project)

Status: Accepted

Decision:

본 프로젝트는 개인 사용 목적이므로 과금 시스템을 구현하지 않는다. 14-pricing.md는 미래 SaaS 전환 시 reference로 보존만 한다.

Rationale:

본인 1명 사용. billing/quota/Stripe 통합은 불필요한 복잡도다.

Consequences:

- billing_usage_counters / api_keys / workspace plan 등 billing 관련 schema 미구현
- Stripe 통합 없음
- quota 관련 코드는 sanity check 수준만 (예: 동시 호출 10개 cap)
- 14-pricing.md는 reference, 코드 미반영
- 향후 SaaS 전환 시 본 ADR을 Superseded로 표시

---

## ADR-025: License = Private Repository (Personal Project)

Status: Accepted

Decision:

본 프로젝트는 GitHub Private repository로 유지한다. 라이선스 파일을 추가하지 않으며, 기본 저작권(All rights reserved)을 유지한다. 추후 공개 결정 시 MIT 또는 Apache 2.0을 검토한다.

Rationale:

본인 사용 목적의 개인 프로젝트로 외부 공개/배포 계획이 없다. ToS/Privacy/DPA/CLA 등 법무 문서가 불필요하다.

Consequences:

- 외부 기여 수용 없음
- 공개 marketplace / OSS SDK 등 외부 노출 영역 없음
- 향후 공개 시 라이선스 추가 + 본 ADR을 Superseded로 표시
