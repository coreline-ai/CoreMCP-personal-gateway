# CoreMCP Frontend UX Specification

문서 버전: v0.1

---

## 1. UX 방향

CoreMCP의 핵심 UX는 “게이트웨이 설정”이 아니라 “내 MCP 도구함 관리”다.

사용자는 기술적으로 MCP gateway를 쓰지만, 화면에서는 다음 개념을 본다.

- 내 도구함
- MCP 추가
- 외부 AI에 연결
- 연결된 서비스
- 실행 로그

---

## 2. Information Architecture

```text
CoreMCP
├── Home / Landing
├── Dashboard
│   ├── My Toolbox
│   ├── Connected Clients
│   ├── Recent Tool Calls
│   └── Setup Guide
├── Marketplace
│   ├── MCP List
│   ├── Search / Category
│   └── MCP Detail
├── Developer Console
│   ├── My MCP Services
│   ├── New MCP Service
│   ├── Service Detail
│   ├── Validation Report
│   ├── Tool Schema
│   └── Test Call
├── Playground
│   ├── Select Toolbox
│   ├── Chat/Test
│   └── Tool Trace
├── Settings
│   ├── Profile
│   ├── Connected Clients
│   ├── Credentials
│   ├── API/OAuth
│   └── Audit Logs
├── Auth
│   ├── Sign up
│   ├── Login
│   ├── Email verify
│   ├── MFA enroll / challenge
│   └── Password reset
├── Pricing
├── Billing
│   ├── Subscription
│   ├── Invoices
│   └── Usage
├── Legal
│   ├── Terms of Service
│   ├── Privacy Policy
│   ├── Subprocessors
│   └── DPA (download)
└── Help
    ├── Docs link
    ├── Status page link
    └── Contact / Support
```

---

## 3. Core Pages

### 3.1 Landing Page

목표: 제품을 5초 안에 이해시키기.

Hero:

```text
Connect once. Use every MCP tool anywhere.

Claude Code, Claude, ChatGPT에서 내 MCP 도구함을 하나의 MCP 서버처럼 사용하세요.
```

CTA:

- Start with CoreMCP
- Register your MCP server
- View demo

Sections:

1. MCP 도구함
2. 외부 AI 연결
3. 개발자 MCP 등록
4. 보안/감사
5. 팀용 관리

### 3.2 Dashboard

위젯:

- Default Toolbox card
- Connected clients card
- MCP services status
- Recent invocations
- Quick connect command

Primary CTA:

- Add MCP to Toolbox
- Connect Claude Code

### 3.3 My Toolbox

목표: 사용자가 외부 AI에 노출할 MCP를 관리.

UI elements:

- Toolbox selector
- MCP service cards
- enabled toggle
- tool count
- status badge
- auth status
- remove button
- add MCP button

Service card 예:

```text
GitHub MCP
6 tools · connected · active
[enabled toggle]
Tools: github.create_issue, github.search_repo, ...
```

Empty state:

```text
아직 도구함에 MCP가 없습니다.
Marketplace에서 MCP를 추가하거나 직접 Remote MCP 서버를 등록하세요.
```

### 3.4 MCP Marketplace

MVP에서는 private registry 중심이지만 UI 구조는 marketplace 확장 가능해야 한다.

Filters:

- category
- official/verified/community
- auth required
- read/write capability
- popularity

Card fields:

- logo
- name
- provider
- description
- tool count
- risk badge
- verified badge
- add button

### 3.5 MCP Detail

Sections:

- overview
- tools
- required permissions
- provider
- validation status
- risk notes
- add to toolbox
- test in playground

Tool list:

```text
github.create_issue
Description...
Inputs: repo, title, body
Risk: write
```

### 3.6 Developer Console - My MCP Services

List columns:

- name
- endpoint
- visibility
- status
- tools
- last validated
- actions

Actions:

- validate
- refresh tools
- edit
- view logs
- disable

### 3.7 New MCP Service

Step-based form:

#### Step 1: Basic Info

- service name
- slug
- description
- visibility: private/unlisted/public request

#### Step 2: Endpoint

- endpoint URL
- transport: Streamable HTTP
- auth type

#### Step 3: Credential

- no auth
- bearer token
- API key header

#### Step 4: Validate

Show validation progress:

```text
✓ URL safety check
✓ Reachability
✓ MCP initialize
✓ tools/list
⚠ Tool metadata scan warnings
```

#### Step 5: Add to Toolbox

- add to default toolbox
- test in playground
- connect Claude Code

### 3.8 Service Detail

Tabs:

- Overview
- Tools
- Validation
- Credentials
- Logs
- Settings

### 3.9 Tool Detail

Fields:

- exposed name
- original name
- description
- input schema viewer
- output schema viewer
- schema hash
- risk scan
- sample call

### 3.10 Connected Clients

List:

- Claude Code
- Claude
- ChatGPT
- OpenClaw
- Other

Columns:

- client type
- client name
- toolbox
- scopes
- last used
- status
- revoke

### 3.11 Connect Client Guide

Client tabs:

#### Claude Code

```bash
claude mcp add --transport http coremcp https://coremcp.example.com/mcp
```

Bearer fallback:

```bash
claude mcp add --transport http coremcp https://coremcp.example.com/mcp \
  --header "Authorization: Bearer <token>"
```

#### Claude

- Settings
- Connectors
- Add custom connector
- Enter CoreMCP URL
- Complete login

#### ChatGPT

- Settings > Apps / Developer Mode
- Add custom MCP app
- Enter server URL
- Complete auth flow

#### OpenClaw

- Generate connection prompt
- Copy text
- Paste into OpenClaw chat

### 3.12 Auth Pages

#### Sign up

- email + password (또는 Google/GitHub OIDC)
- ToS / Privacy 명시적 체크
- captcha (Phase 1+)

#### Email Verify

- "메일을 보냈습니다" 안내
- resend (60s cooldown)
- 미인증 시 dashboard 기능 제한 안내

#### Login

- email/password 또는 SSO
- MFA challenge (활성 시)
- "이 기기 기억" (30d trusted device)

#### MFA Enroll

- TOTP QR + recovery codes 8개 display
- recovery codes는 한 번만 표시 → 사용자가 저장

#### Password Reset

- email magic link
- token TTL 15분

### 3.13 Billing

- 현재 plan, 다음 결제일
- usage gauge (월간 tool_call %)
- invoice list + PDF download (Stripe portal redirect)
- upgrade / downgrade CTA
- cancel subscription (retention modal)

### 3.14 Pricing Page (Public)

- Free / Pro / Team / Enterprise 4 카드
- feature matrix
- annual 17% 할인 toggle
- FAQ
- "Contact Sales" Enterprise

### 3.15 Settings Audit Logs

- filter: action, resource_type, from/to
- detail modal
- export (CSV/NDJSON) — Team+

### 3.16 Workspace Switcher (Phase 5 미리 IA 반영)

- top nav에서 toggle
- workspace 생성 (Team+ only)
- member invitation

---

## 4. UX States

### 4.1 Service Status Badge

| Status | Badge | Color intent |
|---|---|---|
| draft | Draft | neutral |
| validating | Validating | info |
| active | Active | success |
| error | Error | danger |
| disabled | Disabled | neutral |
| review_pending | Review pending | warning |
| auth_required | Auth required | warning |
| deleted | Deleted | neutral muted |

### 4.2 Credential Status

| Status | Copy | Color intent |
|---|---|---|
| connected | Connected | success |
| not_connected | Connection required | warning |
| expired | Reconnect required | danger |
| revoked | Revoked | danger |
| error | Credential error | danger |
| expired_soon | Expires in N days | warning |
| rotating | Rotating | info |

### 4.3 Empty States

#### No MCP Services

```text
등록된 MCP 서버가 없습니다.
Remote MCP URL을 입력해 첫 MCP를 등록하세요.
```

#### No Toolbox Items

```text
도구함이 비어 있습니다.
MCP를 추가하면 Claude Code와 ChatGPT에서 바로 사용할 수 있습니다.
```

#### No Connected Clients

```text
아직 연결된 AI 클라이언트가 없습니다.
Claude Code, Claude, ChatGPT 중 하나를 연결하세요.
```

#### Error state

```text
문제가 발생했어요.
잠시 후 다시 시도해 주세요.
오류 코드: <code>
```

#### Loading state

- skeleton placeholder
- 3초 이상 시 progress hint

#### No search results

```text
검색 결과가 없습니다.
다른 키워드를 시도하거나 Marketplace 카테고리를 둘러보세요.
```

