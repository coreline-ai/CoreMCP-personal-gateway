# Demo MCP Server Ideas

작성 일시: `2026-05-14 KST`

이 폴더는 CoreMCP personal gateway에 연결해 데모하기 좋은 **가상의 MCP 서버 후보**를 고르기 위한 아이디어 보드다.

## 목적

- 실제 외부 서비스 credential 없이 CoreMCP의 연결/검증/도구함/Tool Control/Playground 흐름을 보여준다.
- 외부 LLM API 의존성은 추가하지 않는다.
- downstream credential, token boundary, SSRF guard, tool preset, schema drift 같은 CoreMCP 핵심 기능을 눈으로 확인할 수 있게 한다.

## 구현 상태

- 구현 위치: `apps/demo-mcp-suite`
- 실행 포트: `8791`
- 등록 payload 목록: `GET http://127.0.0.1:8791/demo-services`
- 8개 후보는 하나의 FastAPI 프로세스 안에서 각각 독립 MCP endpoint로 제공한다.
- 각 endpoint는 CoreMCP에 별도 MCP service로 등록할 수 있다.

```bash
cd apps/demo-mcp-suite
uv run demo-mcp-suite
```

```bash
make test-demo
```

## 추천 결론

가장 추천하는 1순위는 **Personal Ops Desk MCP**다.

이유:

- 개인용 CoreMCP 목적과 가장 잘 맞는다.
- 데모 tool이 다양하지만 구현은 단순하다.
- `readonly`, `dangerous_off`, `full_access` preset을 보여주기 좋다.
- 추후 실제 Mac mini 운영/문서/로그/백업 데모로 확장하기 쉽다.

## 후보 요약

| # | 후보 | 데모 가치 | 구현 난이도 | 추천도 |
|---:|---|---:|---:|---:|
| 1 | Personal Ops Desk MCP | 매우 높음 | 낮음 | ★★★★★ |
| 2 | Local Knowledge Vault MCP | 높음 | 낮음 | ★★★★☆ |
| 3 | Project Task Board MCP | 높음 | 낮음 | ★★★★☆ |
| 4 | Bookmark Research MCP | 중간~높음 | 낮음 | ★★★★☆ |
| 5 | Design Asset Catalog MCP | 중간 | 낮음 | ★★★☆☆ |
| 6 | Fake Finance Ledger MCP | 중간 | 중간 | ★★★☆☆ |
| 7 | Home Lab Status MCP | 중간 | 낮음 | ★★★☆☆ |
| 8 | Travel Planner MCP | 중간 | 낮음 | ★★☆☆☆ |

---

## 1. Personal Ops Desk MCP — 1순위 추천

개인 Mac mini/CoreMCP 운영 상태를 흉내 내는 가상 운영 데스크 MCP.

### 데모 시나리오

외부 AI client가 CoreMCP에 연결된 뒤 다음을 수행한다.

- 현재 CoreMCP 운영 체크리스트 조회
- fake service 상태 조회
- 최근 fake incident 조회
- 점검 메모 생성
- 위험 작업은 hidden 처리

### Tool 후보

| Tool | 설명 | Annotation |
|---|---|---|
| `ops_status` | fake API/Web/backup/refresh 상태 조회 | `readOnlyHint: true` |
| `ops_checklist` | 오늘 점검 항목 반환 | `readOnlyHint: true` |
| `incident_list` | 최근 가상 incident 목록 | `readOnlyHint: true` |
| `note_create` | 운영 메모 생성 | `readOnlyHint: false` |
| `backup_run` | fake backup 실행 | `readOnlyHint: false` |
| `service_restart` | fake service restart | `destructiveHint: true` |

### CoreMCP 데모 포인트

- `readonly` preset → `ops_status`, `ops_checklist`, `incident_list`만 노출
- `dangerous_off` preset → `service_restart` hidden
- `full_access` preset → 모든 tool callable
- validation summary/schema hash 확인
- Playground에서 fake 운영 메모 생성

### 구현 방식

- 별도 앱: `apps/demo-ops-mcp`
- FastAPI + JSON-RPC Streamable HTTP
- 데이터: in-memory 또는 `demo-data/*.json`
- 인증: 없음
- 포트 예시: `8791`

---

## 2. Local Knowledge Vault MCP

가상의 개인 지식창고/메모 서버.

### Tool 후보

| Tool | 설명 | Annotation |
|---|---|---|
| `note_search` | demo notes 검색 | `readOnlyHint: true` |
| `note_get` | 특정 note 조회 | `readOnlyHint: true` |
| `note_create` | note 생성 | `readOnlyHint: false` |
| `note_tag` | note에 tag 추가 | `readOnlyHint: false` |
| `note_delete` | note 삭제 | `destructiveHint: true` |

### 장점

- 누구나 이해하기 쉬움
- 검색/조회/생성/삭제 권한 차이를 보여주기 좋음

### 단점

- CoreMCP 운영 목적과 직접 연결성은 Personal Ops Desk보다 약함

---

## 3. Project Task Board MCP

가상의 프로젝트 태스크 보드.

### Tool 후보

| Tool | 설명 | Annotation |
|---|---|---|
| `task_list` | task 목록 조회 | `readOnlyHint: true` |
| `task_get` | task 상세 조회 | `readOnlyHint: true` |
| `task_create` | task 생성 | `readOnlyHint: false` |
| `task_update_status` | 상태 변경 | `readOnlyHint: false` |
| `task_archive` | task archive | `destructiveHint: true` |

### 장점

- 실제 업무 흐름과 비슷함
- Playground에서 즉시 효과가 보임

