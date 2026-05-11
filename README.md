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
[![Status](https://img.shields.io/badge/Status-Docs%20Ready-yellow.svg)](#-phase-plan)
[![Docs](https://img.shields.io/badge/Docs-17%20files-blue.svg)](./coremcp-docs/)

내 Mac mini에서 모든 MCP를 한 곳에 모아 어디서든 쓴다.

[Overview](#overview) · [Quick Start](#-quick-start) · [Architecture](#%EF%B8%8F-architecture) · [Documentation](#-documentation) · [Phase Plan](#-phase-plan)

</div>

---

## Overview

CoreMCP는 본인 1명이 Mac mini에서 운영하는 protected MCP gateway다. 여러 MCP 서버를 등록해 자신의 도구함(toolbox)에 담고, Claude Code 등 외부 AI 클라이언트에는 CoreMCP 하나만 연결한다.

```text
┌─────────────────────────────────────────────────────────────┐
│ External AI Clients                                         │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐         │
│ │ Claude   │ │ Claude   │ │ OpenClaw │ │ ChatGPT  │  ...    │
│ │ Code     │ │ desktop  │ │          │ │ (옵션)   │         │
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

---

## 📦 Features

| 영역 | 기능 | 상태 |
|---|---|---|
| **MCP Gateway** | `/mcp` Streamable HTTP, initialize/tools/list/tools/call, Mcp-Session-Id, GET SSE | Phase P0 |
| **Protocol** | MCP 2025-11-25 + 2025-06-18 병행 지원 (ADR-029) | Phase P0 |
| **Authentication** | Dual token (Admin file + Per-client DB hash, ADR-030) | Phase P0/P1 |
| **MCP Registry** | private service 등록, validation, schema cache, drift detection | Phase P1 |
| **Toolbox** | per-user toolbox, item enable/disable, dynamic catalog | Phase P1 |
| **Tool Alias** | `service_slug.tool_name` 매핑, slug rename grace (ADR-016) | Phase P1 |
| **Downstream Proxy** | bearer/api_key vault, timeout/cancellation/idempotency | Phase P1 |
| **Credential Vault** | macOS Keychain (default) / Fernet (headless, ADR-031) | Phase P1 |
| **Web Admin UI** | Next.js 15 + shadcn/ui, dashboard/services/toolbox/logs | Phase P2 |
| **SSRF Guard** | allowlist 기반, Tailscale CIDR 명시 허용 (ADR-033) | Phase P1 |
| **Tool Poisoning Scanner** | regex pattern + Unicode/homoglyph + SVG sanitize | Phase P1 |
| **Audit / Invocation Log** | 모든 보안·실행 이벤트, secret redaction | Phase P1 |
| **listChanged Emission** | toolbox 변경 시 SSE push | Phase P1 |
| **One-time Token** | OpenClaw 등 OAuth 미지원 client용 | Phase P3 |
| **OAuth 2.1 (옵션)** | CIMD First + DCR Fallback (ADR-036) | Phase P3 |
| **Daemon** | launchd + 자동 시작 + SQLite backup + log rotation | Phase P3 |

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
| MCP Client | Claude Code (필수), 그 외 옵션 |

---

## 🚀 Quick Start

> Phase P0 vertical slice 완성 후 동작하는 명령. 현재는 문서 기반 plan.

```bash
# 1. 디렉토리 생성
mkdir -p ~/.coremcp/{data,logs,backups}
chmod 700 ~/.coremcp

# 2. Admin token 생성 (root 관리자, 파일 보관)
python -c "import secrets; print('cmcp_admin_' + secrets.token_urlsafe(32))" \
  > ~/.coremcp/admin-token
chmod 600 ~/.coremcp/admin-token

# 3. Backend 실행
cd apps/api
uv sync && uv run alembic upgrade head
uv run uvicorn coremcp.main:app --host 127.0.0.1 --port 8787

# 4. Web Admin UI 실행 (옵션, Phase P2 이후)
cd apps/web
pnpm install && pnpm dev

# 5. Claude Code에 CoreMCP 등록
claude mcp add --transport http coremcp http://localhost:8787/mcp \
  --header "Authorization: Bearer $(cat ~/.coremcp/admin-token)"

# 6. (옵션) Per-client token 발급 (Phase P1 이후)
#    Web UI: Settings → Tokens → "+ Generate new client token"
#    또는: curl -X POST http://localhost:8787/v1/settings/client-tokens \
#          -H "Authorization: Bearer $(cat ~/.coremcp/admin-token)"
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
Claude Code  ──▶  /mcp tools/call
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
            Claude Code (result)
```

---

## 📁 Project Structure

```text
CoreMCP/
├── README.md                       # 본 문서
├── CLAUDE.md                       # Claude Code 가이드
├── apps/
│   ├── api/                        # FastAPI backend (Phase P0~)
│   │   ├── coremcp/
│   │   │   ├── main.py             # FastAPI entry
│   │   │   ├── auth/               # admin / client / oauth
│   │   │   ├── mcp_gateway/        # /mcp dispatcher + SSE
│   │   │   ├── registry/           # services + SSRF + scanner
│   │   │   ├── toolbox/            # catalog builder + alias
│   │   │   ├── credentials/        # vault backends
│   │   │   ├── proxy/              # downstream executor
│   │   │   ├── audit/              # logger
│   │   │   └── invocations/
│   │   └── alembic/                # migration
│   ├── web/                        # Next.js admin (Phase P2~)
│   │   └── app/                    # Dashboard / Services / Logs
│   └── fake-mcp/                   # 테스트용 downstream
├── packages/
│   ├── shared-types/
│   └── client-profiles/
├── infra/
│   ├── launchd/                    # com.coremcp.api.plist
│   ├── docker/                     # 옵션 Postgres/Redis
│   └── scripts/                    # backup, rotate-token
├── coremcp-docs/                   # 본 문서팩 (17 files, 정본)
└── production_docs_donotuse/       # SaaS 청사진 (참고용 only)
```

---

## 📖 Documentation

전체 17개 문서가 [`coremcp-docs/`](./coremcp-docs/)에 있습니다. 본 README는 진입점이고, 상세는 각 문서를 참조하세요.

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
| 14 | [MCP Client Profiles](./coremcp-docs/14-mcp-client-profiles.md) | Claude Code / ChatGPT / Cursor 호환성 |
| 15 | [Future SaaS Migration](./coremcp-docs/15-future-saas-migration.md) | SaaS 전환 trigger + 절차 |

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
| **P0** | 1주 | Admin token → fake-mcp → Claude Code 성공 | invocation log 1줄 |
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
| **SSRF allowlist** | 기본 private/loopback/CGNAT 전부 차단, `ALLOW_TAILSCALE_DOWNSTREAM` 등 명시 opt-in | ADR-033 |
| **CIMD validation** | `client_id` byte-exact match, content-type charset 허용, TTL fixed 1h | ADR-036 |
| **SVG XSS 방어** | `<img>` only 렌더링, CSP `img-src 'self' data:`, `ICON_SVG_ENABLED=false` default 권장 | 06 §6.2 |
| **AUTH_MODE 분리** | `static_bearer` default 시 `/.well-known/oauth-protected-resource` = **404** | ADR-032 |
| **Credential vault** | macOS Keychain envelope 또는 Fernet, DB는 secret_ref만 | ADR-012/031 |
| **Tool poisoning** | regex pattern + Unicode NFKC + RTL/zero-width strip + homoglyph 경고 | 06 §8 |
| **Audit + Redaction** | 모든 보안 이벤트 기록, authorization/token/api_key 자동 마스킹 | 06 §11 |
| **Partial unique** | soft-delete 호환 (mcp_services, toolbox_items, tool_aliases, etc.) | ADR-035 |

---

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

# Downstream
DOWNSTREAM_CONNECT_TIMEOUT_MS=3000
DOWNSTREAM_READ_TIMEOUT_MS=30000
DOWNSTREAM_MAX_BODY_MB=5
DOWNSTREAM_MAX_REDIRECTS=0

# SSRF (ADR-033)
ALLOW_LOOPBACK_DOWNSTREAM=true           # fake-mcp 개발용
ALLOW_TAILSCALE_DOWNSTREAM=false
ALLOWED_PRIVATE_CIDRS=

# Icons (06 §6.2)
ICON_SVG_ENABLED=false                   # SVG XSS 방어 default
```

전체 환경 변수는 [`coremcp-docs/02-trd.md`](./coremcp-docs/02-trd.md) §11 참조.

---

## 📝 Architecture Decisions (Highlights)

총 36개 ADR이 [`coremcp-docs/13-adr.md`](./coremcp-docs/13-adr.md)에 있습니다. P0/P1 핵심 결정:

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

---

## 🎯 Roadmap

### 현재 (Personal MCP Gateway)
- **Phase P0~P3 완료 후**: 본인 Mac mini에서 무인 운영 + Claude Code 동작
- **목표**: 1년 사용 + 안정성 검증

### 미래 (SaaS Migration — Trigger 발생 시)
- **Trigger 후보**: 신뢰하는 사용자 추가, 팀 사용, OpenSource 공개
- **전환 절차**: [`coremcp-docs/15-future-saas-migration.md`](./coremcp-docs/15-future-saas-migration.md)
- **영향 ADR**: ADR-020/021/022 → Superseded + 신규 SaaS ADR 작성

---

## 🔧 Troubleshooting

| 증상 | 원인 | 조치 |
|---|---|---|
| `claude mcp add` 후 401 | admin token 파일 mismatch | `cat ~/.coremcp/admin-token`으로 확인 후 헤더 재구성 |
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
- [Claude Code MCP Docs](https://code.claude.com/docs/en/mcp)

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
