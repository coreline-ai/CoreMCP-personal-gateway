# CoreMCP Frontend UX (Personal)

문서 버전: v1.0
작성일: 2026-05-11

---

## 1. UX 방향

CoreMCP Web UI는 **본인을 위한 admin console**이다. 핵심 가치 한 줄:

> 내 MCP 도구함을 한눈에 보고, 새 MCP를 등록하고, 무엇이 호출됐는지 추적한다.

게이트웨이/proxy 같은 기술 용어보다 "도구함", "MCP 추가", "최근 호출" 같은 자연어 라벨 우선. 한국어 본문, 기술 키워드는 영문 유지.

---

## 2. Information Architecture

현재 Web Admin 구현은 Multica dashboard 형식을 흡수해 **좌측 sidebar + 상단 48px page header + route별 단일 content pane** 구조를 사용한다. `/clients` 같은 개별 route에서 모든 section을 한 번에 렌더링하지 않는다.

```text
CoreMCP Web
├── /                        Dashboard
├── /services
│   ├── /services            목록
│   ├── /services/new        등록 form
│   └── /services/[id]       상세 (Overview/Tools/Validation/Credential/Logs/Settings 탭)
├── /toolbox
│   └── /toolbox             default toolbox 관리
├── /clients
│   └── /clients             연결된 외부 AI client 관리
│   └── /clients/connect     Connect 가이드 (Codex CLI exec/Claude Code/OpenClaw/etc.)
├── /playground              tool 직접 호출 테스트
├── /logs
│   ├── /logs/invocations    tool 실행 기록
│   └── /logs/audit          관리 이벤트
└── /settings
    ├── /settings/general    locale, debug trace 등
    ├── /settings/tokens     admin token 회전 + client token 발급/관리 (ADR-030)
    ├── /settings/oauth      (옵션) OAuth 활성/비활성
    └── /settings/about      버전, health, links
```

`/sign-up`, `/login`, `/pricing`, `/billing`, `/legal`, `/marketplace`, `/workspace` 등은 제외.

---

## 3. 초기 접근 / 인증

### 3.1 첫 진입
- 첫 접근 시 `Admin Token` 입력 화면 (`cmcp_admin_*`)
- Mac mini에서 `~/.coremcp/admin-token` 파일 내용 입력
- 입력값은 sessionStorage(`coremcp_admin_token`)에 저장
- Web Admin UI는 admin token 권한으로만 동작
- 모든 API 호출에 `Authorization: Bearer <admin_token>` 헤더

### 3.2 Token 만료/오류
- 401 응답 시 sessionStorage clear + admin token 입력 화면 재노출
- 안내: "Admin token이 만료되었거나 회전되었습니다. ~/.coremcp/admin-token을 확인하세요."

### 3.3 토큰 보관 안전
- localhost 한정 사용 시 큰 문제 없음
- Tailscale 등 외부 노출 시: browser storage XSS 위험 → nonce 기반 CSP, dangerouslySetInnerHTML 금지

### 3.4 Admin vs Client Token

| 종류 | Web UI 사용 | /mcp 사용 | 저장 |
|---|---|---|---|
| Admin (`cmcp_admin_*`) | yes (sessionStorage) | yes (fallback) | 파일 + chmod 600 |
| Client (`cmcp_client_*`) | no | yes (primary) | DB hash (personal_access_tokens) |

Web UI는 admin token만 사용. Codex CLI exec 등 external client에는 client token 발급 권장 (ADR-030).

### 3.5 Icon 렌더링 정책 (XSS 방어)

CoreMCP Web UI는 service / tool icons를 다음 정책으로 렌더링한다:

1. **`<img>` 태그로만 렌더링** — inline SVG (innerHTML, dangerouslySetInnerHTML) 절대 금지
2. **CSP 적용**:
   ```http
   Content-Security-Policy: default-src 'self'; img-src 'self' data: https:; script-src 'self' 'nonce-...'; style-src 'self'; frame-ancestors 'none'
   ```
   현재 Next.js App Router 구현은 middleware nonce 기반 CSP를 사용하며 `script-src`/`style-src`에 `unsafe-inline`을 사용하지 않는다.
3. **content-type 화이트리스트**:
   - `image/png` (권장)
   - `image/webp` (권장)
   - `image/svg+xml` (제한적, sanitize 후만)
   - 그 외 fallback default icon 표시
