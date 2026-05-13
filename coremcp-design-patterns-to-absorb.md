# CoreMCP에 흡수할 오픈소스 설계 패턴

> **Guardrail — 현재 저장소 적용 범위는 personal gateway 우선**
>
> 이 문서는 SaaS 구현 지시서가 아니라, CoreMCP personal gateway를 더 안전하고 운영 가능하게 만들기 위한 패턴 참고 문서다. 현재 저장소의 구현 대상은 **개인용 CoreMCP Gateway + 도구함 관리**이며, team/workspace/marketplace/publisher/billing 관련 항목은 명시 요청과 활성 dev-plan phase/ADR이 생기기 전까지 장기 backlog 또는 제외 범위로만 취급한다. 지금 흡수할 수 있는 범위는 개인 운영에 필요한 service registry metadata, token boundary, credential vault, SSRF guard, tool-level control, schema drift 가시성, local observability, 외부 AI client 연결 UX로 제한한다.

## 문서 목적

이 문서는 CoreMCP가 유사 오픈소스 프로젝트들로부터 흡수해야 할 설계 패턴을 구체화한다.

이 문서가 관찰하는 장기 청사진은 단순 MCP Gateway를 넘어 다음을 포함하는 **MCP Toolbox + Authenticated MCP Gateway SaaS**로 확장될 수 있다는 가정이다. 다만 이는 현재 저장소의 즉시 구현 지시가 아니며, personal gateway 목적에 맞는 패턴만 선별 흡수한다.

- 사용자가 여러 MCP 서버를 개인/팀 도구함에 담는다.
- 외부 AI 클라이언트에는 CoreMCP 하나만 연결한다.
- CoreMCP가 사용자 인증, tool catalog 병합, 권한 검증, downstream MCP 프록시 실행을 담당한다.
- 개발자는 MCP 서버를 등록하고 검증 후 공개할 수 있다.
- 팀/조직은 MCP 사용 권한, audit log, credential, 정책을 관리할 수 있다.

---

# 1. ContextForge에서 가져올 것

## 1.1 Gateway Registry

### 개념

Gateway Registry는 CoreMCP에 등록된 모든 외부 MCP 서비스, REST API, gRPC API, 내부 도구 서버를 중앙에서 관리하는 시스템이다.

CoreMCP에서는 Registry가 단순 URL 목록이 아니라, 다음 정보를 포함하는 **실행 가능한 서비스 카탈로그**여야 한다.

### CoreMCP 적용 방향

```text
MCP Service Registry
- 어떤 MCP 서버가 등록되어 있는가?
- 누가 등록했는가?
- public/private/team 전용인가?
- 어떤 endpoint를 사용하는가?
- 인증 방식은 무엇인가?
- 현재 활성 상태인가?
- tools/list 결과는 언제 마지막으로 캐시되었는가?
- security review를 통과했는가?
- 사용자가 도구함에 추가할 수 있는가?
```

### 필요한 데이터 모델

```sql
mcp_services
- id
- owner_user_id
- workspace_id
- namespace
- slug
- name
- description
- base_url
- mcp_endpoint_url
- transport_type
- auth_type
- visibility
- status
- review_status
- logo_url
- homepage_url
- documentation_url
- created_at
- updated_at
```

### 구현 포인트

- 모든 downstream MCP는 registry에 등록되어야 한다.
- registry에 없는 MCP는 `/mcp` proxy 대상으로 사용할 수 없다.
- public registry와 private registry를 분리한다.
- 팀/조직 전용 MCP는 workspace scope를 가진다.
- MCP 등록 시 endpoint reachability, initialize, tools/list 검증을 수행한다.
- schema hash를 저장해 tool 변경 여부를 추적한다.

---

## 1.2 Plugin System

### 개념

CoreMCP 내부 기능을 고정 코드로만 처리하면 확장성이 떨어진다.
Plugin System은 MCP 등록, tool 변환, 보안 검사, credential 처리, observability, billing 등을 독립 모듈로 확장할 수 있게 만든다.

### CoreMCP 적용 방향

CoreMCP의 plugin은 다음 확장 지점을 가진다.

```text
Plugin Extension Points
- service_registration_validator
- tool_schema_transformer
- tool_description_security_scanner
- credential_provider
- policy_evaluator
- proxy_request_interceptor
- proxy_response_interceptor
- audit_log_sink
- billing_meter
- notification_handler
```

