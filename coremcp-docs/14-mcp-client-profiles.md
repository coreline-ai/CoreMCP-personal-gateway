# CoreMCP MCP Client Profiles (Personal)

문서 버전: v1.0
작성일: 2026-05-11
대상 spec: MCP 2025-06-18 + 2025-11-25 (병행 지원, ADR-029)

본 문서는 CoreMCP가 본인 환경에서 지원하는 외부 AI client별 특성을 정리한다. 최우선은 Codex CLI `exec`, Claude Code와 기타 client는 옵션 호환 경로다.

---

## 1. 우선순위

1. Codex CLI `exec` (Mac mini 로컬) — **MVP 필수**
2. Claude Code (Mac mini 로컬 + MacBook via Tailscale) — 옵션 호환
3. OpenClaw — 옵션 (one-time token)
4. Claude desktop / web custom connector — 옵션 (OAuth 활성 시)
5. ChatGPT custom MCP — 옵션 (OAuth + DCR/CIMD 활성 시)
6. Cursor / Windsurf — 옵션
7. 기타 — 호환 시 동작

---

## 2. Compatibility Matrix

| 항목 | Codex CLI exec | Claude Code | OpenClaw | Claude/ChatGPT | Cursor / Windsurf |
|---|---|---|---|---|---|
| 우선순위 | P0 | P1 | P1 | P2 | P2 |
| Transport | Streamable HTTP via Codex MCP config | Streamable HTTP | 자체 + OTT | Streamable HTTP | Streamable HTTP |
| client token | yes (`COREMCP_CLIENT_TOKEN`) | yes | exchange 후 yes | OAuth 권장 | yes |
| OAuth 2.1 | optional | optional (`AUTH_MODE=oauth`) | n/a | required for full flow | 버전별 |
| DCR/CIMD | optional | optional | n/a | yes | partial |
| One-time token exchange | n/a | n/a | yes | n/a | n/a |
| Mcp-Session-Id | yes | yes | n/a | yes | yes |
| MCP-Protocol-Version (Primary) | 2025-11-25/2025-06-18 협상 | 2025-06-18 | n/a | 검증 필요 | 검증 필요 |
| GET SSE listChanged | yes | yes | n/a | 검증 필요 | 검증 필요 |
| tool annotations 표시 | yes | yes | n/a | 검증 필요 | 검증 필요 |

---

## 3. Codex CLI exec (P0 — MVP 필수)

### 3.0 인증 모드 우선순위

| 모드 | 우선순위 | 시나리오 |
|---|---|---|
| Codex client token (`cmcp_client_*`) | P0 | `make codex-install`이 발급 후 `~/.coremcp/codex-client-token`에 저장 |
| Admin static bearer | fallback | 수동 curl/smoke 또는 bootstrap 문제 진단 |
| OAuth flow | P2 | ChatGPT/Cursor 등 OAuth 강제 client와 동시 운영 시 |

Codex CLI는 `~/.codex/config.toml`의 MCP server entry에 CoreMCP streamable HTTP URL을 저장하고, token 값은 config에 직접 저장하지 않는다. 실행 wrapper가 `COREMCP_CLIENT_TOKEN` 환경변수로만 주입한다.

### 3.1 등록

```bash
make codex-install
make codex-smoke
```

내부적으로 다음 형태가 등록된다.

```bash
codex mcp add coremcp \
  --url http://127.0.0.1:8787/mcp \
  --bearer-token-env-var COREMCP_CLIENT_TOKEN
```

### 3.2 실행

```bash
infra/scripts/codex-exec-coremcp.sh "CoreMCP MCP 도구 목록을 확인해줘"
```

### 3.3 알려진 quirk

- `codex exec`를 직접 호출하면 shell에 `COREMCP_CLIENT_TOKEN`이 있어야 한다.
- wrapper는 `~/.coremcp/codex-client-token`을 읽어 env로 주입한다.
- 실제 LLM 호출 smoke는 비용/네트워크 변동이 있으므로 기본 자동 테스트에서는 제외하고, `make codex-smoke`는 non-LLM MCP initialize/tools-list까지만 검증한다.