4. **SVG 차단 옵션**:
   - 환경 변수 `ICON_SVG_ENABLED=false` (default false 권장)
   - false면 SVG는 default icon으로 대체
   - true면 backend에서 sanitize된 SVG만 표시
5. **외부 URL icons**:
   - 기본값은 remote HTTPS icon 차단 (`COREMCP_REMOTE_TOOL_ICONS_ENABLED=false`)
   - 명시 opt-in 시 https URL만 허용 (mixed content 차단)
   - lazy load + onError fallback
   - privacy 요구가 큰 운영 환경에서는 opt-in하지 않고 fallback/default icon 사용

### 3.6 Toolbox / Service 카드의 icons 렌더링

```jsx
// 권장 패턴
<img
  src={tool.icons?.[0]?.src ?? defaultIconUrl}
  alt={tool.title}
  onError={(e) => { e.currentTarget.src = defaultIconUrl }}
  loading="lazy"
  width={24}
  height={24}
/>

// 금지 패턴 (XSS 위험)
<div dangerouslySetInnerHTML={{ __html: tool.icons?.[0]?.svg }} />
```

### 3.7 Theme

- 기본 theme는 `dark`다.
- 사용자는 좌측 sidebar 하단 `Theme` selector에서 `Dark 기본 / Light / System`을 선택한다.
- 선택값은 `localStorage.coremcp_theme`에만 저장한다.
- admin token은 기존대로 `sessionStorage.coremcp_admin_token`만 사용한다.
- dark/light 전환은 CSS variable token으로 처리하고 backend 설정에는 영향을 주지 않는다.

---

## 4. Pages

### 4.1 Dashboard ( `/` )

목적: 한눈에 현재 상태 파악.

위젯:
- **Default Toolbox** 카드: 서비스 N개, tool M개, 마지막 호출 시각
- **MCP Services** 카드: active N / error M / disabled K
- **Recent Tool Calls** (최근 10건): exposed_name, status, latency, time ago
- **Connected Clients** 카드: claude_code (Mac mini), claude_code (MacBook) 등
- **System Health**: API/DB/Vault 상태
- **Quick Actions**:
  - "+ Add MCP" → `/services/new`
  - "Connect Codex CLI exec" → `/clients/connect`
  - "Open Playground" → `/playground`

빈 상태:
- toolbox 비어있으면 "MCP를 추가해 시작하세요"

### 4.2 Services 목록 ( `/services` )

리스트 컬럼:
- Name + slug + risk badge
- Endpoint URL (truncated)
- Status badge (Active / Validating / Error / Disabled / Auth required)
- Tool count
- Credential status (Connected / Not connected / Expired)
- Last validated
- Actions: validate, refresh tools, edit, view logs, disable

필터: status, slug 검색, risk level.

### 4.3 New MCP Service ( `/services/new` )

Step-based form:

#### Step 1: 기본 정보
- name (필수)
- slug (자동 생성, 수정 가능, 규칙 안내)
- description
- visibility (private 고정, 비활성)

#### Step 2: Endpoint
- endpoint URL (https 강제, localhost http 예외 안내)
- transport: Streamable HTTP (고정)
- auth_type: none / bearer_token / api_key_header

#### Step 3: Credential (auth_type ≠ none 시)
- bearer_token: token 입력 (write-only, 등록 후 masked만 표시)
- api_key_header: header_name + value

#### Step 4: Validation 진행
실시간 진행 표시:
```text
○ URL safety check
○ HTTP reachability
○ MCP initialize
○ tools/list
○ Tool metadata scan
```

각 단계 결과 (✓/✗/⚠ 텍스트 라벨, 이모지 X).

#### Step 5: Add to Toolbox
- "기본 도구함에 추가" 체크박스 (default ON)
- "Playground에서 테스트" 버튼
- "Codex CLI exec에 연결하기" 가이드 링크

### 4.4 Service Detail ( `/services/[id]` )

탭:

#### Overview
- 기본 정보 + 상태 + 메트릭 (호출 24h, 에러율)

#### Tools
- 캐시된 tool 목록
- 각 tool: exposed_name, original_name, description, input/output schema viewer, annotations, schema_hash, risk_level, last_seen
- 개별 policy: enabled toggle + permission level(`hidden`, `visible_only`, `callable`)
- preset: `readonly`, `dangerous_off`, `full_access`
- "Refresh tools" / "Validate" 버튼

#### Validation
- 최근 validation summary
- stage별 결과
- schema drift count + `schema_diff.added/removed/changed` 상세