### 예시

```text
GitHub MCP Plugin
- GitHub OAuth connection flow 제공
- GitHub token refresh 처리
- github.* tool namespace 제공
- GitHub API rate limit 감지
- GitHub organization policy 연동
```

```text
Slack MCP Plugin
- Slack OAuth connection flow 제공
- workspace별 bot token 관리
- slack.* tool namespace 제공
- channel scope 검증
```

### 구현 포인트

- MVP에서는 plugin interface만 정의하고, 실제 구현은 built-in module로 시작한다.
- Phase 2부터 외부 plugin package를 허용한다.
- plugin은 CoreMCP database와 secret vault에 직접 접근하지 못하게 한다.
- plugin 호출은 sandboxed context 또는 제한된 service interface를 통해 수행한다.

### Plugin Interface 예시

```python
class ToolSchemaTransformer:
    async def transform(
        self,
        service: MCPService,
        tool: DownstreamToolSchema,
        context: TransformContext,
    ) -> ExposedToolSchema:
        ...
```

```python
class PolicyEvaluator:
    async def evaluate_tool_call(
        self,
        user: User,
        service: MCPService,
        tool: ExposedTool,
        arguments: dict,
        context: RequestContext,
    ) -> PolicyDecision:
        ...
```

---

## 1.3 Admin Console

### 개념

Admin Console은 운영자가 MCP 서비스, 사용자, 팀, tool, credential, audit log를 관리하는 백오피스다.

CoreMCP는 SaaS이므로 Admin Console이 필수다.
단순 관리 UI가 아니라 **MCP Control Plane** 역할을 해야 한다.

### 주요 화면

```text
Admin Console
- Dashboard
- MCP Services
- Service Detail
- Tool Catalog
- Tool Schema Diff
- Users
- Workspaces
- User Connections
- External Client Connections
- Audit Logs
- Invocation Logs
- Security Review Queue
- Policy Management
- Secret/Credential Status
- System Health
```

### 핵심 기능

```text
MCP Services
- 서비스 등록
- 서비스 비활성화
- public/private 전환
- endpoint 수정
- auth type 변경
- tools/list refresh
- schema diff 확인
- review approve/reject
```

```text
Tool Catalog
- tool enable/disable
- exposed name 수정
- description override
- allowlist/denylist
- dangerous tool flag
- tool poisoning warning 확인
```

```text
Audit Logs
- 누가 어떤 tool을 호출했는가?
- 어느 external client에서 호출했는가?
- downstream service는 무엇인가?
- 성공/실패 여부는?
- latency는?
- policy deny 이유는?
```

### 구현 포인트

- `/admin/*` API는 `/mcp` 인증과 분리한다.
- admin role, workspace admin role, service owner role을 구분한다.
- 모든 admin action은 audit log에 기록한다.
- SSRF 위험이 있으므로 MCP endpoint 등록/수정은 강한 validation을 거친다.

---

## 1.4 Observability

### 개념

CoreMCP는 여러 외부 AI 클라이언트와 여러 downstream MCP 사이에 위치한다.
따라서 문제 발생 시 어디서 실패했는지 추적할 수 있어야 한다.

### 관측 대상

```text
Inbound
- external client type
- user id
- workspace id
- mcp session id
- request id
- method
- protocol version
```

```text
Gateway
- auth validation latency
- toolbox resolving latency
- tools/list generation latency
- policy evaluation latency
- cache hit/miss
```

```text
Downstream
- service id
- downstream endpoint
- downstream tool name
- downstream latency
- retry count
- timeout
- response size
- error code
```

### 필수 Metrics

```text
coremcp_mcp_requests_total
coremcp_tool_calls_total
coremcp_tool_call_errors_total
coremcp_downstream_latency_ms
coremcp_tools_list_latency_ms
coremcp_auth_failures_total
coremcp_policy_denials_total
coremcp_cache_hit_ratio
coremcp_active_mcp_sessions
coremcp_downstream_timeouts_total
```

### 필수 Logs

```text
- MCP request log
- tool invocation log
- downstream proxy log
- auth failure log
- policy deny log
- admin action log
- credential access log
```

### 구현 포인트

