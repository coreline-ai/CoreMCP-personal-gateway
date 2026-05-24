# CoreMCP Project Goal

Last updated: 2026-05-24

## 한 줄 목표

CoreMCP는 개인 운영자가 여러 MCP 서버를 하나의 안전한 `/mcp` endpoint와 Web Admin으로 통합해, Codex CLI·Claude Code·ChatGPT 등 연결된 AI client가 동일한 개인 도구함을 사용할 수 있게 하는 **개인용 MCP 도구함·인증 게이트웨이**다.

## 핵심 가치

- AI client마다 MCP를 반복 등록하지 않고 CoreMCP 하나만 연결한다.
- downstream MCP credential과 CoreMCP admin/client token 경계를 분리한다.
- SSRF guard, credential vault, rate limit, circuit breaker, audit log, launchd 운영 스크립트로 개인 Mac mini 운영을 안전하게 만든다.
- Project Docs MCP, Git Workspace MCP처럼 실제 개인 작업에 바로 쓰는 read-only MCP를 우선한다.
- Web Admin과 simulator는 “무엇을 연결했고 어떤 도구가 어떻게 호출되는지”를 사용자가 이해하도록 돕는다.

## 독립 인증 모듈 범위

Coreline Auth는 CoreMCP에 종속되지 않는 별도 모듈이며, 현재는 독립 저장소 `coreline-ai/coreline-auth-module`에서 관리한다. 로컬에 `packages/coreline-auth/` 폴더가 있을 수 있지만 CoreMCP 저장소에서는 `.gitignore`로 제외한다.

- 이메일/비밀번호, social/OIDC 기반 로그인, RBAC, admin mode, audit, demo SaaS webapp은 Coreline Auth 저장소에서 자체 검증한다.
- CoreMCP에 적용할 때는 최소 통합 어댑터만 둔다.
- CoreMCP 본체가 인증 SaaS 제품으로 변질되지 않도록 저장소·문서·테스트를 분리한다.

## 반드시 지킬 안전 불변식

- CoreMCP admin/client token을 downstream으로 전달하지 않는다.
- `Mcp-Session-Id`를 인증으로 취급하지 않는다.
- `/mcp` 요청마다 bearer auth를 재검증한다.
- downstream credential은 vault abstraction을 통해서만 저장한다.
- debug trace가 명시적으로 켜지지 않으면 raw tool arguments/results를 저장하지 않는다.
- `AUTH_MODE=static_bearer`를 기본값으로 유지한다.
- tool icon은 `src` 기반 `<img>`로만 렌더링하고 inline SVG는 금지한다.

## 명시적 비범위

별도 dev-plan/ADR 없이 아래 항목을 구현하지 않는다.

- SaaS/team/workspace/marketplace/billing
- 외부 LLM API 내장형 챗봇 서비스화
- 멀티 테넌트 조직 관리
- 공개 plugin marketplace
- CoreMCP 본체에 인증 SaaS 기능 흡수

## 성공 기준

- `make test`가 통과한다.
- Web Admin smoke가 통과한다: `make ui-smoke`, 필요 시 `make ui-smoke-p0`.
- 실제 사용 MCP 최소 2개(Project Docs, Git Workspace)가 등록·검증·호출 가능하다.
- Codex CLI exec 또는 simulator에서 CoreMCP 도구 호출 흐름을 설명·시연할 수 있다.
- 외부 운영 검증은 별도 환경에서 `make external-env-validate`, `make mobile-qa-checklist`, `make soak-check`로 수행한다.

## Codex CLI `/goal` 사용 방식

Codex CLI 0.129.0 기준 `/goal`은 custom command가 아니라 Codex 내장 Goal mode다. 현재 로컬 `~/.codex/config.toml`에는 다음 설정을 적용했다.

```toml
[features]
goals = true
```

Codex TUI에서 아래처럼 사용한다.

```text
/goal <목표>
/goal
/goal pause
/goal resume
/goal clear
```

CoreMCP에서 권장하는 목표 예시:

```text
/goal CoreMCP를 개인 MCP gateway 범위로 유지하면서 Project Docs MCP와 Git Workspace MCP의 실사용 흐름을 안정화해줘. 완료 기준: 두 MCP 등록/검증/호출 성공, Web Admin simulator에서 추천 프롬프트 실행, 관련 smoke/test 통과. 비범위: SaaS/team/billing 기능 추가, Coreline Auth 기능을 CoreMCP 본체로 흡수.
```
