# CoreMCP PRD

문서 버전: v0.1  
제품: CoreMCP  
제품 유형: MCP Toolbox + Authenticated MCP Gateway SaaS  
대상 릴리스: MVP / Private Beta

---

## 1. 제품 요약

CoreMCP는 사용자가 여러 MCP 서버를 등록하거나 선택해 개인/팀 도구함에 담고, Claude Code, Claude, ChatGPT, OpenClaw 같은 외부 AI 클라이언트에는 CoreMCP 하나만 연결해 도구함 전체를 인증 기반으로 사용할 수 있게 해주는 SaaS다.

CoreMCP는 사용자의 MCP 도구함을 하나의 protected remote MCP server처럼 노출한다. 외부 AI 클라이언트는 CoreMCP의 `/mcp` endpoint 하나만 등록한다. CoreMCP는 로그인한 사용자와 연결된 toolbox를 기준으로 사용 가능한 tool catalog를 생성하고, 실제 실행은 등록된 downstream MCP service로 proxy한다.

---

## 2. 제품 비전

### 2.1 Vision

AI 클라이언트마다 MCP 서버를 반복 등록하지 않고, 사용자가 자신의 MCP 도구들을 한곳에 모아 어디서나 사용할 수 있게 한다.

### 2.2 Mission

CoreMCP는 MCP 생태계의 “도구함 계층”이 된다.

- 개발자는 remote MCP 서버를 쉽게 등록하고 테스트한다.
- 사용자는 필요한 MCP를 도구함에 담는다.
- 외부 AI 클라이언트는 CoreMCP 하나만 연결한다.
- 조직은 MCP 사용을 정책, 감사, 보안 기준으로 통제한다.

### 2.3 Product Promise

```text
Connect once. Use every MCP tool anywhere.
```

---

## 3. 문제 정의

### 3.1 현재 문제

MCP 생태계가 확장되면서 사용자는 다음 문제를 겪는다.

1. AI 클라이언트마다 MCP 서버를 개별 등록해야 한다.
2. MCP 서버마다 로그인, 토큰, API key를 따로 관리해야 한다.
3. 어떤 MCP가 안전하고 유용한지 판단하기 어렵다.
4. 팀/조직 단위로 tool 사용 권한을 통제하기 어렵다.
5. 개발자는 만든 MCP 서버를 여러 AI 클라이언트에서 테스트하기 어렵다.
6. 외부 AI 연결용 OAuth, one-time token, credential vault를 직접 구현해야 한다.

### 3.2 목표 상태

1. 사용자는 CoreMCP에 로그인한다.
2. 사용자는 필요한 MCP 서버를 등록하거나 marketplace에서 선택한다.
3. 사용자는 MCP를 자신의 toolbox에 담는다.
4. Claude Code/Claude/ChatGPT 등은 CoreMCP 하나만 연결한다.
5. CoreMCP는 사용자의 toolbox 기준으로 tool을 노출한다.
6. CoreMCP는 downstream MCP 호출, 인증, 감사 로그를 처리한다.

---

## 4. 대상 사용자

### 4.1 Persona A: 개인 개발자

- 직접 만든 MCP 서버를 Claude Code에서 사용하고 싶다.
- 매번 로컬 설정이나 토큰 관리를 하고 싶지 않다.
- GitHub, Notion, Linear, Slack 같은 업무 도구 MCP를 묶어서 쓰고 싶다.

성공 기준:

- 10분 안에 Remote MCP URL 등록
- Claude Code에 CoreMCP 연결
- 첫 tool call 성공

### 4.2 Persona B: MCP 서버 개발자

- 자신이 만든 MCP 서버를 테스트하고 배포하고 싶다.
- 실제 LLM 대화에서 tool description과 schema가 잘 작동하는지 보고 싶다.
- 추후 public marketplace에 공개하고 싶다.

성공 기준:

- 등록 validation report 확인
- playground에서 tools/list/tools/call 테스트
- public submission 가능

### 4.3 Persona C: 팀 관리자