- 모든 요청에 `request_id`를 부여한다.
- downstream 호출에도 동일한 correlation id를 전달한다.
- OpenTelemetry trace를 기본으로 설계한다.
- user prompt나 민감한 tool arguments는 원문 저장하지 않는다.
- audit log와 debug log를 분리한다.

---

## 1.5 Redis-backed Cache

### 개념

CoreMCP는 매번 downstream MCP에 `tools/list`를 호출하면 느리고 불안정해진다.
따라서 tool schema와 사용자별 tool catalog는 cache해야 한다.

### 캐시 대상

```text
Redis Cache Targets
- service tools/list result
- service schema hash
- user toolbox tool catalog
- OAuth discovery metadata
- protected resource metadata
- MCP session state
- one-time connection token status
- rate limit counter
- policy decision cache
```

### 캐시 키 예시

```text
service_tools:{service_id}:{schema_hash}
user_tool_catalog:{user_id}:{toolbox_id}:{version}
mcp_session:{session_id}
oauth_metadata:{issuer}
rate_limit:{user_id}:{window}
connection_token:{token_hash}
```

### 구현 포인트

- DB가 source of truth이고 Redis는 cache layer다.
- tools/list 결과는 DB에도 저장한다.
- Redis cache miss 시 DB에서 복원 가능해야 한다.
- service schema refresh 시 user tool catalog cache를 무효화한다.
- 사용자 도구함 변경 시 해당 user catalog cache를 무효화한다.

---

## 1.6 REST/gRPC-to-MCP Expansion

### 개념

초기 CoreMCP는 MCP 서버만 등록 대상으로 삼는다.
하지만 장기적으로는 REST API와 gRPC API도 MCP tool로 노출할 수 있어야 한다.

### CoreMCP 적용 방향

```text
Phase 1
- Remote MCP only

Phase 2
- OpenAPI-to-MCP adapter

Phase 3
- gRPC reflection-to-MCP adapter

Phase 4
- Database query-to-MCP adapter
```

### OpenAPI-to-MCP 변환 예시

```text
GET /repos/{owner}/{repo}/issues
→ github.list_issues

POST /repos/{owner}/{repo}/issues
→ github.create_issue
```

### 구현 포인트

- REST/gRPC 확장은 MVP 범위에서 제외한다.
- adapter interface만 미리 고려한다.
- 모든 non-MCP API도 CoreMCP 내부에서는 동일한 `ExposedTool` 모델로 변환한다.

---

# 2. LiteLLM에서 가져올 것

## 2.1 Team/Key Based Access Control

### 개념

CoreMCP는 개인 사용자뿐 아니라 팀/조직 단위로 MCP 접근을 제어해야 한다.

LiteLLM의 key/team 기반 접근 모델은 CoreMCP의 SaaS 권한 모델에 적합하다.

### CoreMCP 적용 방향

```text
Access Subject
- user
- workspace
- team
- external client
- API key
- service account
```

```text
Access Target
- MCP service
- exposed tool
- downstream credential
- toolbox
- workspace registry
```

### 정책 예시

```text
User A can use github.create_issue in Workspace X.
User B can read notion.search_page but cannot call notion.create_page.
Team Engineering can use internal_jira.*.
External client Claude Code can access only default toolbox.
API key K can call only readonly tools.
```

### 필요한 데이터 모델

```sql
access_policies
- id
- workspace_id
- subject_type
- subject_id
- resource_type
- resource_id
- effect
- actions
- conditions_json
- created_at
- updated_at
```

### 구현 포인트

- MVP에서는 RBAC + allowlist 조합으로 시작한다.
- Phase 2에서 condition 기반 ABAC를 추가한다.
- 모든 `tools/list`와 `tools/call`에 policy check를 적용한다.
- policy deny 이유를 audit log에 기록한다.

---

## 2.2 MCP Tool Permission

### 개념

사용자가 어떤 MCP 서비스에 접근 가능하더라도 모든 tool을 사용할 수 있어서는 안 된다.
도구 단위 권한 제어가 필요하다.

### CoreMCP 적용 방향

```text
Tool Permission Levels
- hidden
- visible_only
- callable
- callable_with_approval
- admin_only
```

### 예시

```text
github.search_repositories      → callable
github.create_issue             → callable
github.delete_repository        → admin_only
slack.search_messages           → callable
slack.send_message              → callable_with_approval
database.execute_query          → callable_with_approval
database.drop_table             → hidden
```

