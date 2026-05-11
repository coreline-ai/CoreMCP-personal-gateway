# CoreMCP Executive Review

문서 버전: v0.1  
작성일: 2026-05-11  
목표 제품: PlayMCP형 MCP 도구함 + 인증 게이트웨이 SaaS

---

## 1. 최종 판단

CoreMCP는 “인증 MCP 게이트웨이”라고만 포지셔닝하면 기술 제품으로 보이지만, 실제 사용자가 구매하거나 기억할 가치는 “MCP 도구함”에 있다.

따라서 제품 정의는 다음으로 고정한다.

> CoreMCP는 사용자가 여러 MCP 서버를 등록하거나 선택해 개인/팀 도구함에 담고, Claude Code, Claude, ChatGPT, OpenClaw 같은 외부 AI 클라이언트에는 CoreMCP 하나만 연결해 도구함 전체를 인증 기반으로 사용할 수 있게 해주는 MCP Gateway SaaS다.

한 줄 카피:

> Connect once. Use every MCP tool anywhere.

한국어 카피:

> Claude Code, Claude, ChatGPT에서 내 MCP 도구함을 하나의 MCP 서버처럼 사용하세요.

---

## 2. PlayMCP 벤치마크 기반 핵심 인사이트

PlayMCP의 본질은 단순 MCP 목록 사이트가 아니다.

공개 정보 기준으로 PlayMCP는 다음 요소를 결합한다.

1. MCP 서버 마켓/디렉터리
2. 개발자용 Remote MCP 등록 및 테스트 공간
3. 사용자별 도구함
4. 외부 AI 클라이언트 연결
5. 로그인/인증 기반 MCP Gateway
6. 원타임 토큰 기반 로컬/오픈소스 에이전트 연결
7. 안전한 연결 해제 및 연결된 서비스 관리

CoreMCP가 따라야 할 핵심 UX는 다음이다.

```text
사용자는 MCP 서버를 하나씩 Claude Code에 등록하지 않는다.
사용자는 CoreMCP에 로그인한다.
사용자는 필요한 MCP를 도구함에 담는다.
Claude Code/Claude/ChatGPT는 CoreMCP 하나만 연결한다.
CoreMCP는 사용자의 도구함에 담긴 MCP tool만 노출한다.
```

---

## 3. 기존 CoreMCP 설계에서 유지할 것

기존 설계의 핵심은 유지한다.

- Claude Code에는 CoreMCP 하나만 등록
- CoreMCP는 단일 protected remote MCP server로 동작
- CoreMCP 내부에서 하위 MCP registry 관리
- 하위 MCP tool schema 수집/캐시
- 사용자별 연결 상태와 credential 관리
- tool 호출은 CoreMCP가 proxy
- audit log 기록

기존 설계 문서의 “단일 진입점 MCP Gateway”, “Auth Broker”, “Tool Schema Aggregator”, “Downstream MCP Proxy” 정의는 제품의 기술 기반으로 유지한다.

---

## 4. 기존 설계에서 바꿔야 할 것

### 4.1 제품 중심 개념 변경

기존:

```text
CoreMCP = MCP Gateway
```

변경:

```text
CoreMCP = MCP Toolbox + Authenticated MCP Gateway
```

Gateway는 내부 구현이고, 사용자가 보는 제품 가치는 도구함이다.

### 4.2 관리자 등록 중심에서 사용자/개발자 self-service로 확장

기존 설계는 `/admin/services` 중심이다. SaaS가 되려면 아래를 추가해야 한다.

- 개발자 MCP 등록 콘솔
- 공개/비공개 MCP visibility
- 등록 검증 리포트
- 사용자 도구함 추가
- 외부 AI 연결 안내
- 연결된 클라이언트 관리
- token/credential vault

### 4.3 Token boundary 엄격화

절대 금지:

```text
External AI client가 CoreMCP에 준 access token을 downstream MCP에 그대로 전달
```

필수 구조:

```text
External AI Client -> CoreMCP token
CoreMCP -> Downstream MCP credential
```

