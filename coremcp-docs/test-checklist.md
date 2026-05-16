# Web Admin + Demo MCP Suite Test Checklist

> 본 문서는 [Implementation Plan](./09-implementation-plan.md) 과 [Test Plan](./10-test-plan.md) 의 보조 manual 검증 매트릭스다. 자동 회귀 (pytest 164 + demo 21 + fake 12) 와 별개로 Web Admin UI · API · 8 demo MCP 통합 흐름을 사람이 손수 확인하기 위한 체크리스트.

## 0. 사전 조건

| 항목 | 명령/확인 |
|---|---|
| API/Web/fake/demo listen | `lsof -nP -iTCP -sTCP:LISTEN \| grep -E ":(8787\|8790\|8791\|3003) "` |
| 8 demo service 등록 + active | `curl -s -H "Authorization: Bearer $(cat ~/.coremcp/admin-token)" http://127.0.0.1:8787/v1/mcp-services` |
| 자동 회귀 통과 | `cd apps/api && uv run pytest -q` |
| 브라우저 sessionStorage 초기화 | DevTools → Application → Clear storage |
| admin token | `cat ~/.coremcp/admin-token` 복사 |

**Destructive 표기**: preset 적용 · credential 변경 · service delete 같은 변경 케이스는 `★ DESTRUCTIVE` 로 표시. 검증 후 원복 단계 포함.

---

## 1. Dashboard (`/`)

| ID | 시나리오 | 액션 | 기대 |
|---|---|---|---|
| D-01 | token 미입력 first-load | sessionStorage 비운 상태에서 `/` 접속 | 사이드바 `token 필요` 주황 배지 + `Admin token 저장 후 데이터를 불러오세요` + 카드 모두 0 |
| D-02 | token 입력 후 fetch | 사이드바 하단 Admin token 저장 | 배지 `auth ok` 초록, banner `최신 데이터를 불러왔습니다`, Default Toolbox 8 / MCP Services 8/8 / Client Tokens ≥1 / Recent Tool Calls ≥0 |
| D-03 | 새로고침 | 헤더 `새로고침` | banner 잠시 불러오는 중 → 갱신 |
| D-04 | Health 버튼 | 사이드바 `Health` | `API 상태: ok` |
| D-05 | theme toggle | dark/light 변경 | 즉시 색 변경, 가독성 유지 |
| D-06 | 토큰 삭제 | `삭제` | 0/0 + sessionStorage clear |
| D-07 | 잘못된 토큰 | `cmcp_admin_invalid` 저장 | 401 자동 감지 → clear |

## 2. Services (`/services`)

| ID | 시나리오 | 액션 | 기대 |
|---|---|---|---|
| S-01 | 8 demo 표시 | 진입 | 8개 row, 전부 active, tools 4~6 |
| S-02 | Validate 단건 | `Validate` 클릭 | success + last_validated 갱신 |
| S-03 | 신규 등록 ★ DESTRUCTIVE | name=`Smoke Test`, slug=`smoke_test`, url=`http://127.0.0.1:8791/personal-ops/mcp` → `등록` | row 추가 → Validate 후 active. 원복: Detail → 삭제 |
| S-04 | SSRF 거부 | url=`http://169.254.169.254/mcp` | 422 + SSRF 차단 |
| S-05 | 동일 slug | demo_ops slug 재등록 | 409 |
| S-06 | 도구함 추가 | `도구함 추가` | banner 성공 |
| S-07 | Detail 진입 | service명 클릭 | `/services/{id}` 메타 표시 |
| S-08 | 검색/필터 | 검색창 활용 | 결과 필터링 |

## 3. Service Detail (`/services/[id]`)

