# CoreMCP Personal Features

문서 버전: v1.0
대상: 본인 1명, Mac mini

본 문서는 `production_docs_donotuse/`에 정의된 기능 중 개인용 컨텍스트에서 **무엇을 어떻게 지원하는지** 명시한다. "포함"이면 단순화되어도 핵심 동작은 유지된다.

---

## 1. 인증 / 인가

출처: `production_docs_donotuse/06-security-auth.md`, `production_docs_donotuse/01-prd.md` §인증

| 기능 | 상태 | 개인용 적용 |
|---|---|---|
| 사용자 로그인 | 부분포함 | bootstrap 시 본인 user 1행 자동 생성, 추가 가입 flow 없음 |
| Admin token (`cmcp_admin_*`) | 포함 | 파일 보관, root 관리자 권한. /v1/* + /mcp fallback (ADR-030) |
| Client token (`cmcp_client_*`) | 포함 | per-connection 발급, DB hash, revocable. /mcp 주 사용 (ADR-030) |
| AUTH_MODE static_bearer | 포함 | default. 401 응답에 resource_metadata omit (ADR-032) |
| AUTH_MODE oauth | 부분포함 | 옵션. /oauth/* 활성, RFC 9728 full metadata (ADR-032, Phase P3+) |
| `/mcp` OAuth 2.1 호환 | 부분포함 | 기본은 bearer. OAuth flow는 옵션(Phase P3+). PKCE는 구현해도 single-user라 형식적 |
| Protected resource metadata | 포함 | `/.well-known/oauth-protected-resource` 응답. authorization_server는 자체 또는 omit |
| Authorization server metadata | 부분포함 | OAuth 사용 시만 |
| DCR (Dynamic Client Registration, RFC 7591) | 부분포함 | AUTH_MODE=oauth 활성 시(Phase P3+) CIMD fallback으로 동작 (ADR-036). static_bearer 모드(default)에서는 비활성 |
| CIMD (Client ID Metadata Documents) | 부분포함 | AUTH_MODE=oauth 활성 시 preferred (ADR-036). static_bearer 모드에서는 비활성 |
| `/.well-known/oauth-protected-resource` endpoint | 부분포함 | static_bearer 모드 default 404. EXPOSE_RESOURCE_METADATA_IN_STATIC_MODE=true 시에만 노출 |
| JWKS endpoint | 부분포함 | JWT 사용 시만 |
| Token revocation | 포함 | token 회전(파일 재작성)으로 즉시 revoke |
| Token introspection | 제외 | 정적 token이라 불필요 |
| Refresh token rotation | 제외 | 정적 token |
| One-time connection token | 포함 | OpenClaw 등 OAuth 미지원 client 연결용 |
| MFA / Password reset / Email verify | 제외 | 가입자 없음 |
| Right-to-erasure / Data export | 제외 | 본인 직접 관리 |
| Connected clients revoke | 포함 | external_connections 행 삭제로 해당 토큰 invalidate |
| MCP Protocol 2025-06-18 | 포함 | Codex CLI/Claude Code 호환성 유지 (ADR-029) |
| MCP Protocol 2025-11-25 | 포함 | latest. icons metadata, JSON Schema 2020-12 (ADR-029) |
| MCP tasks/* (2025-11-25 experimental) | 제외 | client 요청 시 -32601. CoreMCP는 downstream에도 forward 안 함. Phase P3+ 검토 (ADR-029) |

## 2. MCP Service Registry

출처: `production_docs_donotuse/01-prd.md` §registry, `production_docs_donotuse/07-mcp-proxy-spec.md`

| 기능 | 상태 | 개인용 적용 |
|---|---|---|
| Private MCP service 등록 | 포함 | 핵심 |
| endpoint URL + auth_type 입력 | 포함 | bearer_token / api_key_header / none |
| Credential 암호화 저장 | 포함 | macOS Keychain (keyring) 또는 fernet |
| Service status (draft/validating/active/error/disabled) | 포함 | 핵심 |
| Service visibility (private/public/review_pending) | 부분포함 | private만 사용, public 관련 UI/로직 제거 |
| URL safety / SSRF guard | 포함 | localhost http는 fake MCP 개발 위해 허용 |
| 등록 후 자동 validation (initialize + tools/list) | 포함 | 핵심 |
| Validation report (단계별 결과) | 포함 | 핵심 |
| Tool schema cache | 포함 | 핵심 |
| Schema hash + drift detection | 포함 | 핵심 |
| Manual refresh-tools | 포함 | 핵심 |
| TTL refresh | 포함 | private 1h |
| Credential rotation | 포함 | UI에서 새 secret 입력 → 재검증 |
| Credential revoke | 포함 | 핵심 |
| Service edit / delete (soft-delete) | 포함 | 핵심 |
| Tool metadata 보안 scanner | 포함 | regex pattern + Unicode 정규화 + homoglyph 경고 |
| Public marketplace 등록 | 제외 | 외부 노출 없음 |
| Public marketplace review queue | 제외 | 동일 |
| Verified badge | 제외 | 동일 |

## 3. Toolbox

출처: `production_docs_donotuse/01-prd.md` §toolbox, `production_docs_donotuse/05-database-schema.md` §toolboxes

| 기능 | 상태 | 개인용 적용 |
|---|---|---|
| Default toolbox 자동 생성 | 포함 | 부트스트랩 시 |
| Multi-toolbox | 부분포함 | 1개로 충분. 스키마는 multi 허용, UI는 default만 노출 가능 |
| Toolbox item 추가/삭제 | 포함 | 핵심 |
| Item enable/disable | 포함 | 핵심 |
| Tool 단위 enable/disable | 부분포함 | service 단위만 MVP, tool 단위는 Phase P3+ |
| Toolbox 기준 동적 catalog | 포함 | 핵심 |

## 4. MCP Gateway

출처: `production_docs_donotuse/07-mcp-proxy-spec.md`, `production_docs_donotuse/04-api-spec.md` §/mcp

| 기능 | 상태 | 개인용 적용 |
|---|---|---|
| `POST /mcp` Streamable HTTP | 포함 | 핵심 |
| `GET /mcp` SSE (keepalive + notifications) | 포함 | listChanged emit 채널 |
| `DELETE /mcp` session termination | 포함 | 핵심 |
| `Mcp-Session-Id` 처리 | 포함 | CoreMCP client session map + HTTP downstream service별 session id mapping |
| `MCP-Protocol-Version` 협상 | 포함 | 2025-11-25 / 2025-06-18 병행 지원 (ADR-029) |
| initialize | 포함 | 핵심 |
| tools/list | 포함 | 핵심 |
| tools/call | 포함 | 핵심 |
| tools/list pagination cursor | 부분포함 | 50개 미만이면 cursor 불필요, 그래도 spec 호환 |
| notifications/{tools,resources,prompts}/list_changed emission | 포함 | toolbox/tool permission/service catalog 변경 및 downstream list_changed fan-in 시, Last-Event-Id reconnect backfill 지원 |
| notifications/cancelled forward | 포함 | client→downstream |
| notifications/progress forward | 포함 | downstream→client |
| resources / prompts | 포함 | list/read/templates/list/get proxy + catalog cache |
| sampling / elicitation | 제외 | capability omit, -32601 reject |
| Dynamic capability merge | 포함 | default toolbox active service union 기반 initialize capabilities |
| Tool args JSON Schema validation | 포함 | downstream 호출 전 catalog input_schema_json 검증 |
| Tool annotations (destructive/readOnly/idempotent) | 포함 | schema 캐시에 포함, UI 표시 |
| Structured content output | 부분포함 | spec 따라 schema 저장, 렌더링은 Phase P3+ |

## 5. Downstream Proxy

출처: `production_docs_donotuse/07-mcp-proxy-spec.md`

| 기능 | 상태 | 개인용 적용 |
|---|---|---|
| Multi-MCP namespace | 포함 | downstream tool name은 항상 `<service_slug>.<tool>`로 노출, dotted name prefix 우회 차단 |
| Resource strict routing | 포함 | active service가 있을 때 `resources/read`는 catalog에 등록된 URI만 라우팅, duplicate URI는 shadow policy 적용 |
| Per-service quota | 포함 | service_id/method/tool 단위 in-memory fixed-window |
| Partial failure metadata | 포함 | circuit-open/unavailable service를 tools/list `_meta.coremcp.unavailable_services`에 표시 |
| Tool alias (exposed↔downstream 매핑) | 포함 | 별도 테이블 |
| Slug rename 시 alias 유지 | 포함 | 핵심 |
| Tool name normalization (NFKC, lowercase, etc.) | 포함 | 핵심 |
| Reserved namespace (core/admin/internal/mcp/_meta) | 포함 | 핵심 |
| Credential resolve from vault | 포함 | 핵심 |
| Downstream initialize/call | 포함 | 핵심 |
| Timeout / retry policy | 포함 | connect 3s, read 30s, total 35s |
| Downstream response normalization | 포함 | 핵심 |
| Idempotency key 처리 | 포함 | 24h 캐시 |
| Cancellation propagation | 포함 | 핵심 |
| Downstream session cache | 부분포함 | 단일 사용자라 user_id 무관, service+cred_hash로 캐시 |

## 6. Observability

출처: `production_docs_donotuse/12-operations-observability.md`

| 기능 | 상태 | 개인용 적용 |
|---|---|---|
| Tool invocation log (DB) | 포함 | 디버깅 핵심 |
| Audit log (DB) | 포함 | service 변경, credential 회전 등 |
| Raw request/response body 미저장 | 포함 | 디폴트 정책 |
| Opt-in debug trace | 포함 | 환경 변수로 24h 한정 활성 |
| Request ID propagation | 포함 | 핵심 |
| Structured logging (JSON to file) | 포함 | `~/.coremcp/logs/coremcp.log` |
| Tracing (OTel) | 부분포함 | 환경 변수로 활성, 기본 off |
| Metrics endpoint (Prometheus) | 부분포함 | `/metrics`, 본인이 grafana 띄울 때 활용 |
| Health endpoints | 포함 | `/health`, `/ready`, `/live` |
| Status page | 제외 | 외부 사용자 없음 |

## 7. Frontend (Web Admin)

출처: `production_docs_donotuse/08-frontend-ux.md`

| 기능 | 상태 | 개인용 적용 |
|---|---|---|
| Dashboard | 포함 | 핵심 |
| MCP services 목록/상세 | 포함 | 핵심 |
| New service 등록 form | 포함 | 핵심 |
| Validation report 뷰 | 포함 | 핵심 |
| Tool schema 뷰 | 포함 | 핵심 |
| Toolbox 관리 | 포함 | 핵심 |
| Connected clients 목록/revoke | 포함 | 핵심 |
| Connect Codex CLI exec 가이드 | 포함 | 핵심 |
| Connect Claude Code 가이드 | 포함 | 옵션 호환 |
| Connect ChatGPT/Cursor 가이드 | 포함 | OAuth 활성 시 DCR/CIMD flow 사용 |
| Tool invocation logs | 포함 | 핵심 |
| Audit logs viewer | 포함 | 핵심 |
| Settings (token rotate, locale) | 포함 | 핵심 |
| Playground / test tool call | 포함 | 핵심 (디버깅용) |
| Sign up / Login flow | 제외 | 자동 부트스트랩 |
| MFA enroll | 제외 | 동일 |
| Pricing / Billing pages | 제외 | 과금 없음 |
| Public marketplace browse | 제외 | 비공개 |
| Workspace switcher | 제외 | 단일 |
| Cookie banner | 제외 | 외부 노출 없음 |

## 8. Infra / Ops

출처: `production_docs_donotuse/03-architecture.md`, `production_docs_donotuse/12-operations-observability.md`

| 기능 | 상태 | 개인용 적용 |
|---|---|---|
| SQLite or local PostgreSQL | 포함 | SQLite 기본, Postgres 옵션 |
| Redis | 부분포함 | 옵션. 기본은 in-memory dict |
| Worker | 부분포함 | FastAPI BackgroundTasks 기본, Arq 옵션 |
| Secret backend = Keychain | 포함 | default, Mac mini 자동 로그인 환경 (ADR-031) |
| Secret backend = Fernet | 포함 | headless 무인 운영 옵션 (ADR-031) |
| AWS KMS / Vault | 제외 | SaaS 전환 시 |
| Cloudflare WAF / Egress proxy | 제외 | 단일 호스트 |
| Multi-region / DR | 제외 | 단일 호스트 |
| Backup | 포함 | sqlite3 .backup cron / Time Machine |
| launchd daemon | 포함 | 핵심 |
| Status page | 제외 | 동일 |
| Sentry / Datadog | 부분포함 | 옵션, 기본 off |
| Tailscale 외부 노출 | 부분포함 | 옵션, 권장 |
| HTTPS (Caddy local cert 또는 Tailscale Serve) | 부분포함 | 옵션 |

## 9. Security 추가

출처: `production_docs_donotuse/06-security-auth.md`, `production_docs_donotuse/11-risk-review.md`

| 기능 | 상태 | 개인용 적용 |
|---|---|---|
| Static bearer token 검증 | 포함 | 핵심 |
| SSRF guard (private IP block + DNS rebinding) | 포함 | allowlist 기반: ALLOW_TAILSCALE_DOWNSTREAM, ALLOWED_PRIVATE_CIDRS (ADR-033) |
| Tailscale CGNAT (100.64/10) 허용 | 부분포함 | 환경 변수로 활성 (ADR-033) |
| Tool poisoning scanner | 포함 | regex + Unicode |
| Logs redaction | 포함 | authorization/token/api_key 등 |
| Rate limit | 부분포함 | per-process global cap. per-user는 의미 없음 |
| Request/response body size limit | 포함 | request 1MB, response 5MB |
| TLS | 부분포함 | localhost는 http 허용, Tailscale은 자동 TLS |
| CORS | 포함 | localhost + Tailscale 도메인만 |

## 10. 미래 확장 hook

스키마/코드 레벨에서 다음 확장 가능성을 열어둠 (구현은 안 함):
- workspace_id 컬럼 (nullable) — Phase 5 SaaS 전환 시
- visibility 컬럼 (private 고정) — public marketplace 전환 시
- plan 컬럼 (free 고정) — 과금 전환 시
- user.region 컬럼 — multi-region 전환 시
- Soft-delete partial unique 모델은 SaaS 전환 시에도 그대로 유효 (ADR-035)
- AUTH_MODE oauth 전환 시 personal_access_tokens는 JWT issuance로 보완 가능 (ADR-030)
- tool_aliases.exposed_name global unique는 단일 사용자 가정. SaaS 전환 시 user/workspace scope 필요 (15-future-saas-migration §3.2)

상세는 `15-future-saas-migration.md`.