CoreMCP access token은 CoreMCP audience 전용이다. Downstream MCP는 별도 credential을 사용한다.

---

## 5. v0.1 MVP 목표

MVP는 PlayMCP 전체 복제가 아니라, “작동하는 CoreMCP 도구함 + 외부 AI 연결”을 목표로 한다.

### MVP 성공 문장

> 사용자가 CoreMCP에 로그인해 Remote MCP 서버를 등록하고 도구함에 추가한 뒤, Claude Code에서 CoreMCP 하나만 등록하면 해당 MCP tool을 사용할 수 있다.

### MVP 필수 범위

1. 사용자 로그인
2. Remote MCP 서버 등록
3. MCP initialize / tools/list 검증
4. tool schema cache
5. 기본 도구함 생성
6. 도구함에 MCP 추가/삭제
7. CoreMCP `/mcp` endpoint 제공
8. OAuth protected resource metadata 제공
9. Claude Code remote HTTP 연결
10. tools/list 사용자별 동적 노출
11. tools/call downstream proxy
12. bearer/api-key 기반 downstream credential vault
13. audit log
14. 연결 해제

---

## 6. Phase 계획

### Phase 0: Protocol Spike

목표: CoreMCP가 MCP server로 최소 동작하는지 검증.

- `/mcp` Streamable HTTP endpoint
- initialize
- tools/list
- tools/call
- fake downstream MCP proxy

### Phase 1: Private Toolbox MVP

목표: 로그인한 사용자가 자신만의 MCP를 등록하고 Claude Code에서 사용.

- User auth
- MCP registry private mode
- default toolbox
- tool cache
- Claude Code 연결
- proxy execution

### Phase 2: Developer Registry + Playground

목표: 개발자가 MCP 서버를 등록하고 웹에서 테스트.

- developer console
- MCP validation report
- playground chat/test
- schema refresh
- tool call trace

### Phase 3: External Client Expansion

목표: Claude, ChatGPT, OpenClaw 등으로 확장.

- client connection guide
- dynamic OAuth client registration 대응
- one-time connection token
- connected clients management

### Phase 4: Public Marketplace

목표: 공개 MCP 생태계.

- public listing
- review queue
- category/search
- verified badge
- abuse report
- usage metrics

### Phase 5: Team/Enterprise

목표: 팀 단위 정책과 과금.

- workspace
- RBAC
- team toolbox
- admin policy
- usage quota
- compliance export

---

## 7. 가장 큰 리스크

| 리스크 | 설명 | 대응 |
|---|---|---|
| Token passthrough | CoreMCP token과 downstream token 혼동 | credential boundary 문서화, 코드 레벨 타입 분리 |
| Tool poisoning | MCP tool description이 LLM 선택을 조작 | tool metadata scanner, review queue |
| SSRF | 사용자가 내부망 URL을 MCP endpoint로 등록 | URL allow/deny policy, egress proxy |
| Schema drift | downstream tool schema 변경 | schema hash, TTL refresh, lazy refresh |
| Client 호환성 | Claude Code/Claude/ChatGPT마다 MCP 연결 방식 차이 | client profile abstraction |
| Session hijack | MCP session id가 인증처럼 쓰임 | 모든 request에서 bearer token 검증 |
| Marketplace abuse | 악성 MCP 등록 | private default, public review |

---

## 8. 제품 이름/내부 용어 권장

| 개념 | 권장 용어 |
|---|---|
| 사용자 MCP 모음 | Toolbox |
| 공개 MCP 목록 | Marketplace |
| 개발자 등록 공간 | Developer Console |
| 외부 AI 연결 | Client Connection |
| 하위 MCP | Downstream MCP Service |
| 외부에 노출되는 tool 이름 | Exposed Tool Name |
| 원래 하위 tool 이름 | Downstream Tool Name |
| tool 이름 매핑 | Tool Alias |
| 사용자별 연결 credential | User Service Connection |
| 도구 실행 로그 | Tool Invocation |

---

## 9. 기술 원칙

