# CoreMCP MCP Client Profiles

문서 버전: v0.1
작성일: 2026-05-11
대상 spec: MCP 2025-06-18

이 문서는 CoreMCP가 지원해야 하는 외부 AI client별 MCP/OAuth 처리 특성을 정리한다.
값이 명확하지 않은 항목은 (검증 필요) 표기. 실제 호환성 테스트는 10-qa-test-plan.md에 따라 진행한다.

---

## 1. 핵심 호환성 매트릭스

| 항목 | Claude Code | Claude (web/desktop) | ChatGPT Custom MCP | OpenClaw | Cursor | Windsurf |
|---|---|---|---|---|---|---|
| Transport | Streamable HTTP | Streamable HTTP | Streamable HTTP | one-time token + 자체 | Streamable HTTP | Streamable HTTP |
| OAuth 2.1 | yes | yes | yes | no (OTT) | yes (검증 필요) | yes (검증 필요) |
| PKCE | S256 required | S256 required | S256 (검증 필요) | n/a | S256 (검증 필요) | S256 (검증 필요) |
| DCR (RFC 7591) | yes | yes | yes | n/a | partial (검증 필요) | partial (검증 필요) |
| Resource Indicator (RFC 8707) | yes | yes | yes (검증 필요) | n/a | 검증 필요 | 검증 필요 |
| Bearer fallback (--header) | yes | partial | 제한적 | n/a | yes | yes |
| Protected resource metadata 자동 발견 | yes | yes | yes (검증 필요) | n/a | 검증 필요 | 검증 필요 |
| Mcp-Session-Id 사용 | yes | yes | yes | n/a | yes | yes |
| MCP-Protocol-Version 협상 | 2025-06-18 | 2025-06-18 | 검증 필요 | n/a | 검증 필요 | 검증 필요 |
| GET SSE 사용 | yes | yes | 검증 필요 | n/a | 검증 필요 | 검증 필요 |
| tools/list pagination cursor | 검증 필요 | 검증 필요 | 검증 필요 | n/a | 검증 필요 | 검증 필요 |
| notifications/tools/list_changed 처리 | yes | yes | 검증 필요 | n/a | 검증 필요 | 검증 필요 |
| structuredContent 지원 (2025-06-18 신규) | 검증 필요 | 검증 필요 | 검증 필요 | n/a | 검증 필요 | 검증 필요 |
| tool annotations 표시 (destructive 등) | yes | yes | 검증 필요 | n/a | 검증 필요 | 검증 필요 |

---

## 2. Claude Code

### 2.1 등록 명령
```bash
claude mcp add --transport http coremcp https://coremcp.example.com/mcp
```

Bearer fallback (OAuth 미지원 환경):
```bash
claude mcp add --transport http coremcp https://coremcp.example.com/mcp \
  --header "Authorization: Bearer <coremcp_access_token>"
```

### 2.2 OAuth 플로우
1. `claude mcp add` 실행
2. Claude Code가 `POST /mcp` (no auth) 호출
3. 401 + `WWW-Authenticate: Bearer resource_metadata="..."` 수신
4. metadata 페치 → authorization_server URL 획득
5. DCR로 client 등록
6. PKCE 시작 → 브라우저 popup → consent → callback
7. token 발급 → `/mcp` 재호출

### 2.3 알려진 quirk
- Bearer fallback 시 protected resource metadata 미페치
- 한 user가 여러 머신에서 동일 claude mcp add 시 별도 OAuth client 생성 (정상)
- session 단절 시 자동 reconnect 시도

### 2.4 권장 시나리오
- 권장: OAuth flow 사용
- fallback: long-lived API key (Phase 3 정책에 따라)

---

## 3. Claude (web / desktop "Custom Connector")

### 3.1 등록 방법
Settings → Connectors → Add custom connector → CoreMCP URL 입력 → OAuth.

### 3.2 redirect_uri 특성
- Anthropic 측 redirect URI 패턴: `https://claude.ai/oauth/callback`
- DCR 응답에 이 redirect_uri를 client_metadata로 등록해야 함

### 3.3 알려진 quirk
- desktop과 web이 다른 client_id 생성 (검증 필요)
- iOS/Android app은 별도 redirect scheme (검증 필요)
- tool count가 많을 때 UI에서 일부만 표시될 수 있음

---

## 4. ChatGPT Custom MCP

### 4.1 등록 방법
Settings → Apps / Developer Mode → Add custom MCP app → CoreMCP URL.

### 4.2 알려진 제약
- scope 명세 까다로움 — OpenAI가 사전 정의된 scope만 허용 (검증 필요)
- tool 이름의 dot 처리 — UI에서 namespace로 split될 수 있음 (검증 필요)
- response content 중 image/audio 처리 (검증 필요)

### 4.3 권장
- service_slug에 dot/dash 외 special char 회피
- description 영문 우선

---

