# implement_20260516_security_next_batch.md

작성 일시: `2026-05-16 KST`

## 목적

직전 보안 리뷰에서 다음 작업으로 남긴 S-01/S-04/S-05/S-06을 개인 CoreMCP gateway 범위 안에서 안정화한다. 이번 배치는 기능 확장이 아니라 외부 노출/운영 시 안전 기본값과 명시적 정책 hook을 보강하는 데 한정한다.

## 범위

| ID | 항목 | 방향 |
|---|---|---|
| S-01 | OAuth consent / client allow policy | OAuth consent UI 대신 DCR enable toggle 및 client allowlist policy hook |
| S-04 | STDIO argv profile | basename allowlist 위에 위험 argv profile deny layer 추가 |
| S-05 | Remote icon privacy | remote HTTPS icon을 명시 opt-in으로 제한하고 data/self icon 중심 유지 |
| S-06 | SSRF allowlist DNS pinning | allowlisted host도 resolve/pin 및 before/after DNS 변경 감지 |

## 제외 범위

- OAuth consent UI, multi-user approval, SaaS/team/workspace 기능.
- 외부 plugin loading 또는 built-in plugin 확장.
- stdio sandbox/container 격리.
- Web Admin 인증 모델 교체.

## 불변식

- CoreMCP admin/client token은 downstream MCP로 전달하지 않는다.
- `Mcp-Session-Id`는 인증 수단으로 사용하지 않는다.
- `/mcp`는 bearer auth를 매 request 재검증한다.
- downstream credential은 vault abstraction으로만 저장한다.
- raw tool arguments/results는 debug trace opt-in 없이 저장하지 않는다.
- `AUTH_MODE=static_bearer` default를 유지한다.
- tool icon은 `src` + `<img>` 렌더링만 허용하고 inline SVG는 금지한다.

## 병렬 작업 분할

### Workstream A — STDIO argv profile

- [x] `proxy/stdio.py`에 위험 argv deny profile 추가.
- [x] 허용/거부 회귀 테스트 추가.

### Workstream B — SSRF allowlist DNS pinning

- [x] allowlisted host도 resolve 결과를 보존.
- [x] metadata IP 차단과 before/after DNS 변경 감지 테스트 추가.

### Workstream C — OAuth policy hook

- [x] DCR enable toggle 또는 client allowlist hook 추가.
- [x] OAuthError/no-store 응답 관례 유지.
- [x] 정책 회귀 테스트 추가.

### Workstream D — Remote icon privacy + docs

- [x] remote HTTPS icon opt-in 설정 추가.
- [x] `normalize_icons()` 경고/차단 동작 테스트 추가.
- [x] 보안 리뷰/보안 문서에 완료 상태 반영.

## 검증 계획

- [x] `cd apps/api && uv run pytest -q` 통과 — `199 passed, 108 warnings`.
- [x] `cd apps/fake-mcp && uv run pytest -q` 통과 — `12 passed`.
- [x] `make test` 통과 — API 199 + fake-mcp 12 + demo-mcp-suite 21 passed.
- [x] `make lint` 통과.
- [x] `pnpm build` 통과.
- [x] `git diff --check` 통과.

## 완료 기준

- S-01/S-04/S-05/S-06이 코드/테스트/문서 중 하나 이상으로 명시적으로 닫힌다.
- 기존 static bearer/local personal gateway flow는 깨지지 않는다.
- 새 설정은 default-safe 또는 backward-compatible 중 하나를 명확히 문서화한다.

## 운영 검증 중 발견 및 보정

- [x] `make ui-smoke` 1차 실행에서 Web Admin이 빠른 route prefetch/API fetch 중 `/v1` admin rate limit에 도달해 Logs 검증이 실패했다.
- [x] `COREMCP_AUTH_RATE_LIMIT_PER_MINUTE` 기본값을 `240`으로 상향해 실제 Web Admin 사용/route smoke에 맞췄다.
- [x] rate-limit 429 응답에 allowed origin CORS header를 붙여 browser가 `ERR_FAILED` 대신 정상 HTTP error로 처리할 수 있게 했다.
- [x] Web Admin 기본 API base를 `http://127.0.0.1:8787`로 변경해 `localhost` IPv6/IPv4 해석 차이를 줄였다.
- [x] `ui-smoke-p0` oversize body smoke가 streaming limiter의 early close를 정상 body-cap rejection으로 기록하도록 보정했다.

## 운영 검증 결과

- [x] launchd restart + `ops-smoke --post-reboot` 통과.
- [x] `external-env-validate --post-reboot` 통과 — Tailscale CLI/external URL은 환경 미설정으로 skip.
- [x] `make ui-smoke` 통과.
- [x] `make ui-smoke-p0` 통과 — `23 PASS / 0 FAIL / 0 SKIP`.
- [x] `make route-smoke` 통과.
- [x] `make soak-check` 통과 — 300초, 10 checks, 0 failures.
- [x] `make codex-smoke` 통과 — Codex client token으로 initialize/tools-list 40 tools.
- [x] `make codex-exec` 통과 — Codex CLI exec가 CoreMCP 도구 요약 응답.
- [x] `make cli-backup-export && make cli-backup-import-dry-run` 통과.