### 구현 포인트

- `tools/list`에서는 권한 없는 tool을 아예 숨기는 것이 기본이다.
- 연결은 되어 있지만 승인이 필요한 tool은 description에 명확히 표시한다.
- destructive tool은 별도 flag를 가진다.
- tool call 시 다시 한 번 permission check를 수행한다.
- tools/list에 보였다고 해서 tools/call을 무조건 허용하면 안 된다.

---

## 2.3 Toolsets

### 개념

Toolset은 여러 tool을 하나의 목적 단위로 묶은 것이다.
사용자는 개별 tool보다 toolset을 선택하는 편이 쉽다.

### CoreMCP 적용 방향

```text
Toolset Examples
- GitHub Readonly
- GitHub Issue Management
- Slack Search Only
- Slack Messaging
- Notion Knowledge Search
- Calendar Scheduling
- Developer Productivity Pack
```

### 데이터 모델

```sql
toolsets
- id
- workspace_id
- name
- description
- visibility
- created_at

toolset_items
- id
- toolset_id
- service_id
- exposed_tool_name
- permission_level
- created_at
```

### UX 적용

```text
Marketplace
- Add service to toolbox
- Add recommended toolset to toolbox
- Add readonly mode
- Add full access mode

Team Admin
- Assign toolset to team
- Assign toolset to external client
- Disable dangerous tools
```

### 구현 포인트

- MVP에서는 service 단위로 도구함에 추가한다.
- Phase 2에서 toolset 단위 추가를 지원한다.
- team policy와 toolset을 연결한다.
- external client connection에도 toolset 제한을 걸 수 있어야 한다.

---

## 2.4 OAuth Flow Separation

### 개념

CoreMCP에는 최소 3가지 OAuth/Token 흐름이 존재한다.
이 흐름을 섞으면 보안 문제가 발생한다.

### CoreMCP에서 분리해야 할 흐름

```text
1. User Login
사용자가 CoreMCP 웹앱에 로그인하는 흐름.

2. External Client Authorization
Claude Code, Claude, ChatGPT, Cursor 등이 CoreMCP MCP endpoint에 접근하기 위한 흐름.

3. Downstream Service Authorization
CoreMCP가 GitHub, Slack, Notion, custom MCP 등을 호출하기 위한 흐름.
```

### 금지해야 할 구조

```text
External AI client가 받은 CoreMCP access token을
downstream MCP 서비스에 그대로 전달하면 안 된다.
```

### 올바른 구조

```text
External AI Client
→ CoreMCP access token
→ CoreMCP validates token
→ CoreMCP resolves user/service connection
→ CoreMCP loads downstream credential
→ CoreMCP calls downstream MCP
```

### 구현 포인트

- CoreMCP access token의 audience는 CoreMCP여야 한다.
- downstream token은 별도 vault에 저장한다.
- downstream token은 user_service_connection에 연결한다.
- delegated OAuth는 service별 connector가 처리한다.
- token exchange가 필요할 경우 명시적 grant flow로만 처리한다.

---

## 2.5 Zero-trust Gateway Idea

### 개념

CoreMCP는 외부 AI 클라이언트와 downstream MCP 사이의 신뢰 경계에 위치한다.
모든 요청은 기본적으로 신뢰하지 않고 검증해야 한다.

### CoreMCP Zero-trust 원칙

```text
- 모든 MCP 요청은 인증되어야 한다.
- session id는 인증 수단이 아니다.
- tools/list 결과는 사용자/클라이언트별로 제한된다.
- tools/call은 매번 policy check를 수행한다.
- downstream credential은 요청자에게 노출되지 않는다.
- downstream response는 size/type/safety validation을 거친다.
- admin API는 별도 권한 체계를 가진다.
```

### 구현 포인트

- `/mcp` endpoint는 bearer token 없이 접근할 수 없다.
- public marketplace 조회 API와 MCP 실행 API를 분리한다.
- one-time token은 연결 bootstrap에만 사용한다.
- one-time token은 hash로 저장하고, 1회 사용 후 폐기한다.
- downstream URL은 SSRF guard를 통과해야 한다.

---

# 3. MetaMCP에서 가져올 것