- 팀 구성원이 사용할 MCP를 승인하고 싶다.
- 특정 tool은 read-only로 제한하고 싶다.
- 사용량, 실패율, 호출 로그를 보고 싶다.

성공 기준:

- workspace toolbox 구성
- tool allowlist/denylist 적용
- audit log export

### 4.4 Persona D: AI power user

- ChatGPT/Claude/OpenClaw 등 여러 AI 환경에서 동일한 도구함을 쓰고 싶다.
- 한 번 로그인하고 연결된 서비스에서 자연스럽게 tool을 쓰고 싶다.

성공 기준:

- 여러 client 연결
- 연결된 client 해제
- 도구함 enable/disable 반영

---

## 5. 핵심 사용자 여정

### 5.1 개인 MCP 등록 후 Claude Code에서 사용

```text
1. 사용자가 CoreMCP 가입/로그인
2. “New MCP Service” 클릭
3. Remote MCP endpoint URL 입력
4. CoreMCP가 initialize/tools/list 검증
5. 사용자가 service 이름, 설명, visibility=private 저장
6. 사용자가 기본 toolbox에 추가
7. CoreMCP가 exposed tool names 생성
8. 사용자가 Claude Code 연결 가이드에서 명령 복사
9. Claude Code에서 CoreMCP remote HTTP server 등록
10. OAuth 로그인
11. Claude Code가 tools/list 호출
12. CoreMCP가 toolbox tools 반환
13. Claude Code가 tool call
14. CoreMCP가 downstream MCP로 proxy
15. 결과 반환 및 audit log 기록
```

### 5.2 Marketplace MCP를 도구함에 추가

```text
1. 사용자가 Marketplace 진입
2. category/search로 MCP 탐색
3. MCP detail에서 tool 목록과 권한 확인
4. “Add to Toolbox” 클릭
5. 인증 필요한 서비스면 connection flow 진행
6. toolbox item enabled=true
7. 외부 AI client 다음 tools/list 시 반영
```

### 5.3 OpenClaw 같은 로컬/오픈소스 에이전트 연결

```text
1. 사용자가 CoreMCP toolbox에서 “Connect OpenClaw” 클릭
2. 제공 데이터/권한 consent
3. CoreMCP가 10분 만료 one-time connection token 생성
4. 연결 프롬프트 생성
5. 사용자가 프롬프트를 OpenClaw 채팅창에 붙여넣기
6. OpenClaw가 token exchange 진행
7. CoreMCP가 external connection 발급
8. 사용자는 Settings > Connected Clients에서 해제 가능
```

---

## 6. MVP 범위

### 6.1 Must Have

다음 [00-executive-review.md §5.2 MVP 필수 범위](00-executive-review.md) 14항목과 1:1 대응:

| 00 MVP 항목 | 01 PRD Must Have 매핑 |
|---|---|
| 1. 사용자 로그인 | Account/Auth |
| 2. Remote MCP 등록 | MCP Registry |
| 3. MCP initialize / tools/list 검증 | MCP Registry (validation) |
| 4. tool schema cache | MCP Registry |
| 5. 기본 도구함 생성 | Toolbox |
| 6. 도구함에 MCP 추가/삭제 | Toolbox |
| 7. CoreMCP /mcp endpoint | MCP Gateway |
| 8. OAuth protected resource metadata | MCP Gateway |
| 9. Claude Code remote HTTP 연결 | Claude Code 연결 |
| 10. tools/list 사용자별 동적 노출 | MCP Gateway |
| 11. tools/call downstream proxy | MCP Gateway |
| 12. bearer/api-key downstream credential vault | MCP Registry (credential) |
| 13. audit log | (FR-010) |
| 14. 연결 해제 | Connected Clients (FR-008) |

#### Account/Auth

- 이메일/소셜 로그인 중 1개 이상
- user profile
- session management
- OAuth authorization server 또는 OIDC provider 연동
- CoreMCP MCP access token 발급/검증

#### MCP Registry

- private MCP service 등록
- endpoint URL 입력
- auth type 선택: `none`, `bearer_token`, `api_key_header`
- credential 암호화 저장
- initialize validation
- tools/list validation
- schema cache
- 등록 상태 관리: `draft`, `active`, `error`, `disabled`