---

## 5. Critical UX Copy

### 5.1 Token Boundary Warning

```text
CoreMCP 로그인 토큰은 외부 AI 클라이언트가 CoreMCP에 접속하기 위한 토큰입니다.
하위 MCP 서비스 호출에는 별도의 서비스 credential이 사용됩니다.
```

### 5.2 Public Submission Warning

```text
공개 MCP로 제출하면 이름, 설명, 도구 목록이 Marketplace에 표시될 수 있습니다.
민감한 정보가 tool 설명이나 schema에 포함되어 있지 않은지 확인하세요.
```

### 5.3 Tool Risk Warning

```text
이 도구는 외부 서비스에 쓰기 작업을 수행할 수 있습니다.
호출 전 권한과 대상 리소스를 확인하세요.
```

### 5.4 Sign-in Failure

```text
이메일 또는 비밀번호가 일치하지 않습니다.
5회 실패 시 15분간 잠금됩니다.
```

### 5.5 Validation Failure

```text
MCP 서버 연결을 확인할 수 없습니다.
- URL이 https인지
- 외부에서 접근 가능한지
- bearer/api-key가 올바른지
확인 후 다시 시도하세요.
```

### 5.6 Downstream Timeout

```text
연결된 MCP 서버가 응답하지 않습니다.
잠시 후 다시 시도하거나, 서비스 운영자에게 문의하세요.
```

### 5.7 Schema Changed Warning

```text
연결된 MCP 서버의 tool 정의가 변경되었습니다.
도구함의 동작이 달라질 수 있으니 확인해 주세요.
[변경 내용 보기]
```

### 5.8 Right-to-Erasure Confirmation

```text
계정을 삭제하면 30일의 복구 기간 후 모든 데이터가 영구 삭제됩니다.
이 작업은 되돌릴 수 없습니다.
확인을 위해 이메일을 입력해 주세요.
```

---

## 6. MVP Screens Checklist

- [ ] Landing
- [ ] Login
- [ ] Dashboard
- [ ] My Toolbox
- [ ] New MCP Service
- [ ] Validation Report
- [ ] Service Detail
- [ ] Tool List
- [ ] Test Tool Call
- [ ] Connect Claude Code Guide
- [ ] Connected Clients
- [ ] Audit/Invocation Logs
- [ ] Sign up + Email verify
- [ ] Login + MFA challenge
- [ ] Password reset
- [ ] Pricing page (public)
- [ ] Billing (Stripe portal redirect)
- [ ] Settings > Audit Logs
- [ ] Right-to-erasure flow
- [ ] Privacy policy / ToS
- [ ] Error boundary fallback
- [ ] Cookie banner (EU IP)

---

## 7. Design System Notes

권장 UI tone:

- developer-friendly
- security-forward
- simple setup
- avoid “gateway complexity” language

Primary navigation labels:

```text
Toolbox
Marketplace
Developer
Playground
Settings
```

Avoid:

```text
Proxy Config
Downstream Registry
OAuth Resource Metadata
```

Use technical terms only inside Developer Console advanced sections.

### 7.1 Design Tokens

- color: shadcn defaults + custom brand (TBD)
- typography: Inter (or Pretendard for KR)
- spacing: 4px base
- border radius: 4 / 8 / 12
- elevation: 5 shadow levels
- motion: 150ms ease-out default

### 7.2 Accessibility

- WCAG AA 준수
- 키보드 navigation 전 페이지
- aria-* attribute 표준
- color contrast 4.5:1 이상
- screen reader 테스트 (NVDA / VoiceOver)
- focus indicator 명확

### 7.3 Responsive

- breakpoints: 640 / 768 / 1024 / 1280 / 1536
- mobile 우선 일부 페이지 (dashboard, toolbox, settings)
- desktop 우선: developer console, playground

### 7.4 Dark Mode

- system preference 기본
- toggle in settings
- shadcn css variables 패턴

### 7.5 i18n

- next-intl 사용
- locale: en (default), ko
- 사용자 locale은 users.locale 컬럼
- date/number는 Intl 표준
- RTL는 미지원 (v2+)

### 7.6 Browser Support

- Chrome / Edge / Firefox / Safari 마지막 2 major
- IE 미지원
- ES2022+