#### Credential
- type, masked_value, status, last_rotated
- 회전 form
- 삭제 버튼

#### Logs
- 이 서비스로의 tool_invocations
- filter: status, exposed_tool_name, from/to

#### Settings
- private metadata(`category`, `homepage_url`, `documentation_url`, `logo_url`) 수정
- name, description, slug 수정
- slug 변경 시 alias deprecation 경고
- disable / enable
- 삭제 (soft-delete, 확인 modal)

### 4.5 Toolbox ( `/toolbox` )

default toolbox 단일 페이지. multi-toolbox는 옵션:

UI 요소:
- Toolbox 이름
- Service cards (drag-and-drop 순서, position 컬럼)
- Service card:
  - logo + name + slug
  - tool count, status, auth status
  - enable/disable toggle
  - "View tools" 아코디언 → tool 목록 (이름만)
  - remove 버튼
- 우측 상단: "+ Add MCP" → `/services` 또는 `/services/new`

빈 상태:
```text
도구함이 비어 있습니다.
MCP를 추가하면 Codex CLI exec와 선택 client에서 바로 사용할 수 있습니다.
[MCP 추가]
```

### 4.6 Connected Clients ( `/clients` )

리스트 컬럼:
- client_type (Codex CLI exec / Claude Code / OpenClaw / Claude / ChatGPT / Cursor)
- client_name (사용자 라벨)
- toolbox 연결
- protocol_version
- scopes
- client token (token_prefix masked, 예: `cmcp_client_xxxxxx`)
- last_used
- status (active / revoked)
- revoke 버튼 (external_connection + client token CASCADE)

신규 connection 생성 시 client token 평문이 modal에 1회만 노출됨. Codex CLI exec는 helper script가 token file과 env var를 관리한다 (ADR-030).

### 4.7 Connect Client Guide ( `/clients/connect` )

탭 (client별):

#### Codex CLI exec (Mac mini local)
```bash
make codex-install
make codex-smoke
infra/scripts/codex-exec-coremcp.sh "CoreMCP MCP 도구 목록을 확인해줘"
```
"Copy" 버튼.

#### Claude Code (optional, via bearer)
```bash
claude mcp add --transport http coremcp http://localhost:8787/mcp \
  --header "Authorization: Bearer <cmcp_client_token>"
```

#### Claude Code (optional MacBook via Tailscale)
- Tailscale 설치 안내 링크
- Magic DNS URL 표시
- 명령:
```bash
claude mcp add --transport http coremcp https://macmini.ts.net/mcp \
  --header "Authorization: Bearer <token>"
```
- token 표시 (보안 경고 함께)

#### OpenClaw (One-time Token)
- "Generate connection token" 버튼 → OTT 생성
- connection_prompt 표시 + copy
- TTL 10분 타이머

#### Claude desktop / ChatGPT / Cursor (옵션, OAuth 활성 시)
- step-by-step UI 캡처 (또는 placeholder)
- redirect_uri 안내

### 4.8 Playground ( `/playground` )

목적: tool 직접 호출 테스트, 디버깅.

UI:
- 좌측: Toolbox + tool 선택 dropdown
- 우측: input schema 기반 form 자동 생성 (JSON editor 옵션)
- "Call tool" 버튼
- 응답: content / isError / _meta JSON viewer
- latency, request_id 표시
- 최근 5개 호출 히스토리

### 4.9 Logs - Invocations ( `/logs/invocations` )

필터:
- service
- exposed_tool_name
- status
- from / to
- error_code

테이블 컬럼:
- created_at, exposed_tool_name, status, latency_ms, downstream_latency_ms, error_code
- 행 클릭 → 상세 modal (request_id, idempotency_key, sizes)

CSV / NDJSON export 버튼 (개인 분석용).

### 4.10 Logs - Audit ( `/logs/audit` )

필터:
- action
- resource_type
- from / to

테이블 컬럼:
- created_at, action, resource_type, resource_id, metadata preview

행 클릭 → 상세 modal.

### 4.11 Settings - General ( `/settings/general` )

- locale (ko / en)
- debug trace 활성/만료시간 (24h cap)
- log level (INFO/DEBUG/WARN)
- 향후 cache backend (memory/redis) 표시

### 4.12 Settings - Tokens ( `/settings/tokens` )

두 종류 token 관리 (ADR-030):