#### Toolbox

- 사용자별 default toolbox 자동 생성
- toolbox item 추가/삭제
- enable/disable
- toolbox 기준 tool catalog 생성

#### MCP Gateway

- `/mcp` Streamable HTTP endpoint
- OAuth protected resource metadata
- initialize 처리
- tools/list 처리
- tools/call 처리
- exposed tool name → service/tool mapping
- downstream MCP proxy
- audit log

#### Claude Code 연결

- Claude Code 연결 명령 제공
- OAuth 로그인 플로우
- bearer header 연결 fallback 안내

#### Admin/Developer Console

- 내 MCP services 목록
- service detail
- validation report
- cached tools 목록
- test tool call
- invocation log

### 6.2 Should Have

- one-time connection token
- connected clients 관리
- playground chat-lite
- schema refresh button
- tool alias 수동 수정
- service logo/category
- basic rate limit

### 6.3 Could Have

- public marketplace
- review queue
- verified badge
- delegated OAuth
- workspace/team toolbox
- usage-based billing
- OpenAPI export

### 6.4 Won't Have in MVP

- 복잡한 policy engine
- usage billing
- multi-region deployment
- native desktop app
- full marketplace moderation system
- all MCP client compatibility guarantee
- arbitrary stdio MCP hosting

---

## 7. 기능 요구사항

### FR-001 사용자 가입/로그인

사용자는 CoreMCP에 가입하고 로그인할 수 있어야 한다.

Acceptance Criteria:

- 로그인 성공 시 dashboard로 이동한다.
- 최초 로그인 시 default toolbox가 생성된다.
- user id는 모든 toolbox, service, invocation의 owner key로 사용된다.

### FR-002 MCP Service 등록

사용자는 Remote MCP endpoint URL을 등록할 수 있어야 한다.

Acceptance Criteria:

- URL은 HTTPS만 허용한다.
- localhost, private IP, link-local, metadata IP는 차단한다.
- 등록 시 initialize check가 실행된다.
- tools/list check가 실행된다.
- 실패 시 validation error를 보여준다.
- 성공 시 service status는 active가 된다.

### FR-003 Credential 저장

사용자는 downstream MCP 호출에 필요한 credential을 등록할 수 있어야 한다.

Acceptance Criteria:

- credential 원문은 DB에 평문 저장되지 않는다.
- UI에서는 credential 마지막 4자리 또는 masked value만 표시한다.
- credential은 rotation 가능해야 한다.
- credential 삭제 시 연결된 service는 auth_required/error 상태가 된다.

### FR-004 Tool Schema Cache

CoreMCP는 downstream MCP의 tool schema를 캐시해야 한다.

Acceptance Criteria:

- original tool name, description, input schema를 저장한다.
- exposed tool name을 생성한다.
- schema hash를 계산한다.
- schema 변경 시 이전 hash와 비교해 변경 이벤트를 기록한다.

### FR-005 Toolbox 관리

사용자는 MCP service를 자신의 toolbox에 추가/삭제할 수 있어야 한다.

Acceptance Criteria:

- toolbox에 추가된 service의 enabled tools만 외부 AI에 노출된다.
- toolbox에서 disable하면 다음 tools/list에 반영된다.
- service 삭제 시 toolbox item은 soft-delete 또는 inactive 처리된다.

### FR-006 CoreMCP tools/list

외부 AI client가 tools/list를 호출하면 CoreMCP는 현재 사용자 toolbox 기준으로 tool catalog를 반환해야 한다.

Acceptance Criteria:

- access token으로 user_id를 식별한다.
- user_id의 default toolbox를 조회한다.
- enabled toolbox items만 포함한다.
- disabled service/tool은 제외한다.
- tool name 충돌이 없어야 한다.
- 반환되는 tool name은 deterministic해야 한다.

### FR-007 CoreMCP tools/call

외부 AI client가 tool을 호출하면 CoreMCP는 downstream MCP로 proxy해야 한다.