| ID | 시나리오 | 액션 | 기대 |
|---|---|---|---|
| SD-01 | 메타데이터 | demo_home_lab 진입 | endpoint, transport=http, auth=none, validation_summary stages 5개 |
| SD-02 | Tools 탭 | 클릭 | 5개 tool 행 |
| SD-03 | tool override ★ DESTRUCTIVE | service_restart → `hidden` | tools/list 에서 사라짐. 원복: `callable` |
| SD-04 | preset apply ★ DESTRUCTIVE | `dangerous_off` | destructiveHint hidden, banner counts. 원복: `full_access` |
| SD-05 | preset readonly | `readonly` | readOnlyHint=true 만 callable |
| SD-06 | Resources 탭 | demo_knowledge → Resources | 0~N개 |
| SD-07 | Credential 등록 ★ DESTRUCTIVE | bearer_token service secret 입력 | status=connected, masked |
| SD-08 | Credential rotate ★ | rotate | masked 변경 |
| SD-09 | Credential delete | 삭제 | not_connected |
| SD-10 | Validate 재실행 | `Validate` | success, schema_diff |

## 4. Toolbox (`/toolbox`)

| ID | 시나리오 | 액션 | 기대 |
|---|---|---|---|
| T-01 | 8 service 표시 | 진입 | 8개 카드, enabled, tools 4~6 |
| T-02 | service-level toggle ★ | `Disable service` | playground 에서 해당 service 도구 사라짐. 원복: Enable |
| T-03 | per-tool badges | 카드 핀 | default callable 4~6, 나머지 0 |
| T-04 | Remove ★ DESTRUCTIVE | smoke_test Remove | 카드 제거 |

## 5. Playground (`/playground`)

| ID | 시나리오 | 액션 | 기대 |
|---|---|---|---|
| P-01 | tools 목록 fetch | `도구 목록 불러오기` | dropdown 40개 |
| P-02 | `demo_ops.ops_status` | args=`{}` | content[0].text 응답, isError=false |
| P-03 | `demo_ops.ops_checklist` | args=`{}` | 3 item |
| P-04 | `demo_ops.incident_list` | args=`{}` | 2 incident |
| P-05 | `demo_knowledge.note_search` | args=`{"query":"ops"}` | 매칭 |
| P-06 | `demo_knowledge.note_get` | args=`{"note_id":"kv_note_001"}` | fetch |
| P-07 | `demo_tasks.task_list` | args=`{}` | 3 task |
| P-08 | `demo_bookmarks.bookmark_search` | args=`{"query":"demo"}` | 매칭 |
| P-09 | `demo_design.color_tokens` | args=`{}` | 7 token |
| P-10 | `demo_design.asset_search` | args=`{"query":"demo"}` | 3 asset |
| P-11 | `demo_finance.ledger_summary` | args=`{}` | ledger |
| P-12 | `demo_finance.transaction_search` | args=`{}` | 5 transaction |
| P-13 | `demo_home_lab.device_list` | args=`{}` | 3 device |
| P-14 | `demo_travel.itinerary_list` | args=`{}` | 2 itinerary |
| P-15 | `demo_travel.place_search` | args=`{"query":"demo"}` | 7 place |
| P-16 | 잘못된 args | invalid field | jsonschema error 또는 ignore |
| P-17 | 미존재 도구 | `demo_ghost.foo` | -32602 |
| P-18 | hidden tool 호출 | SD-03 후 service_restart | policy_denied + audit |
| P-19 | write tool ★ | `bookmark_create` | demo는 fixture-only, 정상 응답 |
| P-20 | 큰 응답 | size cap 미감지 | 1MB 미만 |

## 6. Connected Clients (`/clients`)

| ID | 시나리오 | 액션 | 기대 |
|---|---|---|---|
| C-01 | 기존 목록 | 진입 | Codex CLI exec 등 |
| C-02 | 신규 등록+Token ★ | `등록+Token` | issued_token 1회만 amber 박스 |
| C-03 | One-time Token ★ | `One-time Token` | prompt + token, expires 10분 |
| C-04 | revoke ★ | `Revoke` | Dashboard count -1 |
| C-05 | revoked 호출 | curl | 401 |
| C-06 | One-time exchange | `POST /v1/external-connections/exchange` | 201, 두 번째 401 |