---

## 4. Claude Code (P1 — 옵션 호환)

### 3.0 인증 모드 우선순위 (CoreMCP 측)

| 모드 | 우선순위 | 시나리오 |
|---|---|---|
| Admin static bearer (`--header`) | P0 | Mac mini local + MacBook Tailscale, MVP |
| Per-client token (`cmcp_client_*`) | P1 | Mac mini local과 MacBook을 분리해 revoke 가능 |
| OAuth flow (자체 AS) + CIMD/DCR | P2 | ChatGPT/Cursor 동시 사용 시 |

P0 운영 경로는 Codex CLI exec다.
Claude Code 사용 시 connection별 client token으로 분리한다 (ADR-030).
P2에서 OAuth로 추가 client 지원.

### 3.1 Mac mini 로컬 등록
```bash
claude mcp add --transport http coremcp http://localhost:8787/mcp \
  --header "Authorization: Bearer $(cat ~/.coremcp/admin-token)"
```

### 3.2 MacBook via Tailscale
```bash
claude mcp add --transport http coremcp https://macmini.tail-scale.ts.net/mcp \
  --header "Authorization: Bearer <token>"
```

### 3.3 OAuth flow (옵션, 자체 AS 활성 시)
1. `claude mcp add --transport http coremcp http://localhost:8787/mcp` (header 없이)
2. CoreMCP 401 + WWW-Authenticate metadata
3. Claude Code DCR + PKCE
4. 단일 사용자라 consent 자동 승인
5. token 발급 → 사용

### 3.4 알려진 quirk
- Bearer 모드에서 resource metadata 미페치 OK
- session 단절 시 자동 reconnect
- Mac mini 잠금 시 keychain 접근 실패 → service_not_connected 빈발 → 잠금 해제 후 안정화

### 3.5 권장 시나리오
- Mac mini 로컬 사용: bearer header
- MacBook 외부: bearer header + Tailscale 강제
- OAuth flow는 필요할 때 추가

---

## 5. OpenClaw (P1 — 옵션)

### 4.1 연결 흐름
1. Web UI "Connect OpenClaw" 클릭
2. CoreMCP가 OTT 발급 (TTL 10분, IP/UA binding)
3. connection_prompt 표시
4. OpenClaw 채팅에 paste
5. OpenClaw → `/v1/external-connections/exchange`
6. CoreMCP가 access_token (정적 또는 JWT) 발급
7. 이후 일반 OAuth client 처럼

### 4.2 보안
- OTT는 1회 사용
- token_hash만 DB
- IP/UA mismatch는 strict 모드에서 reject

### 4.3 알려진 quirk
- refresh token rotation 미지원 → 정적 token 사용 권장

---

## 6. Claude desktop / web Custom Connector (P2 — 옵션)

### 5.1 등록
Settings → Connectors → Add custom → CoreMCP URL.

### 5.2 redirect_uri
`https://claude.ai/oauth/callback` 등 Anthropic 패턴. DCR 응답에 client_metadata로 등록.

### 5.3 본인용 단순화
단일 사용자라 user_consents에 첫 authorize 자동 승인 옵션.

### 5.4 미설정 시
정적 bearer로 동작하지 않을 수 있음 → OAuth 활성 후 사용.

---

## 7. ChatGPT Custom MCP (P2 — 옵션)

### 7.1 등록
Settings → Apps / Developer Mode → Add custom MCP app → CoreMCP URL.

### 7.2 제약
- scope 명세 까다로움 — OpenAI 사전 정의 scope만 허용 (검증 필요)
- dot이 포함된 tool 이름 UI 처리 (검증 필요)
- ChatGPT는 CIMD를 권장. AUTH_MODE=oauth 활성 시 CoreMCP가 CIMD URL을 fetch해서 client metadata 검증 (ADR-036)
- DCR fallback도 지원