1. CoreMCP는 protected remote MCP server다.
2. CoreMCP는 사용자의 toolbox를 MCP tool catalog로 변환한다.
3. CoreMCP token은 downstream으로 전달하지 않는다.
4. 모든 MCP request는 access token 검증을 거친다.
5. session id는 인증 수단이 아니다.
6. downstream credential은 secret vault에 저장한다.
7. public marketplace 등록은 기본적으로 review_pending 상태다.
8. tool schema는 캐시하되 schema_hash로 변경을 추적한다.
9. tool 호출은 request_id로 end-to-end 추적한다.
10. MVP에서는 delegated OAuth보다 bearer/api-key vault를 먼저 구현한다.

---

## 10. 참고한 주요 공개 근거

- PlayMCP: https://playmcp.kakao.com
- Kakao PlayMCP beta/open platform 보도자료: https://www.kakaocorp.com/page/detail/11674
- Kakao PlayMCP OpenClaw 연동 보도자료: https://www.kakaocorp.com/page/detail/12012
- Kakao AI Hub PlayMCP 소개: https://www.kakaocorp.com/page/service/tech/ai
- MCP Authorization spec: https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
- MCP Transports spec: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- MCP Security best practices: https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices
- Claude Code MCP docs: https://code.claude.com/docs/en/mcp

---

## 11. Competitive Landscape

| Service | 포지션 | 강점 | CoreMCP 차별화 |
|---|---|---|---|
| PlayMCP (Kakao) | KR 카카오 생태계 MCP 마켓 | Kakao 통합, OpenClaw native | self-host 가능, multi-AI client |
| Smithery.ai | MCP 디렉터리 + remote MCP host | 등록 MCP 다수 | private toolbox + authenticated gateway |
| mcp.so | MCP 디렉터리 | 정보 풍부 | gateway/proxy 없음 |
| Composio | tool integration platform | 기존 SaaS 통합 | MCP-native, no-code wrapper 없음 |
| Pipedream | workflow + MCP support | workflow 강함 | MCP gateway 전문화 |
| glama.ai | MCP gateway + chat | 자체 채팅 UI | client-agnostic, chat UI 없음 |
| Anthropic Connectors | Claude 전용 | first-party Claude | Claude 외 client 동시 지원 |

CoreMCP 차별화 한 줄:

> **client-agnostic + privacy-first + open ecosystem authenticated gateway**

PlayMCP가 직접 경쟁자라기보다 reference architecture. CoreMCP는 1) self-host 옵션, 2) multi-client (Claude+ChatGPT+Cursor), 3) developer console + audit, 4) compliance(GDPR/SOC2) 로드맵에서 차별.

---

## 12. Activation / Retention KPIs

### 12.1 North Star Metric

**Weekly Successful Tool Calls per Active User**

### 12.2 Activation Funnel

| 단계 | 측정 | MVP 목표 |
|---|---|---|
| Signup | users.created_at | — |
| Email verify | users.email_verified_at | 80% within 24h |
| First MCP service | mcp_services.created_at | 60% within 24h |
| First validation success | service_validation_runs success | 90% of those who tried |
| First toolbox add | toolbox_items.created_at | 80% of validation success |
| First Claude Code connect | external_connections claude_code | 50% within 7d |
| First successful tool_call | tool_invocations status=success | 60% within 7d |
| 5+ tool_calls (engaged) | count > 5 | 30% within 30d |

### 12.3 Retention

| Cohort | D1 | D7 | D30 |
|---|---|---|---|
| All signups | 50% | 25% | 15% |
| Activated (first tool call) | 80% | 50% | 35% |
| Connected Claude Code | 90% | 70% | 50% |

### 12.4 Engagement

- tool_invocations per active user per week
- mcp_services per active user
- external_connections per active user (multi-client adoption)

### 12.5 Reliability

- tools/call success rate > 95% (excluding downstream errors)
- tools/list p95 < 500ms
- validation success rate > 95% for healthy downstreams

### 12.6 Marketplace (Phase 4+)

- public submission count
- add-to-toolbox conversion (impression → add)
- verified service usage share