## 5. OpenClaw / Local AI Agents (One-time Token 흐름)

### 5.1 연결 흐름
1. 사용자 CoreMCP UI에서 "Connect OpenClaw" 클릭
2. CoreMCP가 OTT 생성 (TTL 10분, IP/UA binding)
3. 사용자가 connection_prompt를 OpenClaw 채팅에 붙여넣기
4. OpenClaw가 `POST /v1/external-connections/exchange` 호출
5. CoreMCP가 access_token + refresh_token 발급
6. 이후 `/mcp` 호출은 일반 OAuth client처럼

### 5.2 보안 주의
- OTT는 한 번만 사용 (`used_at` 기록)
- token_hash만 DB 저장 (06-security-auth.md §5)
- IP/UA가 발급 시점과 다를 경우 warning, 정책에 따라 reject 가능

### 5.3 알려진 quirk
- 일부 local agent는 refresh token rotation 미지원 → access token 만료 시 재발급 필요

---

## 6. Cursor / Windsurf / 기타

### 6.1 Cursor
- Settings → MCP → Add Remote Server (검증 필요)
- OAuth/DCR 지원 여부 버전별 상이
- bearer header fallback은 안정적

### 6.2 Windsurf
- Cursor와 유사 (검증 필요)

### 6.3 기타 (Cody, Continue, etc.)
- protocol-level 호환되면 동작 가정
- 호환성 issue 보고 시 case-by-case

---

## 7. Client Profile Abstraction (구현)

03-architecture.md §11 "Client compatibility" 대응 코드 구조:

```python
class ClientProfile:
    client_type: ExternalClientType
    protocol_version_supported: list[str]
    supports_sse_get: bool
    supports_oauth_dcr: bool
    supports_pkce: bool
    requires_resource_indicator: bool
    requires_bearer_header_fallback: bool
    tool_name_dot_safe: bool
    max_tools_per_list: int | None
    known_quirks: list[str]

PROFILES = {
    "claude_code": ClientProfile(...),
    "claude": ClientProfile(...),
    "chatgpt": ClientProfile(...),
    "openclaw": ClientProfile(...),
    "cursor": ClientProfile(...),
    "windsurf": ClientProfile(...),
    "other": ClientProfile(...),  # 보수적 기본값
}
```

initialize 응답 시 client info 기반 profile lookup → 일부 응답 변형 (예: tool_name 형식 조정).

---

## 8. 호환성 테스트 매트릭스

10-qa-test-plan.md §5 MCP Protocol Compatibility의 확장으로 다음을 추가:

| 시나리오 | Claude Code | Claude | ChatGPT | OpenClaw | Cursor | Windsurf |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| OAuth 신규 가입 → 첫 tool call | P0 | P0 | P0 | n/a | P1 | P1 |
| Bearer fallback | P1 | P2 | P2 | n/a | P1 | P1 |
| One-time token exchange | n/a | n/a | n/a | P0 | n/a | n/a |
| revoke 후 401 처리 | P0 | P0 | P0 | P0 | P1 | P1 |
| tools/list_changed propagation | P1 | P1 | P2 | n/a | P2 | P2 |
| 50+ tools 동시 노출 | P1 | P1 | P1 | n/a | P1 | P1 |
| structuredContent 응답 | P2 | P2 | P2 | n/a | P2 | P2 |
| 이미지 content 응답 | P2 | P2 | P2 | n/a | P2 | P2 |
| reconnect after network drop | P1 | P1 | P1 | P1 | P1 | P1 |

P0=MVP gate, P1=Beta gate, P2=GA gate.

---

## 9. Discovery: 신규 Client 등장 시 절차
1. external_client_type enum에 신규 값 추가 (05-database-schema.md)
2. ClientProfile 추가
3. 호환성 테스트 케이스 작성
4. Connect Client Guide UI (08-frontend-ux.md §3.11)에 탭 추가
5. 본 문서에 sub-section 추가

---

## 10. 알려진 비호환 패턴 (모든 client 공통)
- session id 만 보내고 bearer token 누락 → 401 (06-security-auth.md §1)
- audience mismatch JWT → 401
- protocol version 미지원 → InitializeResult에 downgrade or 정중한 error
- response body > 5MB → 413 또는 truncated content
- tool 이름이 client-side regex(`^[a-z0-9_]+$`)에 안 맞음 → tool 누락 가능

---

## 11. Open Questions
1. ChatGPT가 dot tool name을 어떻게 표시하는지 실측 필요
2. Claude desktop과 web의 client_id 분리 여부
3. Cursor가 DCR을 어느 버전부터 지원하는지
4. iOS Claude app의 redirect_uri 패턴
5. OpenClaw 외 다른 local agent (Cody, Continue) 사용자 수와 우선순위
6. structuredContent를 ChatGPT/Cursor가 처리하는지 (2025-06-18 신규 spec)