Acceptance Criteria:

- access token 검증 실패 시 401
- toolbox에 없는 tool 호출 시 JSON-RPC error
- 연결 안 된 service 호출 시 connect_required error
- downstream timeout 시 timeout error
- 성공/실패 모두 invocation log 기록
- downstream token은 CoreMCP access token과 분리되어야 한다.

### FR-008 Connected Clients

사용자는 연결된 외부 AI client를 볼 수 있어야 한다.

Acceptance Criteria:

- client type, name, last used, created at 표시
- revoke 가능
- revoke 후 해당 client token은 사용할 수 없다.

### FR-009 One-Time Connection Token

OAuth 연결이 어려운 client를 위해 one-time token을 발급할 수 있어야 한다.

Acceptance Criteria:

- token은 한 번만 사용 가능하다.
- token TTL 기본값은 10분이다.
- token 원문은 저장하지 않고 hash만 저장한다.
- 사용 후 external connection으로 교환된다.
- 만료/사용/취소 상태를 구분한다.

### FR-010 Audit Log

CoreMCP는 보안/실행 이벤트를 감사 로그로 저장해야 한다.

Acceptance Criteria:

- login, service create/update/delete, credential rotate, toolbox add/remove, client connect/revoke, tool call을 기록한다.
- request_id/correlation_id를 포함한다.
- 민감한 request/response body는 원문 저장하지 않는다.

### FR-011 Tool Argument / Response Size Limit

CoreMCP는 비정상적으로 큰 payload를 거부해야 한다.

Acceptance Criteria:

- request body > 1MB rejected with `body_too_large`
- tool arguments JSON size > 256KB rejected
- response body > 5MB truncated with warning + `_meta.truncated: true`
- size metrics 기록 (12-operations §3)

### FR-012 Concurrent Tool Call Limit

한 사용자가 동시에 진행 가능한 tool_call 개수를 제한해야 한다.

Acceptance Criteria:

- Free plan: 동시 3개
- Pro plan: 동시 10개
- Team plan: 동시 25개 per user
- 초과 시 429 + Retry-After
- 측정: per (user_id, external_connection_id) in-flight count

### FR-013 Email Verification

CoreMCP는 이메일 인증을 요구한다.

Acceptance Criteria:

- 가입 직후 verify email 전송
- 미인증 상태에서는 service 등록 제한 (Phase 1 결정)
- 24시간 안에 미인증 시 reminder
- 7일 미인증 시 계정 비활성화

### FR-014 Account Deletion (Right to be Forgotten)

사용자는 계정을 삭제할 수 있어야 한다.

Acceptance Criteria:

- DELETE /v1/me → soft-delete 즉시, 30일 grace
- grace 기간 복구 가능
- 30일 후 hard-delete + KMS ciphertext destroy
- audit_logs는 anonymize 후 1년 보존
- 사용자에게 단계별 email 통지

### FR-015 Data Export (Portability)

사용자는 본인 데이터를 export할 수 있어야 한다.

Acceptance Criteria:

- POST /v1/me/export → async job
- NDJSON 또는 ZIP-of-JSON
- S3 signed URL (TTL 7일)
- 24시간 이내 완료 목표
- 90일 1회 제한

### FR-016 Admin Impersonation (Support)

CoreMCP support가 user 동의 하에 일시적으로 view-only impersonate 가능해야 한다.

Acceptance Criteria:

- user가 in-app에서 "support access 7일 허용" toggle
- impersonation 모든 action audit_logs에 actor + impersonated_user 기록
- write action은 차단 (view-only)
- impersonation 종료 시 자동 alert

---

## 8. 비기능 요구사항

### 8.1 성능

| 항목 | 목표 |
|---|---|
| tools/list p95 | 500ms 이하, cache hit 기준 |
| tools/call gateway overhead p95 | downstream 제외 150ms 이하 |
| service validation | 10초 이하 |
| dashboard initial load | 2초 이하 |

### 8.2 안정성