#### Admin Token (root)
- 현재 admin token masked (`cmcp_admin_••••abcd`)
- "Rotate admin token" 버튼 → confirm modal → 새 token 1회 평문 노출 + "Copy" + "다운로드"
- 회전 시 Web UI 모든 세션 logout + 사용자가 새 token으로 재로그인
- `~/.coremcp/admin-token` 파일 동기화 안내 (자동 또는 수동)

#### Client Tokens (external connections)
- 목록 테이블: token_prefix, external_connection, scopes, last_used, status, revoke
- "+ Generate new client token" → external_connection 선택 → 새 token 평문 1회 노출
- 개별 revoke 버튼 → 해당 token만 revoke (external_connection은 유지)
- 모든 client 일괄 revoke 옵션 (회전 시 편의)

### 4.13 Settings - OAuth (옵션) ( `/settings/oauth` )

- enable/disable toggle
- enable 시:
  - issuer URL
  - JWKS rotation 정보
  - registered DCR clients 목록
  - revoke 버튼

### 4.14 Settings - About ( `/settings/about` )

- CoreMCP version
- API health (`/health`)
- DB / Vault / Cache health
- Mac mini hostname
- documentation links → coremcp-docs/

---

## 5. UX States

### 5.1 Service Status Badge

| Status | Label | Color intent |
|---|---|---|
| draft | Draft | neutral |
| validating | Validating | info |
| active | Active | success |
| error | Error | danger |
| disabled | Disabled | neutral muted |
| auth_required | Auth required | warning |
| deleted | Deleted | neutral muted (필터로만) |

### 5.2 Credential Status

| Status | Label |
|---|---|
| connected | Connected |
| not_connected | Connection required |
| expired | Reconnect required |
| expired_soon | Expires in N days |
| revoked | Revoked |
| rotating | Rotating |
| error | Credential error |

### 5.3 Empty / Error / Loading

#### Empty
- "MCP 없음": "아직 MCP를 등록하지 않았습니다. Remote MCP URL을 입력해 첫 MCP를 등록하세요." + CTA
- "Toolbox 비어있음": "도구함이 비어 있습니다. MCP를 추가하면 Codex CLI exec에서 바로 쓸 수 있어요." + CTA
- "연결된 client 없음": "아직 연결된 AI client가 없습니다. Codex CLI exec 연결 가이드를 확인하세요." + CTA

#### Error
- "문제가 발생했어요. 잠시 후 다시 시도해 주세요. 오류 코드: <code>"
- 401: "토큰이 만료되었거나 회전되었습니다. 새 토큰을 입력해 주세요."

#### Loading
- skeleton placeholder
- 3초 이상이면 progress hint

---

## 6. Critical UX Copy

### 6.1 Token Boundary 경고 (Settings/Token)
```text
이 토큰은 CoreMCP API 전체에 대한 관리자 권한을 부여합니다.
공유 또는 노출되지 않도록 주의하세요. Tailscale 등 외부 노출 시에는
HTTPS만 사용하세요.

CoreMCP 토큰은 downstream MCP 호출에 절대 사용되지 않습니다.
하위 서비스 인증은 각 서비스의 credential을 별도 등록하세요.
```

### 6.2 Validation 실패 안내
```text
MCP 서버 연결을 확인할 수 없습니다.
- URL이 https인지 확인하세요. (localhost는 http 허용)
- 외부에서 접근 가능한지 확인하세요.
- bearer / api-key가 올바른지 확인하세요.
```

### 6.3 Tool Risk 경고
```text
이 도구는 외부 서비스에 쓰기 작업을 수행할 수 있습니다.
호출 전 권한과 대상 리소스를 확인하세요.
```

### 6.4 Schema 변경 알림
```text
연결된 MCP 서버의 tool 정의가 변경되었습니다.
도구함의 동작이 달라질 수 있으니 확인해 주세요.
[변경 내용 보기]
```

### 6.5 Service 삭제 confirm
```text
이 MCP 서비스를 삭제하면 도구함과 호출 기록도 영향을 받습니다.
soft-delete 후 30일 내 복구 가능합니다.
삭제하려면 서비스 이름을 입력해 주세요.
```

### 6.6 Admin Token Rotate confirm
```text
새 admin token을 발급하면 이전 admin token은 즉시 무효화됩니다.
Web Admin UI는 다시 로그인해야 합니다. /mcp에서 admin token을 fallback으로
사용 중인 client도 영향을 받습니다. 권장: Web UI에서 새 admin token으로 로그인
한 후, 각 client는 별도 client token으로 분리하세요.
```