### 단점

- Linear/Notion 같은 실제 서비스와 유사해 “가상 demo” 느낌이 약할 수 있음

---

## 4. Bookmark Research MCP

가상의 북마크/리서치 링크 관리 서버.

### Tool 후보

| Tool | 설명 | Annotation |
|---|---|---|
| `bookmark_search` | 링크 검색 | `readOnlyHint: true` |
| `bookmark_list_by_tag` | tag별 링크 목록 | `readOnlyHint: true` |
| `bookmark_create` | 링크 저장 | `readOnlyHint: false` |
| `bookmark_summarize_stub` | 저장된 summary 필드 반환 | `readOnlyHint: true` |
| `bookmark_delete` | 링크 삭제 | `destructiveHint: true` |

### 주의

- 실제 웹 크롤링/외부 LLM 요약은 넣지 않는다.
- summary는 fixture JSON에 미리 넣는다.

---

## 5. Design Asset Catalog MCP

가상의 디자인 asset catalog.

### Tool 후보

| Tool | 설명 | Annotation |
|---|---|---|
| `asset_search` | 컴포넌트/아이콘 검색 | `readOnlyHint: true` |
| `color_tokens` | color token 반환 | `readOnlyHint: true` |
| `component_get` | component spec 조회 | `readOnlyHint: true` |
| `asset_register` | asset 등록 | `readOnlyHint: false` |
| `asset_deprecate` | asset deprecated 처리 | `destructiveHint: true` |

### 장점

- 최근 정리한 `docs/design/`과 연결 가능

### 단점

- 일반 사용자에게는 운영/태스크 데모보다 직관성이 낮음

---

## 6. Fake Finance Ledger MCP

가상의 개인 지출/수입 ledger.

### Tool 후보

| Tool | 설명 | Annotation |
|---|---|---|
| `ledger_summary` | 월별 summary | `readOnlyHint: true` |
| `transaction_search` | 거래 검색 | `readOnlyHint: true` |
| `transaction_create` | 거래 추가 | `readOnlyHint: false` |
| `transaction_categorize` | category 수정 | `readOnlyHint: false` |
| `transaction_delete` | 거래 삭제 | `destructiveHint: true` |

### 주의

- 실제 금융 데이터/계좌 연동 금지
- demo fixture만 사용

---

## 7. Home Lab Status MCP

가상의 홈랩/장비 상태 서버.

### Tool 후보

| Tool | 설명 | Annotation |
|---|---|---|
| `device_list` | 장비 목록 | `readOnlyHint: true` |
| `device_status` | CPU/RAM/disk fake 상태 | `readOnlyHint: true` |
| `service_logs` | fake log tail | `readOnlyHint: true` |
| `service_restart` | fake restart | `destructiveHint: true` |

### 장점

- Mac mini 운영 컨셉과 잘 맞음

### 단점

- Personal Ops Desk와 겹침. 단독 구현보다는 Ops Desk의 하위 기능으로 흡수 추천

---

## 8. Travel Planner MCP

가상의 여행 일정/장소 추천 서버.

### Tool 후보

| Tool | 설명 | Annotation |
|---|---|---|
| `itinerary_list` | 일정 조회 | `readOnlyHint: true` |
| `place_search` | fixture 장소 검색 | `readOnlyHint: true` |
| `itinerary_add_place` | 일정에 장소 추가 | `readOnlyHint: false` |
| `itinerary_remove_place` | 일정에서 장소 제거 | `destructiveHint: true` |

### 단점

- CoreMCP 운영/개발 데모와 연결성이 약함

---

## 선택 기준

| 기준 | 설명 |
|---|---|
| CoreMCP 목적 부합 | 개인형 MCP gateway/도구함을 잘 보여주는가 |
| Tool policy 데모 | read-only/write/destructive tool 구분이 자연스러운가 |
| 구현 단순성 | 외부 API 없이 빠르게 만들 수 있는가 |
| 확장성 | 나중에 실제 서비스 연결 데모로 발전 가능한가 |
| 시각적 이해 | Web Admin/Playground에서 결과가 직관적인가 |

## 최종 추천 순서

1. **Personal Ops Desk MCP**
2. **Local Knowledge Vault MCP**
3. **Project Task Board MCP**

## 구현 구조

```text
apps/demo-mcp-suite/
├── pyproject.toml
├── README.md
├── demo_mcp_suite/
│   ├── main.py
│   ├── registry.py
│   ├── runtime.py
│   └── servers/
│       ├── personal_ops.py
│       ├── knowledge_vault.py
│       ├── task_board.py
│       ├── bookmark_research.py
│       ├── design_assets.py
│       ├── finance_ledger.py
│       ├── home_lab.py
│       └── travel_planner.py
└── tests/
```

## 구현안 — Personal Ops Desk MCP

8개 demo 중 1순위 추천이며, suite에 포함된다.

```text
apps/demo-mcp-suite/demo_mcp_suite/servers/personal_ops.py
```

### 예상 endpoint

```text
http://127.0.0.1:8791/personal-ops/mcp
```

### CoreMCP 등록 값

```json
{
  "name": "Demo Ops MCP",
  "slug": "demo_ops",
  "description": "가상의 개인 운영 데스크 MCP",
  "endpoint_url": "http://127.0.0.1:8791/mcp",
  "auth_type": "none",
  "category": "demo"
}
```

### 구현 완료 기준

- `initialize`
- `tools/list`
- `tools/call`
- 6개 demo tool
- destructive/readOnly annotations
- pytest
- CoreMCP 등록/validate/playground 문서
