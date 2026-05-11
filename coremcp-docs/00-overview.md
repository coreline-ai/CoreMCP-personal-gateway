# CoreMCP Overview

문서 버전: v1.0
작성일: 2026-05-11
대상 사용자: 본인

---

## 1. 제품 요약

CoreMCP는 본인이 Mac mini에서 운영하는 protected MCP gateway다. 외부 AI 클라이언트(Claude Code 우선, 필요 시 ChatGPT/Cursor)는 `/mcp` endpoint 하나만 등록하면 사용자의 도구함에 담긴 모든 downstream MCP tool을 사용할 수 있다.

본인이 직접 만든 MCP 서버, GitHub/Notion/Linear 같은 외부 MCP 서버 등을 하나의 진입점으로 모은다.

## 2. 왜 이걸 만드는가

문제:
- AI 클라이언트마다 MCP 서버를 개별 등록해야 한다.
- MCP 서버마다 토큰/API key 관리가 분산된다.
- 여러 머신(Mac mini, MacBook)에서 동일한 도구함을 쓰고 싶다.
- downstream MCP의 schema 변경을 추적하고 싶다.

해결:
- Mac mini에 CoreMCP 1개 운영.
- 클라이언트는 CoreMCP만 안다.
- credential은 한 곳에서 관리.
- audit/invocation log 일원화.

## 3. 범위

### 3.1 적용 환경
- 사용자: 본인 1명
- 호스트: Mac mini 24/7 가동
- 외부 노출: 옵션 (Tailscale 권장)
- 언어: 한국어 (영문 기술용어 그대로 사용)

### 3.2 우선 클라이언트
- Claude Code (Mac mini 로컬 + Tailscale 통한 MacBook)
- 추후: Claude desktop, ChatGPT custom MCP, Cursor, OpenClaw (14-mcp-client-profiles.md)

모든 client는 정적 bearer로 시작. 동일 사용자가 여러 client(Mac mini Claude Code, MacBook Claude Code)를 운영 시 client별 token으로 분리해 revoke 가능 (ADR-030).

### 3.3 우선 downstream MCP 후보
- 본인이 만든 MCP 서버 (로컬 Python/Node)
- GitHub MCP (PAT)
- Notion MCP
- Linear MCP
- 임의 remote MCP

## 4. 비-목표 (개인 프로젝트라 명시적으로 빠지는 것)

- **다인 사용 / 팀 워크스페이스 / RBAC**: 본인 외 사용자 없음
- **공개 marketplace**: 외부에 노출 안 함
- **Pricing / Billing / Stripe**: 본인 사용
- **GDPR / 개인정보보호법 / SOC2 / ISO27001**: 외부 사용자 데이터 없음
- **Multi-region / DR / Cross-region backup**: 단일 호스트
- **MFA / Email verify / Password reset / Sign-up flow**: 가입자 없음
- **Account takeover defense / Bug bounty / Status page**: 공개 서비스 아님
- **Right-to-erasure API**: 본인이 직접 rm

상세는 `01-features.md`.

## 5. 가치 정의

한 줄:
> 내 Mac mini에서 모든 MCP를 한 곳에 모아 어디서든 쓴다.

## 6. 성공 기준

본인 기준:
- Mac mini의 Claude Code가 CoreMCP를 통해 GitHub/Notion 등 실제 MCP를 사용한다.
- MacBook의 Claude Code가 Tailscale로 동일 도구함을 쓴다.
- downstream MCP의 schema 변경이 자동 감지된다.
- credential은 평문으로 어디에도 저장되지 않는다.
- Mac mini 재부팅 후 자동 복귀한다.
- 본인 외에는 어떤 client도 접근 불가하다.
- Mac mini Claude Code와 MacBook Claude Code가 각각 별도 client token으로 동작하고, 한쪽 revoke 시 다른 쪽은 영향 없다.
- MCP 2025-11-25 client 요청에도 정상 응답한다.

## 7. 핵심 용어

| 용어 | 의미 |
|---|---|
| MCP Service | 등록된 downstream MCP 서버 1개 |
| Service Tool | MCP Service가 제공하는 tool 1개의 schema 캐시 |
| Toolbox | 본인이 외부 AI에 노출할 MCP Service 모음 (보통 1개 default) |
| Toolbox Item | toolbox와 service의 연결 |
| Tool Alias | exposed tool name(예: `github.create_issue`) ↔ downstream tool name(`create_issue`) 매핑 |
| External Connection | Claude Code, ChatGPT 등 외부 client 등록 |
| Tool Invocation | 1회의 tools/call 실행 기록 |
| Downstream | CoreMCP가 proxy하는 MCP 서버 |
| Exposed | CoreMCP가 외부 AI에 노출하는 tool 이름 |
| Admin Token | `cmcp_admin_*` root 권한 token, 파일 보관 |
| Client Token | `cmcp_client_*` per-connection token, DB hash 비교 |
| AUTH_MODE | 인증 모드 (static_bearer 기본 / oauth 옵션) |
| SECRET_BACKEND | credential 저장 모드 (keychain / fernet) |

## 8. 문서 우선순위

본 문서팩이 충돌 시 정본. `production_docs_donotuse/`는 미래 SaaS 확장 시 참고용.
