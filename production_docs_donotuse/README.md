# CoreMCP Development Documentation Pack

작성일: 2026-05-11  
버전: v0.1  
제품 정의: MCP Toolbox + Authenticated MCP Gateway SaaS

---

## 현재 상태

- 현재 phase: **Phase 0 — Protocol Spike** (목표: `/mcp` minimal 동작)
- 문서 버전: v0.1
- 최근 갱신: 2026-05-11

---

## 문서 목록

| 파일 | 목적 |
|---|---|
| `00-executive-review.md` | 최종 판단, PlayMCP 벤치마크, 제품 방향 |
| `01-prd.md` | 제품 요구사항 문서 |
| `02-trd.md` | 기술 요구사항 문서 |
| `03-architecture.md` | 시스템 아키텍처와 컴포넌트 설계 |
| `04-api-spec.md` | MCP API와 REST API 명세 |
| `05-database-schema.md` | PostgreSQL 스키마/DDL |
| `06-security-auth.md` | 인증, OAuth, token boundary, 보안 설계 |
| `07-mcp-proxy-spec.md` | MCP proxy mode, tool alias, tools/list/call 처리 |
| `08-frontend-ux.md` | 화면 구조, UX 플로우, 카피 |
| `09-implementation-plan.md` | 개발 마일스톤, task breakdown, DoD |
| `10-qa-test-plan.md` | QA, 보안 테스트, E2E 테스트 계획 |
| `11-risk-review.md` | 상세 리스크 리뷰와 대응책 |
| `12-operations-observability.md` | 운영, 로그, 모니터링, runbook |
| `13-adr.md` | 아키텍처 의사결정 기록 |
| `14-pricing.md` | Pricing tier, quota matrix, billing |
| `16-compliance.md` | GDPR / 개인정보보호법 / data residency |
| `17-mcp-client-profiles.md` | Claude Code / Claude / ChatGPT / OpenClaw / Cursor 호환성 매트릭스 |

---

## 최종 제품 정의

CoreMCP는 사용자가 여러 MCP 서버를 등록하거나 선택해 개인/팀 도구함에 담고, Claude Code, Claude, ChatGPT, OpenClaw 같은 외부 AI 클라이언트에는 CoreMCP 하나만 연결해 도구함 전체를 인증 기반으로 사용할 수 있게 해주는 MCP Gateway SaaS다.

---

## Phase ↔ Milestone ↔ MVP 매핑

| Phase (00) | Milestone (09) | MVP 단계 |
|---|---|---|
| Phase 0 Protocol Spike | M0 Bootstrap + M5 Gateway minimal (fake) | 1. Project bootstrap |
| Phase 1 Private Toolbox MVP | M1 Auth/Toolbox + M2 Service Registry + M3 Validation Worker + M4 Toolbox | 2~5 |
| Phase 1 (cont.) | M5 Gateway + M6 OAuth + M7 Proxy | 6~8 |
| Phase 1 (cont.) | M8 Claude Code integration + M9 OTT + M10 Hardening | 9~11 |
| Phase 2 Developer Console | Phase 1 GA 이후 | playground, schema refresh, tool alias 수동 |
| Phase 3 External Client Expansion | Phase 2 GA 이후 | Claude/ChatGPT/OpenClaw 확장, delegated OAuth |
| Phase 4 Public Marketplace | Phase 3 GA 이후 | review queue, verified badge |
| Phase 5 Team/Enterprise | Phase 4 GA 이후 | workspace RBAC, BYOK, SSO |

MVP 11단계 ([00 §5.2](00-executive-review.md) MVP 필수 범위와 동일)는 Phase 0~1 내에서 완료한다.

---

## 가장 중요한 개발 원칙

1. CoreMCP는 사용자의 toolbox를 하나의 MCP server처럼 노출한다.
2. CoreMCP access token은 downstream으로 전달하지 않는다.
3. 모든 `/mcp` request는 bearer token을 검증한다.
4. MCP session id는 인증 수단이 아니다.
5. downstream credential은 secret vault에 저장한다.
6. public marketplace는 MVP 이후로 미룬다.
7. MVP는 Claude Code end-to-end 성공을 최우선으로 한다.

---

## 빠른 시작용 개발 목표

첫 vertical slice:

```text
hardcoded user
 -> hardcoded toolbox
 -> registered fake no-auth downstream MCP
 -> /mcp initialize
 -> /mcp tools/list
 -> /mcp tools/call proxy
```

이후 auth, DB, UI, credential vault를 붙인다.

---

## 참고 공개 자료

- PlayMCP: https://playmcp.kakao.com
- Kakao PlayMCP beta/open platform: https://www.kakaocorp.com/page/detail/11674
- Kakao PlayMCP OpenClaw connection: https://www.kakaocorp.com/page/detail/12012
- MCP Authorization: https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
- MCP Transports: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- MCP Security Best Practices: https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices
- Claude Code MCP docs: https://code.claude.com/docs/en/mcp
- RFC 7591 Dynamic Client Registration: https://datatracker.ietf.org/doc/html/rfc7591
- RFC 7636 PKCE: https://datatracker.ietf.org/doc/html/rfc7636
- RFC 7662 Token Introspection: https://datatracker.ietf.org/doc/html/rfc7662
- RFC 7009 Token Revocation: https://datatracker.ietf.org/doc/html/rfc7009
- RFC 8707 Resource Indicators: https://datatracker.ietf.org/doc/html/rfc8707
- RFC 9728 OAuth Protected Resource Metadata: https://datatracker.ietf.org/doc/html/rfc9728
- RFC 8785 JSON Canonicalization Scheme: https://datatracker.ietf.org/doc/html/rfc8785
- OAuth 2.1 draft: https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1