### 6.7 Client Token Revoke confirm
```text
이 client token을 revoke하면 해당 external_connection은 즉시 접근 불가합니다.
영향: Codex CLI exec 또는 Claude Code 등 1개 client만 차단됩니다.
다른 client는 영향 없습니다.
```

---

## 7. Design System

### 7.1 Tokens
- color: shadcn defaults + 개인 brand (필요 시)
- typography: Pretendard (KR 우선) + Inter (fallback)
- spacing: 4px base
- border radius: 4 / 8 / 12
- elevation: 5 shadow levels
- motion: 150ms ease-out default

### 7.2 Components (shadcn/ui)
- Button, Input, Textarea, Select, Switch, Checkbox
- Card, Tabs, Dialog, Drawer
- Table, DataTable (tanstack)
- Toast (sonner)
- Tooltip, Popover
- Form (react-hook-form + zod)
- Skeleton, Progress
- Badge

### 7.3 Accessibility
- WCAG AA
- 키보드 navigation 전 페이지
- aria-* 표준
- color contrast 4.5:1
- focus indicator 명확
- screen reader 테스트 (VoiceOver)

### 7.4 Responsive
- breakpoints: 640 / 768 / 1024 / 1280
- mobile: dashboard / toolbox / logs 우선
- desktop: services / playground 우선

### 7.5 Dark Mode
- system preference 기본
- toggle in settings
- shadcn CSS variables

### 7.6 i18n (한국어 우선)
- next-intl 또는 자체 dict
- locale: `ko` (default), `en` (옵션)
- users.locale 컬럼과 sync
- date/number는 Intl 표준
- RTL 미지원

### 7.7 Browser Support
- Chrome / Safari / Edge / Firefox 마지막 2 major
- IE 미지원
- ES2022+

### 7.8 Avoid 용어
사용자 facing copy에서 다음 회피:
```text
gateway
proxy
aggregator
audience
resource server
authorization server
```

대신:
```text
도구함 (toolbox)
MCP 추가 / 등록
연결된 AI client
호출 기록
설정
```

기술 용어는 Developer/Logs/Settings 탭의 advanced 섹션에서만 사용.

### 7.9 Iconography
- 기본 icon set: lucide-react
- service / tool icons는 backend에서 받은 URL을 `<img>`로만 렌더링
- SVG inline 렌더링 금지 (XSS 방어, §3.5)
- 누락 시 default icon (lucide-react `Box`)

---

## 8. MVP Screens Checklist

Phase P2 완료 기준:

- [ ] Admin Token 입력 화면
- [ ] Dashboard
- [ ] Services 목록
- [ ] New MCP Service step form
- [ ] Service Detail (Overview/Tools/Validation/Credential 탭)
- [ ] Toolbox 관리
- [ ] Connected Clients 목록
- [ ] Connect Codex CLI exec 가이드 (`make codex-install` + wrapper)
- [ ] Playground (tool 호출 테스트)
- [ ] Logs / Invocations
- [ ] Logs / Audit
- [ ] Settings / Tokens (Admin + Client 분리)
- [ ] Client Token 생성 modal (평문 1회 노출)
- [ ] Connected Clients의 client token 표시 + revoke
- [ ] Settings / About (health)
- [ ] Error boundary fallback
- [ ] 401 → 토큰 재입력 flow
- [x] Icon 렌더링이 `<img>` 태그만 사용 (CSP + sanitize 확인)
- [x] ICON_SVG_ENABLED=false default 동작 (SVG → default icon fallback)

Phase P3+ 옵션:
- [ ] Service Detail / Logs / Settings 나머지 탭
- [ ] Connect OpenClaw (OTT)
- [ ] Settings / OAuth
- [ ] Connect ChatGPT / Cursor 가이드
- [ ] Schema diff viewer
- [ ] Tool annotations rich rendering

---

## 9. 제외 화면 (개인 컨텍스트)

production_docs_donotuse/08-frontend-ux.md에 있지만 본 프로젝트에 제외:
- Landing / Marketing
- Sign up / Login (정적 token으로 대체)
- Email verify
- MFA enroll / challenge
- Password reset
- Pricing
- Billing / Invoices / Subscription
- Marketplace browse / Marketplace detail
- Public submission
- Workspace switcher / Member invitation
- Legal (ToS / Privacy / Subprocessors / DPA)
- Help / Docs (외부 링크로 대체)
- Cookie banner
- Status page (외부 monitoring로 대체)
