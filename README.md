<div align="center">

# CoreMCP

**개인용 MCP Toolbox + Authenticated MCP Gateway**

[![MCP Spec](https://img.shields.io/badge/MCP-2025--11--25-FF6B6B?logo=anthropic&logoColor=white)](https://modelcontextprotocol.io/specification/2025-11-25)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-15+-000000?logo=nextdotjs&logoColor=white)](https://nextjs.org/)
[![SQLite](https://img.shields.io/badge/SQLite-3.35+-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![macOS](https://img.shields.io/badge/macOS-Mac%20mini-000000?logo=apple&logoColor=white)](https://www.apple.com/mac-mini/)
[![License](https://img.shields.io/badge/License-Private-red.svg)](#-license)
[![Status](https://img.shields.io/badge/Status-P1%20Core-green.svg)](#-phase-plan)
[![Docs](https://img.shields.io/badge/Docs-17%20files-blue.svg)](./coremcp-docs/)

내 Mac mini에서 모든 MCP를 한 곳에 모아 어디서든 쓴다.

[Overview](#overview) · [Quick Start](#-quick-start) · [Architecture](#%EF%B8%8F-architecture) · [Documentation](#-documentation) · [Phase Plan](#-phase-plan)

</div>

---

## Overview

CoreMCP는 본인 1명이 Mac mini에서 운영하는 protected MCP gateway다. 여러 MCP 서버를 등록해 자신의 도구함(toolbox)에 담고, Codex CLI `exec` 등 외부 AI 클라이언트에는 CoreMCP 하나만 연결한다.

```text
┌─────────────────────────────────────────────────────────────┐
│ External AI Clients                                         │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐         │
│ │ Codex    │ │ Claude   │ │ OpenClaw │ │ ChatGPT  │  ...    │
│ │ CLI exec │ │ Code     │ │          │ │ (옵션)   │         │
│ │ (Mac mini)│ │(MacBook)│ │          │ │          │         │
│ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘         │
└──────┼────────────┼────────────┼────────────┼───────────────┘
       │            │            │            │
       │  Bearer    │  Bearer    │   OTT      │   OAuth
       │  (admin    │  (client   │   exchange │   (CIMD)
       │   token)   │   token)   │            │
       └────────────┴────────────┴────────────┘
                       │
                       ▼ http://localhost:8787/mcp
       ┌─────────────────────────────────────────┐
       │  CoreMCP  (Mac mini, single process)    │
       │  ┌────────────────────────────────────┐ │
       │  │ /mcp  Streamable HTTP gateway      │ │
       │  │ /v1   REST admin API               │ │
       │  │ /web  Next.js admin console        │ │
       │  └────────────────────────────────────┘ │
       │           │           │                 │
       │           ▼           ▼                 │
       │     ┌──────────┐ ┌──────────┐           │
       │     │ SQLite   │ │ Keychain │           │
       │     │ ~/.coremcp/data/      │           │
       │     └──────────┘ └──────────┘           │
       └─────────────────┬───────────────────────┘
                         │ proxy + downstream credential
                         ▼
       ┌─────────────────────────────────────────┐
       │  Downstream MCP Services                │
       │  GitHub / Notion / Linear / 자체 MCP     │
       └─────────────────────────────────────────┘
```

**한 줄 가치**: AI 클라이언트마다 MCP를 따로 등록하지 않고, 한 곳에 모아 어디서나 동일한 도구함을 쓴다.

### Implementation Status — 2026-05-16

- Root monorepo scaffold, `apps/api`, `apps/fake-mcp`, `apps/web`, `packages/*`, `infra/*`가 생성되었습니다.
- P1 backend core는 Alembic 단일 schema source, client token DB hash, service registry, toolbox catalog, 실제 Fernet/Keychain credential vault, SSRF guard, validation, DB 기반 `/mcp tools/list/call`까지 구현되었습니다.
- OAuth optional flow는 `AUTH_MODE=oauth`에서 DCR/CIMD, authorize/code, token, JWKS, refresh rotation, revoke/introspect까지 로컬 테스트로 고정했습니다. RSA signing key는 vault reference로 보관하고, DCR client, authorization code hash, refresh token hash/family, revoked access JTI, CIMD cache는 SQLite에 영속화됩니다.
- OAuth 미지원 client용 one-time connection token issue/exchange와 기본 off Prometheus `/metrics` endpoint를 구현하고 API 회귀 테스트로 고정했습니다.
- 개인 도구함 tool-level override(`hidden`, `visible_only`, `callable`)와 preset(`readonly`, `dangerous_off`, `full_access`), client/OAuth scope enforcement, schema drift detail, `X-Request-ID` 전파, policy deny/audit/invocation 관측성을 구현하고 API 회귀 테스트로 고정했습니다.
- HTTP/STDIO service transport foundation을 추가했습니다. `transport_type=stdio` service는 command/args/env/cwd metadata로 등록·검증·호출 가능하며, `COREMCP_STDIO_ALLOWED_COMMANDS` command allowlist와 CoreMCP admin/client/Authorization 계열 env 차단을 적용합니다. STDIO runtime/crash snapshot은 `mcp_services` 컬럼에 영속화합니다.
- MCP `resources/list`, `resources/read`, `resources/templates/list`, `prompts/list`, `prompts/get` live proxy를 추가해 tools-only gateway 한계를 줄였습니다. `resources/read` 대형 text/blob는 client context 보호를 위해 CoreMCP metadata와 함께 truncate하며, active service가 있을 때는 catalog에 등록된 URI만 라우팅해 cross-service first-hit 충돌을 막습니다.
- Multi-MCP 안정화를 위해 downstream tool 이름은 항상 `<service_slug>.<tool>` namespace로 노출하고, HTTP downstream이 발급한 `Mcp-Session-Id`는 service별로 TTL 관리하며 client session id를 downstream에 그대로 전달하지 않습니다. Service update/delete, TTL 만료, circuit-open 전환 시 downstream session cache를 무효화합니다. Downstream `notifications/{tools,resources,prompts}/list_changed`는 CoreMCP SSE로 fan-in/fan-out됩니다.
- 동일 resource URI를 여러 service가 노출하면 가장 최근 validation service의 resource만 active로 유지하고 이전 active row는 `deprecated` shadow 처리 및 `resource.shadow` audit로 기록합니다.
- Multi-MCP P1 고도화로 service capability union 기반 initialize response, tool arguments JSON Schema 사전 검증, per-service fixed-window quota, `tools/list` unavailable service metadata, health probe schema drift refresh, downstream `Idempotency-Key` forwarding을 추가했습니다.
- 운영 안정성 module로 in-memory circuit breaker, idle session reaper, stdio process client cache를 추가했고, `coremcp` CLI foundation(`doctor`, `service add/validate`, `tool call`, `token issue/revoke`, `export/import dry-run`)과 Makefile CLI thin wrappers를 제공합니다.
- Fake downstream MCP fixture는 `initialize`, `tools/list`, `tools/call`, `ping`과 production test fixture(`cancellation`, `schema-change`, `cimd-test`, `dcr-test`, `icons-rich`)를 지원하며 12개 테스트로 고정했습니다.
- Web Admin UI는 nonce 기반 CSP/security headers, `/services`, `/services/[id]`, `/toolbox`, `/clients`, `/settings`, `/playground`, `/logs` route split, Service Detail Tool Control, Services 검색/필터/sort, Playground schema form/replay/diff/pin, Logs filter, client 연결 카드와 Playwright CLI route smoke script를 통과했습니다.
- API CORS는 `COREMCP_CORS_ALLOWED_ORIGINS` 환경변수로 허용 origin을 관리하며, Web UI ↔ API ↔ demo MCP 통합 흐름은 `make ui-smoke`로 검증합니다.
- Web Admin 디자인 시스템은 `docs/design/`에 code-level audit, component pattern, token JSON/CSS/SVG asset으로 정리했고, `cm-*` semantic primitive를 전체 admin route에 반영했습니다.
- Local demo MCP suite는 `apps/demo-mcp-suite`에서 8개 가상 MCP endpoint를 제공하며, 외부 credential 없이 CoreMCP service 등록/validation/도구함/preset/Playground 흐름을 시연할 수 있습니다.
- Codex CLI `exec` 연결 helper를 추가했습니다. `make codex-install`은 Codex 전용 client token을 발급해 `~/.coremcp/codex-client-token`에 저장하고, `codex mcp add coremcp --url ... --bearer-token-env-var COREMCP_CLIENT_TOKEN` 구성을 등록합니다.
- launchd fake-mcp/API/Web/backup/logrotate/refresh 실제 load smoke와 plist 검증을 통과했습니다. Reboot 검증은 실제 재부팅이 필요하며, Tailscale 검증은 현재 머신에 CLI가 없어 skipped 상태입니다.

---


### Remaining Work Classification — 2026-05-14

| 구분 | 남은 항목 |
|---|---|
| 목적 부합 core 미구현 | 현재 known blocker 없음. personal gateway 목적 범위의 core blocker는 로컬 검증 기준 해소됨 |
| 이번 안정화 batch 완료 | STDIO process cap/default idle timeout/delete cleanup, admin `/v1` + `/mcp` fixed-window rate limit, CLI import hardening, Multi-MCP namespace/session/resource routing/P1 운영성 hardening 구현 및 테스트 통과 |
| 외부환경 검증 필요 | actual macOS reboot recovery, Tailscale CLI install/login/Serve/ACL smoke, real external OAuth client compatibility, 실제 모바일 visual QA, long soak — `make external-env-validate`, `make mobile-qa-checklist`, `make soak-check`로 운영 host에서 결과 기록 |
| 선택 Polish | Web Admin UX polish, 관측 dashboard/metric tuning, proactive health probe tuning은 지속 개선 대상 |

### Stabilization Batch Notes — 2026-05-16

- 권장 commit split은 `dev-plan/implement_20260514_224500.md` 및 후속 dev-plan에 기록되어 있습니다. 이 문서/code patch는 commit split을 **계획만** 하며, 사용자가 `commit/push`를 명시 요청하기 전에는 commit을 만들지 않습니다.
- 2026-05-15/16 코드 레벨 안정화 사이클에서 다음 항목이 추가 통합되었습니다.
  - `coremcp.main` 의 라우트 등록을 `coremcp.api.{admin_meta,connections,mcp_endpoint,meta,oauth,playground,services,toolboxes}` 와 `coremcp.mcp.{dispatcher,tools_handlers,resources_handlers,prompts_handlers,catalog,rpc,session_proxy,capabilities,notifications,args_validator}` 로 분리해 main.py 의 god-file 패턴을 해소했습니다 (main.py 라우트 정의 0건).
  - `handle_tools_call` 단일 함수를 `_check_tool_service_resilience` / `_execute_downstream_tool_call` orchestrator 패턴으로 분해해 plugin / circuit breaker / idempotency / record 흐름의 가독성과 단위 테스트성을 확보했습니다.
  - `Repository` 를 `ConnectionsRepositoryMixin` / `AuditRepositoryMixin` / `JobsRepositoryMixin` 분리로 mixin 화하고, 도메인별 facade(`db.repos.{services,catalog,credentials,connections,toolbox,audit,jobs}`) 를 새 라우트에서 점진 채택했습니다.
  - `coremcp.errors` 의 `CoreMcpError` / `CoreMcpValueError` / `CoreMcpRuntimeError` 공통 base 계층에 9개 도메인 예외를 모두 상속시켜 일관 catch 와 builtin 호환성을 양립시켰습니다.
  - `OAuthService.shutdown()` 을 lifespan teardown 에 연결하고, service mutation / circuit-open 시 `forget_downstream_session` 으로 downstream session cache 를 즉시 무효화합니다. TTL 만료는 reaper 가 별도 청소합니다.
  - `app_factory.create_app` 을 공개 import 경계로 명시하고 transition docstring 을 갱신했습니다.
- 최신 API 검증: `cd apps/api && uv run pytest -q` **PASS** (159 passed). 최신 전체 smoke 기준: `make test` **PASS** (API 159 + fake-mcp 12 + demo-mcp-suite 21), 이전 검증 기준 `pnpm lint && pnpm build && pnpm test`, `make ui-smoke`, `git diff --check` **PASS**.
- 외부환경 검증 대표 명령:
  - `make external-env-validate`
  - `COREMCP_EXTERNAL_API_URL=https://<host> COREMCP_EXTERNAL_WEB_URL=https://<host> make external-env-validate`
  - `make mobile-qa-checklist`
  - `COREMCP_SOAK_DURATION_SECONDS=3600 COREMCP_SOAK_INTERVAL_SECONDS=30 make soak-check`

## 📸 Web Admin Screens

CoreMCP Web Admin (`http://localhost:3003`) 의 8개 주요 화면입니다. admin token 입력 후 fetch 결과 기준 캡쳐.

### 1. Dashboard `/`

![Dashboard](coremcp-docs/screenshots/web-admin/01-dashboard.png)

운영 한눈 보기 — KPI 4 카드(Default Toolbox 항목 수, MCP Services active/total, Client Tokens 활성 수, 최근 24h Tool Calls + 평균 latency)와 운영 상태(error rate, max latency, health failing, circuit open) + `Top tools 24h` 로 가장 많이 호출된 도구를 표시합니다. 사이드바 하단 `Health` 버튼으로 `GET /health` 즉시 점검 가능합니다.

### 2. Services `/services`

![Services](coremcp-docs/screenshots/web-admin/02-services.png)

등록된 MCP 서비스 목록 + 신규 등록 폼(name / slug / endpoint_url). 각 row 에서 `Validate`(다운스트림 initialize + tools/list + metadata scan + schema diff) / `도구함 추가` / `Detail` 진입을 한 곳에서 수행합니다. 잘못된 URL(예: `169.254.169.254`) 등록은 SSRF 가드와 slug 중복 가드(`409 conflict`)로 거부됩니다.

### 3. Service Detail `/services/[id]`

![Service Detail](coremcp-docs/screenshots/web-admin/03-service-detail.png)

서비스 1건의 모든 측면 — 메타데이터(endpoint, transport_type, auth_type, last_validated), Tools 탭의 per-tool override(`callable`/`visible_only`/`hidden`) 및 preset(`readonly`/`dangerous_off`/`full_access`), Resources/Prompts catalog, Credentials 등록·회전·삭제, 5-stage validation 이력(url_safety/mcp_initialize/tools_list/metadata_scan/resource_prompt_catalog) 을 확인·조작합니다.

### 4. Toolbox `/toolbox`

![Toolbox](coremcp-docs/screenshots/web-admin/04-toolbox.png)

Default 도구함의 가시 상태 — 등록된 서비스마다 service-level enable/disable 토글과 per-tool count 색 핀(callable / visible_only / hidden / disabled)을 표시합니다. 토글 변경은 즉시 `notifications/tools/list_changed` SSE 로 fan-out 되어 다른 탭의 Playground 도구 목록까지 자동 동기화됩니다.

### 5. Playground `/playground`

![Playground](coremcp-docs/screenshots/web-admin/05-playground.png)

도구함의 도구를 admin 권한으로 직접 호출 — Tool 선택 dropdown(40 tools, `★ Pin` 즐겨찾기), 선택된 도구의 설명·요약 카드, **catalog 캐시된 inputSchema 기반 자동 Schema form** (또는 `JSON 직접 편집` 토글로 raw 모드), 실행 후 응답 JSON 표시, `Replay` 로 동일 args 재호출. 캡쳐는 `demo_ops.ops_status` 를 선택해 `include_services` boolean primitive 가 Schema form 으로 자동 렌더링된 상태입니다.

### 6. Connected AI client `/clients`

![Connected Clients](coremcp-docs/screenshots/web-admin/06-clients.png)

외부 AI agent (Codex CLI exec / Claude Code / Cursor / ChatGPT / OpenClaw 등) 를 CoreMCP에 연결할 때 사용. `등록+Token` 으로 client_type / client_name 입력 후 client token 1회 발급(`cmcp_client_…`), `One-time Token` 으로 OAuth 미지원 client 용 10분 TTL 1회용 prompt+token 발급, `Revoke` 로 즉시 차단(다른 client 영향 0). 모든 발급/회수는 audit 기록됩니다.

### 7. Logs `/logs`

![Logs](coremcp-docs/screenshots/web-admin/07-logs.png)

최근 tool invocation 과 audit event 를 단일 화면에서 — invocation 은 method/exposed_tool_name/status/latency_ms/created_at, audit 은 action(`service.create` / `service.validate.success` / `policy.deny` / `ssrf.block` / `credential.put` 등)/resource_type/resource_id/request_id 표시. `X-Request-ID` 가 invocation 과 audit cross-link 됩니다. metadata 에 admin/client token 노출 0건 (structlog `redact_sensitive_data` processor + 회귀 테스트로 보장).

### 8. Settings / Tokens `/settings`

![Settings / Tokens](coremcp-docs/screenshots/web-admin/08-settings.png)

admin token 은 항상 마스킹 표시 (`cmcp_admin_••••`) — 실제 값은 sessionStorage 에만, 절대 응답·로그에 노출되지 않습니다. 전체 client token 리스트(prefix + scopes + status + last_used_at)와 환경 meta(auth_mode / secret_backend / cache_backend / app_version) 표시. Revoke 는 Settings 와 Connected Clients 양쪽에서 가능합니다.

---

## 📦 Features

| 영역 | 기능 | 상태 |
|---|---|---|
| **MCP Gateway** | `/mcp` Streamable HTTP, initialize/tools/list/tools/call, Mcp-Session-Id, GET SSE | implemented + tested |
| **Protocol** | MCP 2025-11-25 + 2025-06-18 병행 지원 (ADR-029) | implemented + tested |
| **Authentication** | Admin file bearer + per-client DB hash token + MCP scope enforcement | implemented + tested |
| **Codex CLI exec** | Codex MCP config 등록 + client token env wrapper + non-LLM MCP smoke | implemented + tested |
| **Demo MCP Suite** | 8개 local demo MCP endpoint + registration payload + preset demo tools | implemented + tested |
| **MCP Registry** | private service 등록, category/homepage/docs/logo metadata, validation, schema cache + schema diff detail | implemented + tested |
| **Toolbox** | default toolbox, item enable/disable, tool-level override(`hidden`/`visible_only`/`callable`), preset(`readonly`/`dangerous_off`/`full_access`), dynamic catalog | implemented + tested |
| **Tool Alias** | `service_slug.tool_name` 매핑, primary alias | implemented |
| **Downstream Proxy** | bearer/api_key vault, timeout, redirect block, JSON content-type/size sanitizer, idempotency cache, error mapping | implemented + tested |
| **Credential Vault** | macOS Keychain adapter / Fernet encrypted fallback | implemented + tested |
| **Web Admin UI** | Next.js 15 admin route split + sessionStorage token + nonce CSP headers | implemented + route smoke |
| **SSRF Guard** | allowlist 기반, metadata IP hard reject | implemented + tested |
| **Tool Poisoning Scanner** | regex pattern + Unicode/homoglyph + SVG default-off | implemented |
| **Audit / Invocation Log** | 보안·실행 이벤트, request_id 추적, secret redaction | implemented + tested |
| **listChanged / Cancellation** | dynamic SSE emission, cancellation logging + downstream forward | implemented + tested |
| **One-time Token** | OpenClaw 등 OAuth 미지원 client용 issue/exchange | implemented + tested |
| **OAuth 2.1 (옵션)** | CIMD First + DCR Fallback (ADR-036), PKCE, JWKS, refresh/revoke/introspect | implemented + local tested |
| **Metrics** | `METRICS_ENABLED=true`일 때 Prometheus `/metrics` 노출, MCP/tool/auth/policy/timeout counters | implemented + tested |
| **Daemon** | launchd + fake-mcp/API/Web 자동 시작 + daily SQLite backup label + logrotate + scheduled service refresh + ops smoke | implemented; fake/api/web/backup/logrotate/refresh load verified |

---

## 📋 Prerequisites

| 항목 | 요구사항 |
|---|---|
| 하드웨어 | Mac mini (24/7 가동 권장) |
| OS | macOS 13+ |
| Python | 3.12+ (uv 또는 poetry) |
| Node.js | 18+ (pnpm + Turborepo) |
| Storage | `~/.coremcp/` 최소 1GB |
| Network | localhost 또는 Tailscale (외부 노출 시) |
| MCP Client | Codex CLI `exec` (필수), Claude Code/ChatGPT/Cursor 등은 옵션 |

---

## 🚀 Quick Start

> 현재 저장소 기준 로컬 실행 명령입니다. 가장 빠른 방법은 `make run`입니다.

```bash
# 1. 의존성/토큰/DB migration/Web build 후 launchd로 background 실행
make run

# 2. 상태 확인
make status
infra/scripts/ops-smoke.sh
infra/scripts/web-route-smoke.sh
make ui-smoke

# 3. Web Admin
open http://127.0.0.1:3003

# 4. Codex CLI exec에 CoreMCP 등록
make codex-install
make codex-smoke

# 5. Codex CLI exec에서 CoreMCP 도구함 사용
infra/scripts/codex-exec-coremcp.sh "CoreMCP MCP 도구 목록을 확인해줘"

# 6. 중지
make stop
```

Foreground로 터미널에서 직접 실행하려면 다음을 사용합니다.

```bash
make run-local
```

상세는 [`coremcp-docs/09-implementation-plan.md`](./coremcp-docs/09-implementation-plan.md) §7 First Working Vertical Slice 참조.

---

## 🏗️ Architecture

### 단일 프로세스 구성 (MVP)

```text
launchd (com.coremcp.api)
  │
  ▼
┌──────────────────────────────────────────────────────┐
│  FastAPI (uvicorn, single worker)                    │
│  ┌─────────────────┐  ┌─────────────────┐            │
│  │ /mcp gateway    │  │ /v1 admin API   │            │
│  │  - Streamable   │  │  - services     │            │
│  │  - JSON-RPC     │  │  - toolbox      │            │
│  │  - SSE          │  │  - credentials  │            │
│  └────────┬────────┘  │  - settings     │            │
│           │           │  - logs         │            │
│           │           └─────────────────┘            │
│           ▼                                          │
│  ┌────────────────────────────────────────────────┐  │
│  │ Auth Middleware (Admin / Client Bearer)        │  │
│  └────────────────────────────────────────────────┘  │
│           │                                          │
│  ┌────────▼──────────┐  ┌───────────────────────┐    │
│  │ Toolbox Resolver  │  │ BackgroundTasks       │    │
│  │ Tool Catalog      │  │  - validation         │    │
│  │ Alias Resolver    │  │  - schema refresh     │    │
│  └────────┬──────────┘  └───────────────────────┘    │
│           │                                          │
│  ┌────────▼──────────────────────────────────────┐   │
│  │ Downstream Proxy Executor (httpx async)       │   │
│  └────────┬──────────────────────────────────────┘   │
└───────────┼──────────────────────────────────────────┘
            │ Authorization: Bearer <downstream_token>
            ▼
       External MCP Services
```

### Dual Token Model (ADR-030)

| Token | Prefix | 저장 | 용도 | Revoke |
|---|---|---|---|---|
| **Admin** | `cmcp_admin_*` | `~/.coremcp/admin-token` (chmod 600) | `/v1/*` root + `/mcp` fallback | 파일 회전 |
| **Client** | `cmcp_client_*` | DB `personal_access_tokens.token_hash` (sha256) | `/mcp` per-connection | external_connection 단위 |

### Request Flow (tools/call)

```text
Codex CLI exec ──▶  /mcp tools/call
                  │
                  ▼
            Auth: admin file 비교 또는 client hash DB lookup
                  │
                  ▼
            Alias Resolver: github.create_issue → svc_xx + tool_yy
                  │
                  ▼
            Toolbox membership check + Policy check
                  │
                  ▼
            Credential Vault: Keychain or Fernet → downstream secret
                  │
                  ▼
            Downstream MCP  ──▶  실제 API 호출
                  │
                  ▼
            Result + tool_invocations log (BG)
                  │
                  ▼
            Codex CLI exec (result)
```

---

## 📁 Project Structure

```text
CoreMCP/
├── README.md                       # 본 문서
├── CLAUDE.md                       # LLM coding agent 가이드
├── AGENTS.md                       # 프로젝트별 agent 작업 규칙
├── apps/
│   ├── api/                        # FastAPI backend
│   │   ├── coremcp/
│   │   │   ├── app_factory.py      # 공개 create_app 경계 (외부 import 진입점)
│   │   │   ├── main.py             # lifespan + middleware + helper 보유 (라우트 정의 0)
│   │   │   ├── cli.py              # coremcp CLI (doctor / service / tool / token / export)
│   │   │   ├── errors.py           # CoreMcpError / *ValueError / *RuntimeError 공통 계층
│   │   │   ├── settings.py         # Pydantic Settings (환경변수 정의)
│   │   │   ├── logging.py          # structlog 설정 + redact processor
│   │   │   ├── refresh.py          # launchd 주기 catalog refresh entry
│   │   │   ├── smoke.py            # in-process smoke test
│   │   │   ├── api/                # FastAPI 라우트 (도메인별 분리)
│   │   │   │   ├── meta.py             # /health, /live, /ready, /metrics
│   │   │   │   ├── admin_meta.py       # /v1/me, /settings, audit-logs, invocations
│   │   │   │   ├── mcp_endpoint.py     # /mcp POST / GET (SSE) / DELETE
│   │   │   │   ├── services.py         # /v1/mcp-services CRUD + validate + tools/creds
│   │   │   │   ├── toolboxes.py        # /v1/toolboxes 및 items / overrides
│   │   │   │   ├── connections.py      # external-connections + client-tokens + one-time
│   │   │   │   ├── playground.py       # /v1/playground/*
│   │   │   │   └── oauth.py            # /.well-known/* + /oauth/*
│   │   │   ├── mcp/                # MCP method 처리 (라우트와 분리)
│   │   │   │   ├── dispatcher.py       # JSON-RPC method 분기
│   │   │   │   ├── rpc.py              # downstream JSON-RPC 공통 helper
│   │   │   │   ├── tools_handlers.py   # tools/list, tools/call orchestrator
│   │   │   │   ├── resources_handlers.py # resources/list/templates/list/read
│   │   │   │   ├── prompts_handlers.py # prompts/list, prompts/get
│   │   │   │   ├── catalog.py          # 통합 카탈로그 helper
│   │   │   │   ├── args_validator.py   # JSON Schema 인자 검증
│   │   │   │   ├── capabilities.py     # 서비스 capability union → initialize 응답
│   │   │   │   ├── notifications.py    # downstream notification fan-in/out
│   │   │   │   └── session_proxy.py    # downstream session cache + TTL/forget
│   │   │   ├── mcp_gateway/        # SessionStore / ListChangedEventBus / Idempotency / Reaper / Protocol
│   │   │   ├── plugins/            # 닫힌 기본의 plugin framework (before/after tool call hook)
│   │   │   ├── auth/               # admin / client_tokens / oauth / rate_limit
│   │   │   ├── registry/           # catalog normalizer + tool poisoning scanner
│   │   │   ├── credentials/        # macOS Keychain + Fernet vault backend
│   │   │   ├── proxy/              # downstream HTTP/STDIO client + circuit breaker + SSRF
│   │   │   └── db/                 # Repository mixin + repos/* 도메인 facade + migrations
│   │   │       ├── repository.py             # 베이스 + mixin 결합 (Connections / Audit / Jobs)
│   │   │       ├── repository_connections.py # 연결/토큰 도메인 mixin
│   │   │       ├── repository_audit.py       # audit/invocation mixin
│   │   │       ├── repository_jobs.py        # 잡 mixin
│   │   │       ├── repository_facade.py      # 도메인 facade 묶음
│   │   │       ├── repository_constants.py   # LOCAL_USER_ID / DEFAULT_TOOLBOX_ID
│   │   │       ├── repository_ids.py         # new_id prefix helper
│   │   │       ├── migrations.py             # Alembic upgrade("head") 단일 진입점
│   │   │       └── repos/                    # services / catalog / credentials / connections / toolbox / audit / jobs
│   │   └── alembic/                # 마이그레이션 (0001 ~ 0008)
│   ├── web/                        # Next.js 15 admin (App Router)
│   │   ├── app/                    # Dashboard / Services / Toolbox / Clients / Playground / Logs / Settings
│   │   ├── components/admin/       # admin-shell, sections/*, icons, tool-icon
│   │   └── middleware.ts           # CSP / nonce / 보안 헤더
│   ├── fake-mcp/                   # 테스트용 downstream MCP (단일 server)
│   └── demo-mcp-suite/             # 8개 가상 demo MCP endpoint
├── packages/
│   ├── shared-types/
│   └── client-profiles/
├── infra/
│   ├── launchd/                    # api/web/backup/logrotate/refresh plists
│   ├── docker/                     # 옵션 Postgres/Redis
│   └── scripts/                    # backup, restore, launchctl, log rotation, ops/web/ui-smoke
├── docs/
│   └── design/                     # Web Admin design system + token assets
├── dev-plan/                       # 단계별 개발 계획 (implement_YYYYMMDD_HHMMSS.md)
├── coremcp-docs/                   # 본 문서팩 (17 files, 정본)
└── production_docs_donotuse/       # SaaS 청사진 (참고용 only)
```

---

## 📖 Documentation

전체 17개 제품/구현 문서가 [`coremcp-docs/`](./coremcp-docs/)에 있습니다. Web Admin의 코드 레벨 디자인 시스템은 [`docs/design/`](./docs/design/)에 별도로 정리했습니다.

| # | 문서 | 핵심 내용 |
|---:|---|---|
| 00 | [Overview](./coremcp-docs/00-overview.md) | 제품 요약, 범위, 비-목표, 핵심 용어 |
| 01 | [Features](./coremcp-docs/01-features.md) | 포함/부분포함/제외 매트릭스 (10 카테고리) |
| 02 | [TRD](./coremcp-docs/02-trd.md) | 기술 요구사항, MCP version, error taxonomy |
| 03 | [Architecture](./coremcp-docs/03-architecture.md) | 시스템 아키텍처, 8개 sequence diagram |
| 04 | [API Spec](./coremcp-docs/04-api-spec.md) | MCP Protocol + REST `/v1` 완전 명세 |
| 05 | [DB Schema](./coremcp-docs/05-database-schema.md) | SQLite/Postgres DDL, partial unique 5개 |
| 06 | [Security & Auth](./coremcp-docs/06-security-auth.md) | Token boundary, SSRF, CIMD, SVG XSS 방어 |
| 07 | [MCP Proxy Spec](./coremcp-docs/07-mcp-proxy-spec.md) | proxy mode, alias, cache, error mapping |
| 08 | [Frontend UX](./coremcp-docs/08-frontend-ux.md) | 본인용 admin Web UI 설계 |
| 09 | [Implementation Plan](./coremcp-docs/09-implementation-plan.md) | Phase P0~P3, 4~5주 1인 작업 |
| 10 | [Test Plan](./coremcp-docs/10-test-plan.md) | unit/integration/e2e/compatibility |
| 11 | [Risk Notes](./coremcp-docs/11-risk-notes.md) | R-101~R-115 위험 + mitigation |
| 12 | [Operations](./coremcp-docs/12-operations.md) | launchd, backup, runbook 8개 |
| 13 | [ADR](./coremcp-docs/13-adr.md) | 36개 아키텍처 의사결정 기록 |
| 14 | [MCP Client Profiles](./coremcp-docs/14-mcp-client-profiles.md) | Codex CLI exec / Claude Code / ChatGPT / Cursor 호환성 |
| 15 | [Future SaaS Migration](./coremcp-docs/15-future-saas-migration.md) | SaaS 전환 trigger + 절차 |

### Design documentation

| 문서/Asset | 핵심 내용 |
|---|---|
| [Design System](./docs/design/README.md) | CoreMCP personal admin console의 목표, token, 화면 구조 원칙 |
| [Code-level Audit](./docs/design/code-level-audit.md) | `apps/web` theme/global/component class 분석과 반복 지점 |
| [Component Patterns](./docs/design/component-patterns.md) | `cm-card`, `cm-panel`, `cm-button`, `cm-input`, `ToolIcon` 사용 규칙 |
| [Theme Tokens](./docs/design/assets/coremcp-theme.tokens.json) | 색상/반경/그림자/font token JSON |
| [Palette SVG](./docs/design/assets/coremcp-palette.svg) | 팔레트와 semantic tone 시각 asset |

---

## 🗺️ Phase Plan

총 4 phase, **약 4~5주** (1인 작업 기준).

```text
P0 (1주)              P1 (1.5주)            P2 (1~2주)         P3 (1주)
┌──────────────┐     ┌──────────────┐      ┌──────────────┐    ┌──────────────┐
│ Vertical     │ ──▶ │ Real Service │ ───▶ │ Web Admin UI │ ──▶│ Daemon + OAuth│
│ Slice        │     │ + Per-Client │      │              │    │ + Hardening   │
│              │     │ Token        │      │              │    │               │
└──────────────┘     └──────────────┘      └──────────────┘    └──────────────┘
   admin token         real MCP 연결          Settings/Tokens     launchd + CIMD
   /mcp minimal        credential vault       Playground          + Tailscale
   fake-mcp            SSRF allowlist         Logs viewer         + backup cron
   protocol nego       icons top-level
```

| Phase | 기간 | 목표 | Exit |
|:---:|:---:|---|---|
| **P0** | 1주 | Admin/client token → fake-mcp → Codex CLI exec 성공 | invocation log 1줄 |
| **P1** | 1.5주 | Per-client token + 실제 MCP + vault | Mac mini + MacBook 분리 동작 |
| **P2** | 1~2주 | Web Admin UI (Settings/Tokens dual model) | 마우스 조작만으로 운영 |
| **P3** | 1주 | launchd + Tailscale + OAuth/CIMD | 무인 운영 1주 안정 |

---

## 🔒 Security Highlights

CoreMCP는 단일 사용자 환경이지만 **SaaS급 보안 원칙**을 적용한다.

| 영역 | 정책 | ADR |
|---|---|---|
| **Token boundary** | CoreMCP token은 downstream에 **절대 전달 금지** | ADR-004 |
| **Dual token revoke** | admin 회전 / client per-connection 즉시 invalidate | ADR-030 |
| **SSRF allowlist** | 기본 private/loopback/CGNAT 전부 차단, `ALLOW_TAILSCALE_DOWNSTREAM` 등 명시 opt-in 구현 | ADR-033 |
| **DNS pinning** | DNS host downstream은 검증된 IP로 request URL을 pinning하고 원래 Host/SNI를 유지 | ADR-033 |
| **CIMD validation** | `client_id` byte-exact match, content-type charset 허용, TTL fixed 1h | ADR-036 |
| **SVG XSS 방어** | `<img>` only 렌더링, CSP `img-src 'self' data:`, `ICON_SVG_ENABLED=false` default 권장 | 06 §6.2 |
| **AUTH_MODE 분리** | `static_bearer` default 시 `/.well-known/oauth-protected-resource` = **404** | ADR-032 |
| **Credential vault** | macOS Keychain envelope 또는 Fernet, DB는 secret_ref만 | ADR-012/031 |
| **Tool poisoning** | regex pattern + Unicode NFKC + RTL/zero-width strip + homoglyph 경고 | 06 §8 |
| **Audit + Redaction** | 모든 보안 이벤트 기록, authorization/token/api_key 자동 마스킹 | 06 §11 |
| **Partial unique** | soft-delete 호환 (mcp_services, toolbox_items, tool_aliases, etc.) | ADR-035 |

---

OAuth 운영 경고: `AUTH_MODE=oauth`는 개인 단일 사용자용 옵션이며 현재 별도 사람-클릭 consent UI 없이 local authorization을 자동 승인한다. Tailscale/localhost 접근 권한을 본인 장비로 제한하고, 외부 user-agent가 `/oauth/authorize`에 접근할 수 없도록 ACL/방화벽을 먼저 적용해야 한다.

## ⚙️ Configuration

### 핵심 환경 변수

```bash
# Core
COREMCP_HOST=127.0.0.1
COREMCP_PORT=8787
COREMCP_DATA_DIR=~/.coremcp

# Auth (ADR-030, ADR-032)
COREMCP_ADMIN_TOKEN_FILE=~/.coremcp/admin-token
AUTH_MODE=static_bearer                  # static_bearer | oauth
EXPOSE_RESOURCE_METADATA_IN_STATIC_MODE=false

# Protocol (ADR-029)
MCP_SUPPORTED_VERSIONS=2025-11-25,2025-06-18
MCP_DEFAULT_VERSION=2025-11-25

# DB
DATABASE_URL=sqlite+aiosqlite:////Users/me/.coremcp/data/db.sqlite3

# Secret backend (ADR-031)
SECRET_BACKEND=keychain                  # keychain | fernet
# keychain: Mac mini 자동 로그인 환경
# fernet:   headless 무인 운영 (FERNET_KEY_FILE 필요)
FERNET_KEY_FILE=~/.coremcp/data/secrets.key

# Downstream HTTP
COREMCP_DOWNSTREAM_TIMEOUT_SECONDS=35
COREMCP_DOWNSTREAM_CONNECT_TIMEOUT_SECONDS=3
COREMCP_DOWNSTREAM_READ_TIMEOUT_SECONDS=30
COREMCP_DOWNSTREAM_MAX_RESPONSE_BYTES=1048576    # 1MB downstream 응답 cap
COREMCP_MAX_REQUEST_BODY_BYTES=1048576           # 1MB inbound /mcp body cap
COREMCP_INITIALIZE_DOWNSTREAM_TIMEOUT_SECONDS=2  # init 단계 별도 짧은 timeout
COREMCP_DOWNSTREAM_SESSION_TTL_SECONDS=3600      # downstream Mcp-Session-Id 캐시 TTL

# Service health probe
COREMCP_SERVICE_HEALTH_PROBE_ENABLED=true
COREMCP_SERVICE_HEALTH_PROBE_INTERVAL_SECONDS=60
COREMCP_SERVICE_HEALTH_PROBE_TIMEOUT_SECONDS=2

# Downstream STDIO transport (CVE-2026-30623 defense-in-depth)
COREMCP_STDIO_ALLOWED_COMMANDS=npx,uvx,python,python3,node,docker,deno
COREMCP_STDIO_MAX_CONCURRENT_PROCESSES=8
COREMCP_STDIO_DEFAULT_IDLE_TIMEOUT_SECONDS=300

# Rate limit (fixed-window, in-process)
COREMCP_AUTH_RATE_LIMIT_PER_MINUTE=60      # OAuth /authorize/token/register/revoke
COREMCP_MCP_RATE_LIMIT_PER_MINUTE=120      # /mcp 엔드포인트
COREMCP_SERVICE_RATE_LIMIT_PER_MINUTE=120  # per-service (service_id 키)

# Web Admin ↔ API (cross-origin)
COREMCP_CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003

# SSRF (ADR-033)
COREMCP_SSRF_ALLOW_HOSTS=127.0.0.1,localhost  # fake-mcp/local MCP 개발용
ALLOW_TAILSCALE_DOWNSTREAM=false
COREMCP_SSRF_ALLOW_CIDRS=

# Icons (06 §6.2)
ICON_SVG_ENABLED=false                   # SVG XSS 방어 default

# Observability (옵션)
METRICS_ENABLED=false                    # true 시 Prometheus /metrics 노출
```

전체 환경 변수는 [`coremcp-docs/02-trd.md`](./coremcp-docs/02-trd.md) §11 참조.

---

## 📝 Architecture Decisions (Highlights)

총 38개 ADR이 [`coremcp-docs/13-adr.md`](./coremcp-docs/13-adr.md)에 있습니다. P0/P1 핵심 결정:

| ADR | 결정 | Status |
|---|---|---|
| ADR-001 | Product Concept = Toolbox First | Accepted |
| ADR-002 | Proxy Mode (not Meta-tool) | Accepted |
| ADR-004 | No Token Passthrough | Accepted |
| ADR-019 | Tool Naming = `service.tool` (dot) | Accepted |
| ADR-020 | Data Region = Local Mac mini | Accepted |
| ADR-029 | Protocol Support = 2025-06-18 + 2025-11-25 | Accepted |
| ADR-030 | Token Model = Dual (admin file + client DB hash) | Accepted |
| ADR-031 | Secret Backend = Keychain default / Fernet headless | Accepted |
| ADR-032 | Auth Mode = static_bearer default, OAuth optional | Accepted |
| ADR-033 | SSRF Private CIDR Allowlist | Accepted |
| ADR-034 | Error Mapping = Protocol vs Tool Result Separation | Accepted |
| ADR-035 | Soft-delete Partial Unique Index | Accepted |
| ADR-036 | OAuth Client = CIMD First, DCR Fallback | Accepted |
| ADR-038 | Bidirectional RPC = default reject + future opt-in gates | Accepted |

---

## 🎯 Roadmap

### 현재 (Personal MCP Gateway)
- **Phase P0~P3 완료 후**: 본인 Mac mini에서 무인 운영 + Codex CLI exec 동작
- **목표**: 1년 사용 + 안정성 검증

### 미래 (SaaS Migration — Trigger 발생 시)
- **Trigger 후보**: 신뢰하는 사용자 추가, 팀 사용, OpenSource 공개
- **전환 절차**: [`coremcp-docs/15-future-saas-migration.md`](./coremcp-docs/15-future-saas-migration.md)
- **영향 ADR**: ADR-020/021/022 → Superseded + 신규 SaaS ADR 작성

---

## 🔧 Troubleshooting

| 증상 | 원인 | 조치 |
|---|---|---|
| `make codex-smoke` 후 401 | Codex client token 만료/불일치 | `make codex-install` 재실행 또는 `infra/scripts/codex-mcp-install.sh --force --rotate-token` |
| Mac mini 재부팅 후 credential 실패 | Keychain 잠금 | 자동 로그인 활성 또는 `SECRET_BACKEND=fernet` 전환 |
| `/mcp tools/call` 응답 timeout | downstream 35s 초과 | `DOWNSTREAM_READ_TIMEOUT_MS` 조정 또는 downstream 점검 |
| Tailscale에서 차단 | `ALLOW_TAILSCALE_DOWNSTREAM=false` | 환경 변수 `true` + `ALLOWED_PRIVATE_CIDRS` 명시 |
| SQLite "database is locked" | WAL 미적용 | `PRAGMA journal_mode=WAL` 확인 |

전체 runbook 8개는 [`coremcp-docs/12-operations.md`](./coremcp-docs/12-operations.md) §7 참조.

---

## 📜 License

**Private Repository** — All rights reserved.

본 프로젝트는 개인 사용 목적으로 운영되며 외부 공개·배포 계획이 없습니다. ToS / Privacy / DPA 등 법무 문서는 적용되지 않습니다. 추후 공개 결정 시 MIT 또는 Apache 2.0 검토. 자세한 결정 근거는 [ADR-022](./coremcp-docs/13-adr.md)와 [ADR-025](./coremcp-docs/13-adr.md) 참조.

---

## 🙏 References

### MCP Specification
- [MCP Spec 2025-11-25 (latest)](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP Spec 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18)
- [MCP Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
- [OpenAI Codex CLI Reference](https://developers.openai.com/codex/cli/reference)

### OAuth / Auth RFCs
- [RFC 7591 — Dynamic Client Registration](https://datatracker.ietf.org/doc/html/rfc7591)
- [RFC 7636 — PKCE](https://datatracker.ietf.org/doc/html/rfc7636)
- [RFC 7662 — Token Introspection](https://datatracker.ietf.org/doc/html/rfc7662)
- [RFC 7009 — Token Revocation](https://datatracker.ietf.org/doc/html/rfc7009)
- [RFC 8707 — Resource Indicators](https://datatracker.ietf.org/doc/html/rfc8707)
- [RFC 9728 — OAuth Protected Resource Metadata](https://datatracker.ietf.org/doc/html/rfc9728)
- [RFC 8785 — JSON Canonicalization Scheme](https://datatracker.ietf.org/doc/html/rfc8785)
- [OAuth 2.1 draft](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1)

### Benchmarks / Related
- [PlayMCP](https://playmcp.kakao.com) — 카카오 PlayMCP 벤치마크 reference

---

<div align="center">

**문서팩 v1.0** · 작성일 2026-05-11 · MCP 2025-11-25 + 2025-06-18

[코드 시작 → coremcp-docs/09-implementation-plan.md](./coremcp-docs/09-implementation-plan.md)

</div>
