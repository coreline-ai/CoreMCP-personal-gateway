# implement_20260516_security_hardening.md

작성 일시: `2026-05-16 KST`

이 문서는 CoreMCP 보안 안정화 병렬 작업의 문서 소유 범위를 고정한다. 코드 파일은 병렬 워커가 소유하며, 이 문서 작업은 보안 리뷰 결과와 다음 작업 경계를 정리하는 데 한정한다.

## 개발 목적

- `security_best_practices_report.md`의 S-01~S-07 결과를 현재 hardening batch와 다음 batch로 분류한다.
- 이번 배치에서 S-02 `TrustedHostMiddleware`와 S-07 value-based redaction이 코드 패치 완료됐음을 문서화한다.
- S-01/S-04/S-05/S-06은 범위 확장 없이 다음 작업으로 남긴다.
- 개인 CoreMCP gateway 범위를 유지하고 SaaS/team/marketplace 기능으로 확장하지 않는다.

## 소유 범위

| 경로 | 작업 |
|---|---|
| `security_best_practices_report.md` | 보안 리뷰 상태/다음 작업 보강 |
| `coremcp-docs/06-security-auth.md` | S-02/S-07 정책과 2026-05-16 hardening 상태 추가 |
| `dev-plan/implement_20260516_security_hardening.md` | 이번 문서 작업 계획/완료 상태 기록 |

## 제외 범위

- `apps/api` 코드 수정 금지.
- `apps/web` 코드 수정 금지.
- TrustedHost/redaction 구현 자체는 병렬 코드 워커 소유.
- OAuth consent UI, STDIO argv profile, remote icon proxy, allowlist DNS pinning 구현 금지.
- SaaS/team/workspace/marketplace/publisher/billing 기능 추가 금지.

## 보안 불변식

- CoreMCP admin/client token은 downstream MCP로 전달하지 않는다.
- `Mcp-Session-Id`는 인증 수단이 아니다.
- `/mcp`는 bearer auth를 매 request 재검증한다.
- downstream credential은 vault abstraction으로만 저장한다.
- raw tool arguments/results는 debug trace opt-in 없이 저장하지 않는다.
- `AUTH_MODE=static_bearer` default를 유지한다.
- tool icon은 `src` + `<img>` 렌더링만 허용하고 inline SVG는 금지한다.

## Phase 상태 요약

- [x] Phase 1 — 기존 보안 리뷰 문서 확인
- [x] Phase 2 — S-02/S-07 current batch 상태 반영
- [x] Phase 3 — S-01/S-04/S-05/S-06 next work 정리
- [x] Phase 4 — 보안 auth 문서에 hardening 상태 동기화
- [x] Phase 5 — markdown/diff 검증

## Phase 1. 기존 보안 리뷰 문서 확인

### 작업
- [x] `security_best_practices_report.md` 존재 확인.
- [x] 기존 S-01~S-07 findings 구조 확인.
- [x] 개인 gateway scope와 SaaS 금지 조건 확인.

### 결과
- [x] 기존 보고서를 보강 대상으로 유지했다.

## Phase 2. 이번 배치 항목 정리

### 작업
- [x] S-02 `TrustedHostMiddleware`를 current batch patched 상태로 표시.
- [x] S-07 value-based redaction을 current batch patched 상태로 표시.
- [x] 두 항목 모두 코드 워커 구현 완료 후 문서 상태를 완료로 갱신.
- [x] 완료 기준과 검증 목표를 문서화.

### 결과
- [x] 보고서 executive summary와 각 finding에 patched status가 추가되었다.
- [x] `coremcp-docs/06-security-auth.md`에 S-02/S-07 정책이 추가되었다.

## Phase 3. 다음 작업 범위 정리

### 작업
- [x] S-01 OAuth consent / allowlist policy를 다음 작업으로 유지.
- [x] S-04 STDIO argv profile을 다음 작업으로 유지.
- [x] S-05 remote icon proxy / opt-in을 다음 작업으로 유지.
- [x] S-06 allowlist DNS pinning을 다음 작업으로 유지.
- [x] team/workspace/marketplace/billing 확장 금지를 명시.

### 결과
- [x] next batch 항목이 구현 범위가 아닌 정책/후속 작업으로 정리되었다.

## Phase 4. 보안 auth 문서 동기화

### 작업
- [x] Logging Rules 아래 value-based redaction 정책 추가.
- [x] CORS/Origin 섹션에 Trusted Host / Host Header 정책 추가.
- [x] 2026-05-16 Security Hardening Review Status 섹션 추가.

### 결과
- [x] `coremcp-docs/06-security-auth.md`가 보고서 상태와 동기화되었다.

## Phase 5. 검증

### 작업
- [x] markdown 표/heading의 큰 문법 오류가 없는지 육안 검토.
- [x] `git diff --check` 실행.

### 결과
- [x] `git diff --check` 통과.

## 최종 결과 요약

- S-02와 S-07은 이번 보안 안정화 배치의 코드 패치 완료/검증 항목으로 문서화했다.
- S-01, S-04, S-05, S-06은 다음 작업으로 남겼고 범위를 확장하지 않았다.
- 모든 문서 변경은 개인 CoreMCP gateway scope 안에 제한했다.