- CoreMCP gateway는 downstream failure를 격리해야 한다.
- 한 service 장애가 전체 tools/list를 실패시키면 안 된다.
- tools/list에서 특정 service cache가 stale이면 stale-but-usable 정책을 적용한다.

### 8.3 보안

- HTTPS only
- OAuth token audience validation
- no token passthrough
- encrypted secret storage
- SSRF guard
- request/response size limit
- per-user rate limit
- admin endpoint RBAC
- TLS 1.3 (1.2 fallback)
- HSTS preload
- CSP frame-ancestors 'none' (clickjacking)
- per-user concurrent tool_call limit (FR-012)
- right-to-erasure SLA (FR-014)

### 8.4 확장성

MVP target:

- 동시 active user: 1,000
- 동시 active external_connection: 5,000
- 일일 tool_invocation: 100,000
- p99 db connection saturation < 70%

- service registry는 workspace/user scope를 모두 지원할 수 있어야 한다.
- tool catalog는 user-specific materialized cache로 확장 가능해야 한다.
- proxy executor는 async worker 또는 queue 기반 구조로 확장 가능해야 한다.

### 8.5 관측성

- request_id
- tool_invocation_id
- downstream latency
- upstream client type
- error class
- auth decision log
- schema refresh event

---

## 9. 성공 지표

### Activation

- 가입 후 첫 MCP service 등록 완료율
- 등록 후 첫 tools/list 성공률
- Claude Code 연결 완료율
- 첫 tools/call 성공률

### Engagement

- 사용자당 toolbox item 수
- 주간 tool invocation 수
- 연결된 external client 수
- playground test call 수

### Reliability

- tools/call success rate
- downstream timeout rate
- schema refresh failure rate
- auth failure rate

### Marketplace Future

- public MCP submission 수
- approved service 수
- add-to-toolbox conversion
- verified service usage share

---

## 10. MVP 릴리스 체크리스트

- [ ] user auth 동작
- [ ] default toolbox 자동 생성
- [ ] private MCP 등록
- [ ] URL/SSRF validation
- [ ] downstream credential vault
- [ ] MCP initialize validation
- [ ] tools/list cache
- [ ] tool alias 생성
- [ ] `/mcp` endpoint
- [ ] OAuth protected resource metadata
- [ ] tools/list user catalog 반환
- [ ] tools/call proxy
- [ ] Claude Code 연결 테스트
- [ ] audit log
- [ ] invocation log
- [ ] basic dashboard
- [ ] connected clients revoke
- [ ] error taxonomy
- [ ] security review
- [ ] OAuth Authorization Server (Logto) deploy
- [ ] DCR endpoint 작동
- [ ] PKCE S256 강제
- [ ] Resource Indicator 검증
- [ ] JWKS rotation 절차
- [ ] RLS 정책 모든 user-owned 테이블
- [ ] right-to-erasure 30d grace flow
- [ ] data export endpoint
- [ ] email verification flow
- [ ] privacy policy / ToS 공개

---

## 11. Open Questions

1. MVP auth provider는 Auth.js/NextAuth, Logto, Keycloak, Auth0 중 무엇으로 갈 것인가? — 결정: Logto self-host (ADR-011)
2. CoreMCP 자체 authorization server를 만들 것인가, 외부 OIDC provider를 resource server로만 사용할 것인가? — 결정: Logto self-host (ADR-011)
3. ChatGPT custom MCP app까지 MVP에 포함할 것인가, Claude Code만 우선할 것인가? — 결정: MVP는 Claude Code, ChatGPT는 Phase 3 (ADR-013 + 17-mcp-client-profiles.md)
4. downstream MCP가 OAuth만 지원하는 경우 MVP에서 제외할 것인가? — 결정: MVP 제외 (ADR-005), Phase 3
5. public marketplace를 언제부터 열 것인가? — 결정: Phase 4 (ADR-006)
6. 팀/워크스페이스 구조를 DB에 선반영할 것인가? — 결정: 선반영 Accepted (ADR-010)
7. tool result 원문 저장을 허용할 것인가, metadata만 저장할 것인가? — 결정: 기본 미저장, opt-in debug trace (ADR-009)