## 3.1 Simple Self-hosted Deployment

### 개념

CoreMCP는 SaaS가 기본이지만, 개발자와 기업 사용자를 위해 self-hosted 배포도 제공해야 한다.

### CoreMCP 적용 방향

```text
Deployment Modes
- Cloud SaaS
- Docker Compose Self-hosted
- Kubernetes Self-hosted
- Enterprise Private Cloud
```

### MVP Docker Compose 구성

```yaml
services:
  coremcp-api:
    image: coremcp/api
    environment:
      DATABASE_URL: postgresql://...
      REDIS_URL: redis://...
      OIDC_ISSUER: ...
      SECRET_ENCRYPTION_KEY: ...
    ports:
      - "8080:8080"

  coremcp-web:
    image: coremcp/web
    ports:
      - "3000:3000"

  postgres:
    image: postgres:16

  redis:
    image: redis:7
```

### 구현 포인트

- `.env.example`을 반드시 제공한다.
- `docker compose up`으로 local dev가 가능해야 한다.
- 초기 admin user bootstrap 기능을 제공한다.
- self-hosted에서는 외부 OAuth provider 없이 local auth도 가능해야 한다.
- production SaaS와 self-hosted configuration을 분리한다.

---

## 3.2 Aggregator Implementation

### 개념

Aggregator는 여러 downstream MCP tool을 하나의 CoreMCP tool catalog로 병합하는 기능이다.

### CoreMCP 적용 방향

```text
Downstream MCP A
- search
- create

Downstream MCP B
- search
- send

CoreMCP Exposed Tools
- notion.search
- notion.create
- slack.search
- slack.send
```

### Aggregation 단계

```text
1. service registry에서 대상 MCP 조회
2. downstream MCP initialize
3. downstream tools/list 호출
4. tool schema normalize
5. namespace prefix 적용
6. permission filter 적용
7. user toolbox filter 적용
8. exposed tool catalog 생성
9. cache 저장
10. external client에 tools/list 반환
```

### 구현 포인트

- tool name 충돌을 namespace로 해결한다.
- downstream tool schema가 잘못된 경우 해당 tool만 비활성화한다.
- 하나의 downstream MCP 장애가 전체 tools/list 실패로 이어지면 안 된다.
- user별 catalog 생성 시 연결되지 않은 service tool은 숨긴다.
- tools/list는 가능한 빠르게 반환되어야 하므로 캐시를 적극 사용한다.

---

## 3.3 Tool Sync/Caching

### 개념

Downstream MCP의 tool schema는 변경될 수 있다.
CoreMCP는 schema 변경을 감지하고 사용자 tool catalog를 갱신해야 한다.

### Sync Trigger

```text
- service 등록 직후
- service endpoint 수정 직후
- admin manual refresh
- scheduled refresh
- tools/call schema error 발생 시
- downstream metadata version 변경 감지 시
```

### Sync 결과 상태

```text
Tool Sync Status
- success
- partial_success
- failed
- schema_invalid
- auth_failed
- timeout
```

### 데이터 모델

```sql
service_tool_sync_jobs
- id
- service_id
- status
- started_at
- finished_at
- error_code
- error_message
- discovered_tool_count
- changed_tool_count
```

### 구현 포인트

- sync는 background job으로 처리한다.
- sync 중에도 기존 cache는 유지한다.
- 새 schema가 유효할 때만 active catalog로 승격한다.
- schema diff를 admin console에서 확인할 수 있어야 한다.
- tool 삭제/변경은 audit event로 남긴다.

---

## 3.4 Middleware Architecture

### 개념

CoreMCP의 proxy path에는 여러 공통 처리가 필요하다.
이를 middleware chain으로 구성하면 확장과 테스트가 쉬워진다.

### Proxy Middleware Chain

```text
Inbound MCP Request
→ Request ID Middleware
→ Auth Middleware
→ MCP Session Middleware
→ Toolbox Resolver Middleware
→ Tool Alias Resolver Middleware
→ Policy Middleware
→ Credential Resolver Middleware
→ Rate Limit Middleware
→ Downstream Proxy Middleware
→ Response Sanitizer Middleware
→ Audit Middleware
→ Outbound MCP Response
```

### 구현 포인트