## 7. Settings / Tokens (`/settings`)

| ID | 시나리오 | 액션 | 기대 |
|---|---|---|---|
| ST-01 | masked | 진입 | `cmcp_admin_••••` |
| ST-02 | client tokens 리스트 | 표시 | prefix + scopes |
| ST-03 | revoke | `Revoke` | 동일 |
| ST-04 | env meta | 표시 | auth_mode, secret_backend, app_version |

## 8. Logs (`/logs`)

| ID | 시나리오 | 액션 | 기대 |
|---|---|---|---|
| L-01 | invocations | 진입 | 10건 |
| L-02 | audit | 탭 | service.create / validate 등 |
| L-03 | policy.deny | P-18 후 | audit row 추가 |
| L-04 | ssrf.block | S-04 후 | audit row |
| L-05 | request_id | 검색 | cross-link |

## 9. 통합 E2E 시나리오

### E2E-A. 신규 service onboarding
S-03 → S-02 → SD-02 → S-06 → P-01 → P-02 → L-01 → SD-03 → P-18 → 원복

### E2E-B. Preset 라이프사이클
demo_home_lab → SD-04 `dangerous_off` → /toolbox hidden count +1 → P-18 → `full_access` 원복

### E2E-C. Token 라이프사이클
C-02 → curl tools/list 성공 → ST-03 revoke → curl 401 → L-02 audit

### E2E-D. 자동 health probe 회복
SSRF allowlist 임시 제거 → API 재시작 → 1분 후 services error → SSRF 원복 → API 재시작 → 1분 후 자동 active 회복 (수동 validate 없이)

## 10. 비기능 / 회귀 / 안전성

| ID | 시나리오 | 검증 |
|---|---|---|
| NF-01 | 응답 latency | Playground 평균 <50ms |
| NF-02 | CORS preflight | OPTIONS 200 + `access-control-allow-origin: http://localhost:3003` |
| NF-03 | SSE listChanged | Toolbox 토글 후 /playground 자동 갱신 |
| NF-04 | request body 1MB cap | curl 1.5MB → 413 |
| NF-05 | rate limit | 120 req/min 초과 → 429 |
| NF-06 | redaction | audit metadata에 token 미노출 |
| NF-07 | 모바일 viewport | 사이드바 → 상단 nav 칩 |
| NF-08 | dark/light 가독성 | 모든 페이지 두 테마 OK |
| NF-09 | favicon | CoreMCP 아이콘 |
| NF-10 | unauthorized 자동 처리 | API 종료 후 `coremcp:unauthorized` 이벤트 |

## 11. 실행 권장 순서 (≈45분)

1. 사전 조건 (5분)
2. Dashboard D-01~07 (3분)
3. Services S-01~08 (5분)
4. Service Detail SD-01~10 (10분)
5. Toolbox T-01~04 (3분)
6. Playground P-01~20 (10분)
7. Clients C-01~06 (4분)
8. Settings ST-01~04 (2분)
9. Logs L-01~05 (3분)
10. 통합 E2E 선택 (10분)
11. 비기능 NF-01~10 (10분)

## 12. 자동화 매핑

| 영역 | 자동화 |
|---|---|
| 사전 조건 1~3 | `make ui-smoke` |
| P-02~P-15 | `/tmp/coremcp_ui_test/playground_self_test.py` |
| E2E-A onboarding | Playwright (현재 ui-smoke 보강 후보) |
| E2E-D auto recovery | `tests/test_health_probe_recovery.py` (164 passed) |

## 13. 시간 제약 시 최소 검증 (P0 5건)

1. **D-02, D-07** — 인증 흐름
2. **S-01, S-02** — 8 demo active
3. **P-01~02** — playground sanity
4. **E2E-D** — health probe 자동 회복 (직전 fix 회귀)
5. **NF-02** — CORS preflight