### 7.3 본인용 권장
- service_slug에 dot/dash 외 special char 회피
- description 한국어 OK이지만 너무 길지 않게

---

## 8. Cursor / Windsurf (P2 — 옵션)

### 8.1 Cursor
- Settings → MCP → Add Remote Server
- 버전별로 OAuth/DCR 지원 차이 큼
- bearer header fallback 안정적

### 8.2 Windsurf
Cursor와 유사 (검증 필요).

---

## 9. Client Profile 코드 추상화

```python
@dataclass
class ClientProfile:
    client_type: str
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
    "codex_cli": ClientProfile(
        client_type="codex_cli",
        protocol_version_supported=["2025-11-25", "2025-06-18"],
        supports_sse_get=True,
        supports_oauth_dcr=False,
        supports_pkce=False,
        requires_resource_indicator=False,
        requires_bearer_header_fallback=False,
        tool_name_dot_safe=True,
        max_tools_per_list=None,
        known_quirks=["COREMCP_CLIENT_TOKEN env required for codex exec"],
    ),
    "claude_code": ClientProfile(
        client_type="claude_code",
        protocol_version_supported=["2025-06-18", "2025-03-26"],
        supports_sse_get=True,
        supports_oauth_dcr=True,
        supports_pkce=True,
        requires_resource_indicator=True,
        requires_bearer_header_fallback=True,
        tool_name_dot_safe=True,
        max_tools_per_list=None,
        known_quirks=["keychain unlock dependency"],
    ),
    # ... 그 외 client
}
```

initialize 응답 시 clientInfo.name으로 profile lookup → 일부 응답 변형.

---

## 10. 본인 환경 검증 우선순위

다음 시나리오를 본인이 직접 점검:

| 시나리오 | 우선순위 |
|---|---|
| Codex CLI exec → CoreMCP → fake downstream | P0 |
| Codex CLI exec → CoreMCP → 실제 GitHub MCP | P0 |
| Claude Code → Tailscale → CoreMCP | P1 |
| listChanged emit 후 Claude Code 자동 refresh | P1 |
| Mac mini 재부팅 후 launchd 자동 시작 + keychain 잠금 해소 | P1 |
| 50+ tools 노출 시 Claude Code UI | P1 |
| revoke 후 401 | P1 |
| OpenClaw OTT exchange | P2 |
| Claude desktop OAuth (자체 AS 활성 시) | P2 |

---

## 11. 신규 client 추가 절차

1. external_client_type enum 또는 CHECK 제약에 신규 값 추가 (05-database-schema.md)
2. ClientProfile 추가
3. 호환성 테스트 케이스 작성 (10-test-plan.md)
4. Web UI Connect 가이드 페이지 (08-frontend-ux.md)에 탭 추가
5. 본 문서에 sub-section 추가

---

## 12. 비호환 패턴 (모든 client 공통)
- session id만 보내고 bearer 누락 → 401
- audience mismatch JWT → 401
- protocol version 미지원 → InitializeResult에 downgrade or 정중한 error
- response body > 5MB → 413 또는 truncated content
- tool 이름이 client-side regex(`^[a-z0-9_]+$`)에 안 맞음 → tool 누락 가능
- MCP-Protocol-Version mismatch 시 downgrade 정책 명시 안 한 client는 silently 실패할 수 있음 → CoreMCP는 항상 응답 protocol version 명시
- 2025-11-25 client capabilities에 sampling/elicitation 미사용 client도 있음 → server capabilities omit으로 충분

---

## 13. Open Questions
1. ChatGPT가 dot tool name을 어떻게 표시하는지 실측 필요
2. Cursor가 DCR을 어느 버전부터 지원하는지
3. structuredContent를 Claude Code가 실제로 처리하는지
4. OpenClaw 사용 빈도 — 실제로 쓸 일이 있나?
5. Claude Code의 2025-11-25 실제 요청 시점/조건 확인
6. tool icons metadata가 Claude Code/ChatGPT UI에 실제 표시되는지 실측