- middleware는 순서가 중요하다.
- Auth 이전에는 민감한 처리를 하지 않는다.
- Policy 이전에는 downstream credential을 조회하지 않는다.
- Audit middleware는 성공/실패 모두 기록한다.
- Response sanitizer는 downstream 응답 크기와 content type을 검증한다.

---

# 4. MCP Registry에서 가져올 것

## 4.1 Server Metadata Schema

### 개념

Marketplace에 등록되는 MCP 서버는 일관된 metadata를 가져야 한다.

### CoreMCP Metadata 필드

```yaml
name: "GitHub MCP"
namespace: "github"
description: "Search repositories, manage issues, and interact with GitHub."
publisher: "coremcp"
homepage_url: "https://github.com"
documentation_url: "https://docs.github.com"
support_url: "https://support.example.com"
logo_url: "https://..."
categories:
  - developer-tools
  - productivity
tags:
  - github
  - issues
  - repositories
auth:
  type: oauth2
  scopes:
    - repo
    - read:user
transport:
  type: streamable-http
  endpoint_url: "https://example.com/mcp"
visibility: public
```

### 구현 포인트

- metadata는 DB 저장 전 schema validation을 통과해야 한다.
- namespace는 globally unique해야 한다.
- public marketplace에는 review 완료된 service만 노출한다.
- metadata 변경 이력은 저장한다.
- publisher verification 상태를 표시한다.

---

## 4.2 Publisher Flow

### 개념

개발자가 MCP 서버를 등록하고 공개하기 위한 흐름이다.

### Flow

```text
1. Developer login
2. Create publisher profile
3. Verify email/domain/GitHub organization
4. Register MCP service
5. Enter metadata
6. Run automated validation
7. Run tool schema scan
8. Submit for review
9. Admin approve/reject
10. Publish to marketplace
```

### Publisher 상태

```text
Publisher Status
- unverified
- email_verified
- domain_verified
- organization_verified
- trusted
- suspended
```

### 구현 포인트

- 개인 개발자와 조직 publisher를 모두 지원한다.
- public 등록은 publisher verification이 필요하다.
- private MCP는 review 없이 사용할 수 있다.
- verified publisher badge를 제공한다.
- abuse report가 많은 publisher는 자동 제한한다.

---

## 4.3 Namespace Model

### 개념

Namespace는 exposed tool name의 prefix이자 marketplace 식별자다.

### 예시

```text
github.create_issue
notion.search_page
slack.send_message
calendar.create_event
```

여기서 `github`, `notion`, `slack`, `calendar`가 namespace다.

### Namespace 규칙

```text
- lowercase only
- numbers allowed
- hyphen not recommended for tool prefix
- underscore allowed
- must be globally unique for public services
- workspace-private namespace can overlap if scoped
```

### 구현 포인트

- public namespace는 전역 unique해야 한다.
- private namespace는 workspace 안에서만 unique해도 된다.
- namespace 변경은 breaking change로 취급한다.
- namespace 변경 시 기존 toolbox item과 tool alias migration이 필요하다.

---

## 4.4 Registry API

### 개념

CoreMCP marketplace와 external integration이 MCP service 목록을 조회할 수 있도록 Registry API를 제공한다.

### API 예시

```http
GET /api/registry/services
GET /api/registry/services/{namespace}
GET /api/registry/services/{namespace}/tools
GET /api/registry/categories
GET /api/registry/publishers/{publisher_id}
```

### 검색 필터

```text
- category
- tag
- publisher
- verified
- auth_type
- transport_type
- popularity
- recently_updated
```

### 구현 포인트

- public registry API는 read-only로 시작한다.
- private workspace registry는 인증 필요.
- marketplace 검색 성능을 위해 search index를 둔다.
- public API rate limit을 적용한다.

---

# 5. MCP Router에서 가져올 것

## 5.1 Project/Workspace UX

### 개념

사용자는 개인 도구함뿐 아니라 프로젝트/팀 단위로 MCP 도구를 다르게 구성하고 싶어 한다.

### CoreMCP 적용 방향

```text
Personal Workspace
- 개인 도구함
- 개인 external client connection
- 개인 credential

Team Workspace
- 팀 도구함
- 팀 service registry
- 팀 policy
- 팀 audit log
- 팀 shared credential
```

### UX 구조

