<div align="center">

# CoreMCP Documentation Pack

**개인용 MCP Gateway — 17개 문서 / v1.0**

[![Docs](https://img.shields.io/badge/Docs-17%20files-blue.svg)](#-document-index)
[![ADR](https://img.shields.io/badge/ADR-36-purple.svg)](./13-adr.md)
[![MCP Spec](https://img.shields.io/badge/MCP-2025--11--25-FF6B6B?logo=anthropic&logoColor=white)](https://modelcontextprotocol.io/specification/2025-11-25)
[![Status](https://img.shields.io/badge/Status-Docs%20Ready-yellow.svg)](#-current-status)
[![Scope](https://img.shields.io/badge/Scope-Personal-green.svg)](#-current-status)

[Document Index](#-document-index) · [Quick Start](#-quick-start) · [Phase Plan](#-phase-plan) · [Design Principles](#-design-principles)

[← 메인 README로](../README.md)

</div>

---

## 📍 Current Status

| 항목 | 값 |
|---|---|
| 적용 범위 | 개인 사용 (본인 1명, Mac mini 단일 호스트) |
| 언어 | 한국어 우선 |
| 현재 Phase | **Phase 0 — Vertical Slice 준비** |
| 문서 버전 | v1.0 (2026-05-11) |
| ADR 개수 | 36 (ADR-001 ~ ADR-036) |
| MCP Spec | 2025-11-25 + 2025-06-18 병행 (ADR-029) |
| Token Model | Dual — admin file + client DB hash (ADR-030) |
| Auth Mode | static_bearer default, OAuth 옵션 (ADR-032) |

> 본 문서팩이 **실제 구현의 정본**이다.
> 프로덕션 SaaS 청사진은 `../production_docs_donotuse/`에 보관되어 있으며, 본 프로젝트에는 적용하지 않는다.

---

## 📚 Document Index

### Overview & Spec (00 ~ 04)

| # | 문서 | 핵심 내용 |
|---:|---|---|
| 00 | [Overview](./00-overview.md) | 제품 요약, 범위, 비-목표, 핵심 용어 9개 |
| 01 | [Features](./01-features.md) | 포함 / 부분포함 / 제외 매트릭스 (10 카테고리) |
| 02 | [TRD](./02-trd.md) | 기술 요구사항, MCP version, error taxonomy, env 변수 |
| 03 | [Architecture](./03-architecture.md) | 시스템 아키텍처 + 8개 sequence diagram |
| 04 | [API Spec](./04-api-spec.md) | MCP Protocol API + REST `/v1/*` 완전 명세 |

### Data & Security (05 ~ 07)

| # | 문서 | 핵심 내용 |
|---:|---|---|
| 05 | [Database Schema](./05-database-schema.md) | SQLite/Postgres DDL, partial unique 5개, personal_access_tokens |
| 06 | [Security & Auth](./06-security-auth.md) | Token boundary, SSRF allowlist, CIMD, SVG XSS 방어 |
| 07 | [MCP Proxy Spec](./07-mcp-proxy-spec.md) | proxy mode, alias, 3-tier cache, error mapping |

### Frontend & Implementation (08 ~ 10)

| # | 문서 | 핵심 내용 |
|---:|---|---|
| 08 | [Frontend UX](./08-frontend-ux.md) | 본인용 admin Web UI (Next.js + shadcn/ui) |
| 09 | [Implementation Plan](./09-implementation-plan.md) | Phase P0~P3, 4~5주 1인 작업 |
| 10 | [Test Plan](./10-test-plan.md) | unit/integration/e2e/compatibility + fixture 13개 |

### Operations & Decisions (11 ~ 15)

| # | 문서 | 핵심 내용 |
|---:|---|---|
| 11 | [Risk Notes](./11-risk-notes.md) | R-101 ~ R-115 위험 + mitigation |
| 12 | [Operations](./12-operations.md) | launchd, backup, runbook 8개, SLO |
| 13 | [ADR](./13-adr.md) | **36개 아키텍처 의사결정 기록** |
| 14 | [MCP Client Profiles](./14-mcp-client-profiles.md) | Claude Code 우선, ChatGPT/Cursor 호환성 |
| 15 | [Future SaaS Migration](./15-future-saas-migration.md) | SaaS 전환 trigger + 절차 |

---

## 🚀 Quick Start

```bash
# 1. 디렉토리 + Admin token
mkdir -p ~/.coremcp/{data,logs,backups}
chmod 700 ~/.coremcp
python -c "import secrets; print('cmcp_admin_' + secrets.token_urlsafe(32))" \
  > ~/.coremcp/admin-token
chmod 600 ~/.coremcp/admin-token

# 2. Backend (Phase P0 완료 후)
cd apps/api && uv sync && uv run alembic upgrade head
uv run uvicorn coremcp.main:app --host 127.0.0.1 --port 8787

# 3. Claude Code 등록
claude mcp add --transport http coremcp http://localhost:8787/mcp \
  --header "Authorization: Bearer $(cat ~/.coremcp/admin-token)"
```

> Per-client token (Phase P1+)은 Web UI Settings → Tokens 또는 `POST /v1/settings/client-tokens`로 발급.

상세는 [09-implementation-plan.md §7](./09-implementation-plan.md) First Working Vertical Slice 참조.

---

## 🗺️ Phase Plan

```text
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Phase P0    │─▶│  Phase P1    │─▶│  Phase P2    │─▶│  Phase P3    │
│  Vertical    │  │  Real Service│  │  Web Admin   │  │  Daemon &    │
│  Slice       │  │  + Per-Client│  │  UI          │  │  OAuth       │
│              │  │  Token       │  │              │  │              │
│  1주         │  │  1.5주       │  │  1~2주       │  │  1주         │
└──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
```

| Phase | 기간 | 목표 | Exit Criteria |
|:---:|:---:|---|---|
| **P0** | 1주 | admin token → fake-mcp → Claude Code | invocation log 1줄, token boundary 검증 |
| **P1** | 1.5주 | per-client token + 실제 MCP + vault | Mac mini/MacBook 분리 동작, 한쪽 revoke 검증 |
| **P2** | 1~2주 | Web Admin UI 완성 | Settings/Tokens dual model UI |
| **P3** | 1주 | launchd + Tailscale + (옵션) OAuth/CIMD | 무인 운영 1주 안정 |

---

## 🎯 Design Principles

CoreMCP의 **11개 핵심 설계 원칙**:

1. CoreMCP는 사용자의 toolbox를 하나의 MCP server처럼 노출한다.
2. CoreMCP access token은 downstream으로 전달하지 않는다.
3. 모든 `/mcp` request는 bearer token을 검증한다.
4. Token은 dual 구조 — admin(파일) + per-client(DB hash). per-client는 revocable. ([ADR-030](./13-adr.md))
5. MCP Protocol은 2025-06-18 + 2025-11-25 양쪽 지원. ([ADR-029](./13-adr.md))
6. AUTH_MODE는 static_bearer 기본, oauth는 ChatGPT/Cursor 사용 시 옵션. ([ADR-032](./13-adr.md))
7. MCP session id는 인증 수단이 아니다.
8. downstream credential은 secret vault에 저장한다 — Keychain(default) 또는 Fernet(headless). ([ADR-031](./13-adr.md))
9. SSRF는 allowlist 기반 — Tailscale 등 명시 허용. ([ADR-033](./13-adr.md))
10. 단일 프로세스 우선, multi-process 확장 가능성은 열어둔다.
11. OAuth client 등록은 CIMD 우선, DCR fallback (AUTH_MODE=oauth 활성 시, [ADR-036](./13-adr.md)).

---

## 🔒 Security Highlights

CoreMCP는 단일 사용자 환경이지만 **SaaS급 보안 원칙**을 적용한다.

| 영역 | 정책 | 문서 |
|---|---|---|
| Token boundary | CoreMCP token이 downstream에 절대 전달 안 됨 | [06](./06-security-auth.md) §2.3 |
| Dual token revoke | per-connection 즉시 invalidate, CASCADE | [05](./05-database-schema.md) §9.3 |
| SSRF allowlist | 기본 사설망 전부 차단, env 변수로 명시 opt-in | [06](./06-security-auth.md) §7 |
| CIMD validation | client_id byte-exact, content-type charset, TTL fixed 1h | [06](./06-security-auth.md) §4.4.2 |
| SVG XSS 방어 | `<img>` only, CSP, default `ICON_SVG_ENABLED=false` | [05](./05-database-schema.md) / [07](./07-mcp-proxy-spec.md) / [08](./08-frontend-ux.md) |
| AUTH_MODE 분리 | static_bearer 시 metadata endpoint = 404 | [06](./06-security-auth.md) §4.0 |
| Credential vault | Keychain or Fernet, DB는 secret_ref만 | [06](./06-security-auth.md) §6 |
| Partial unique | soft-delete 호환 — 5개 테이블 적용 | [05](./05-database-schema.md) |

---

## 📊 Statistics

| 지표 | 값 |
|---:|:---|
| 총 라인 | ~8,000 |
| 문서 개수 | 17 |
| ADR 개수 | 36 |
| Sequence Diagram | 8 |
| 환경 변수 | 35+ |
| Test Fixture | 13 |
| Risk 항목 | 15 (R-101~R-115) |
| Phase | 4 (P0~P3) |

---

## 📖 External References

- [MCP Spec 2025-11-25 (latest)](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP Spec 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18)
- [MCP Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [PlayMCP (벤치마크)](https://playmcp.kakao.com)

### OAuth / Auth RFCs (Phase P3+ 시)
- [RFC 7591 — DCR](https://datatracker.ietf.org/doc/html/rfc7591) · [RFC 7636 — PKCE](https://datatracker.ietf.org/doc/html/rfc7636) · [RFC 8707 — Resource Indicators](https://datatracker.ietf.org/doc/html/rfc8707) · [RFC 9728 — OAuth PRM](https://datatracker.ietf.org/doc/html/rfc9728) · [RFC 8785 — JSON Canonicalization](https://datatracker.ietf.org/doc/html/rfc8785)

---

<div align="center">

**v1.0 · 2026-05-11**

[메인 README ↑](../README.md) · [Implementation Plan →](./09-implementation-plan.md) · [ADR 목록 →](./13-adr.md)

</div>