```text
Workspace Sidebar
- Personal
- Team A
- Team B

Workspace Pages
- Toolbox
- MCP Services
- Members
- Policies
- External Connections
- Audit Logs
- Settings
```

### 구현 포인트

- MVP는 personal workspace만 지원한다.
- DB는 처음부터 workspace_id를 포함한다.
- Team workspace는 Phase 2에서 활성화한다.
- external client connection은 특정 workspace/toolbox에 연결된다.

---

## 5.2 Tool Enable/Disable UX

### 개념

사용자는 도구함에 service를 추가하더라도 모든 tool을 항상 쓰고 싶지는 않다.
개별 tool on/off UX가 필요하다.

### UX 예시

```text
My Toolbox > GitHub MCP

[ON] github.search_repositories
[ON] github.list_issues
[ON] github.create_issue
[OFF] github.delete_repository
[OFF] github.invite_user
```

### 구현 포인트

- service 단위 enable/disable
- tool 단위 enable/disable
- dangerous tool 기본 off
- readonly preset 제공
- full access preset 제공
- 변경 시 user tool catalog cache invalidation

### 데이터 모델

```sql
toolbox_tool_overrides
- id
- toolbox_id
- service_id
- exposed_tool_name
- enabled
- permission_level
- created_at
- updated_at
```

---

## 5.3 Local Credentials UX

### 개념

사용자는 downstream MCP 또는 API credential을 CoreMCP에 연결해야 한다.
이 credential 입력 UX는 매우 신중해야 한다.

### Credential UX 유형

```text
1. OAuth Connect
- Connect GitHub
- Connect Slack
- Connect Notion

2. API Key Input
- 사용자가 API key를 입력
- CoreMCP가 vault에 암호화 저장

3. Service Account
- 팀 admin이 service account credential 등록

4. No Auth
- 인증 없는 public MCP
```

### UX 원칙

```text
- credential 원문은 다시 보여주지 않는다.
- 저장 후 masked form만 보여준다.
- 언제 마지막으로 사용되었는지 보여준다.
- 언제 만료되는지 보여준다.
- revoke/rotate 버튼을 제공한다.
- tool call 실패가 auth 때문이면 재연결 안내를 제공한다.
```

### 구현 포인트

- secret은 DB 평문 저장 금지.
- secret_ref만 DB에 저장.
- encryption key rotation 전략 필요.
- credential access는 audit log에 남긴다.
- self-hosted에서는 local encrypted DB 또는 Vault 연동을 선택할 수 있게 한다.

---

## 5.4 One-click Integration UX

### 개념

CoreMCP의 핵심 UX는 외부 AI 클라이언트에 쉽게 연결하는 것이다.

### 지원 대상

```text
- Claude Code
- Claude Desktop / Claude web custom connector
- ChatGPT developer mode / custom MCP
- Cursor
- Windsurf
- OpenClaw
- Local MCP-compatible client
```

### UX Flow

```text
1. 사용자가 My Toolbox 화면으로 이동
2. "Connect external app" 클릭
3. 클라이언트 선택
4. 연결 방식 표시
   - OAuth 지원 클라이언트: MCP URL 복사 + OAuth 안내
   - OAuth 미지원 클라이언트: one-time connection token 생성
5. 연결 완료 후 external_connections에 기록
6. 사용자는 언제든 연결 해제 가능
```

### One-time Token Flow

```text
1. User clicks "Generate connection token"
2. CoreMCP creates one-time token
3. Token expires in 10 minutes
4. User copies connection prompt or URL
5. External client exchanges token
6. CoreMCP creates external_connection
7. Token is marked used
8. Token cannot be reused
```

### 구현 포인트

- one-time token은 원문 저장 금지.
- token hash만 DB에 저장.
- token은 1회 사용 후 즉시 폐기한다.
- token 생성/사용/실패 모두 audit log에 남긴다.
- external connection은 사용자가 revoke할 수 있어야 한다.

---

# 6. CoreMCP 통합 설계

## 6.1 최종 CoreMCP 구조

```text
External AI Clients
- Claude Code
- Claude
- ChatGPT
- Cursor
- OpenClaw
        |
        | MCP + OAuth / One-time Token
        v
CoreMCP Gateway
        |
        +-- Auth Layer
        +-- MCP Session Manager
        +-- Toolbox Resolver
        +-- Tool Catalog Aggregator
        +-- Policy Engine
        +-- Credential Vault
        +-- Proxy Executor
        +-- Audit Logger
        +-- Observability Layer
        |
        v
Registered MCP Services
- Public MCP
- Private MCP
- Team MCP
- REST/gRPC adapters
```

---

## 6.2 CoreMCP 내부 모듈

```text
coremcp-api
- /mcp
- /api
- /admin
- /.well-known

coremcp-auth
- user login
- external client OAuth
- token validation
- one-time token exchange

coremcp-registry
- service registration
- metadata validation
- publisher flow
- namespace management

coremcp-toolbox
- user toolbox
- team toolbox
- toolbox item management
- tool enable/disable

coremcp-catalog
- tools/list cache
- tool alias map
- schema hash
- catalog generation

coremcp-proxy
- tools/call routing
- downstream credential loading
- MCP client execution
- response normalization

coremcp-policy
- RBAC
- ABAC
- tool permission
- team/key access control

coremcp-security
- SSRF guard
- tool poisoning scan
- response sanitizer
- credential protection

coremcp-observability
- metrics
- logs
- traces
- audit events
```

---

# 7. 개발 우선순위

## Phase 0: Foundation

```text
- FastAPI backend scaffold
- Next.js frontend scaffold
- PostgreSQL schema
- Redis connection
- basic user auth
- workspace model
```

## Phase 1: MCP Registry

```text
- MCP service registration
- endpoint validation
- initialize test
- tools/list fetch
- tool schema cache
- service detail page
```

## Phase 2: Toolbox

```text
- default user toolbox
- add service to toolbox
- remove service from toolbox
- tool enable/disable
- user tool catalog generation
```

## Phase 3: MCP Gateway

```text
- /mcp Streamable HTTP endpoint
- OAuth protected resource metadata
- tools/list from user toolbox
- tools/call proxy
- request id
- audit log
```

## Phase 4: External Connections

```text
- Claude Code connection guide
- ChatGPT connection guide
- one-time token flow
- external_connections table
- revoke connection
```

## Phase 5: Security & Governance

```text
- SSRF guard
- tool poisoning scanner
- policy engine
- admin review queue
- audit log viewer
```

## Phase 6: Marketplace

```text
- public service listing
- category/tag search
- publisher profile
- publish request
- review approve/reject
- verified badge
```

---

# 8. 핵심 설계 원칙

```text
1. CoreMCP는 Gateway가 아니라 Toolbox-first SaaS다.
2. 사용자는 개별 MCP를 외부 AI 클라이언트에 등록하지 않는다.
3. 외부 AI 클라이언트는 CoreMCP 하나만 등록한다.
4. CoreMCP는 사용자 도구함에 담긴 tool만 노출한다.
5. CoreMCP access token과 downstream credential은 절대 섞지 않는다.
6. 모든 tools/list와 tools/call은 사용자, workspace, client, policy 기준으로 필터링된다.
7. Downstream MCP 장애가 전체 CoreMCP 장애가 되면 안 된다.
8. Tool schema는 cache하지만, 변경 감지와 invalidation이 가능해야 한다.
9. Public marketplace 등록은 security review를 통과해야 한다.
10. 모든 중요한 실행과 권한 변경은 audit log로 남긴다.
```

---

# 9. 최종 흡수 전략 요약

| Source Project | CoreMCP에 흡수할 핵심 |
|---|---|
| ContextForge | Registry, plugin, admin console, observability, cache, API-to-MCP expansion |
| LiteLLM | Team/key access control, tool permission, toolsets, OAuth separation, zero-trust gateway |
| MetaMCP | Self-hosted deployment, aggregation, sync/cache, middleware architecture |
| MCP Registry | Metadata schema, publisher flow, namespace, registry API |
| MCP Router | Workspace UX, tool on/off UX, local credential UX, one-click external integration |

CoreMCP는 이 패턴들을 조합해 다음 제품으로 완성한다.

> CoreMCP는 사용자가 여러 MCP 서버를 개인/팀 도구함에 담고, Claude Code, Claude, ChatGPT, Cursor 같은 외부 AI 클라이언트에는 CoreMCP 하나만 연결해 도구함 전체를 안전하게 사용할 수 있게 해주는 MCP Toolbox + Authenticated Gateway SaaS다.
