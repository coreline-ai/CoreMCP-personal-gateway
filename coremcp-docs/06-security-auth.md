# CoreMCP Security & Auth (Personal)

문서 버전: v1.0
작성일: 2026-05-11

---

## 1. 핵심 보안 원칙

1. 모든 `/mcp` request는 bearer token을 검증한다.
2. MCP session id는 인증 수단이 아니다.
3. CoreMCP access token을 downstream MCP에 절대 전달하지 않는다.
4. downstream credential은 vault(macOS Keychain 또는 fernet)에 저장한다.
5. 등록되는 downstream endpoint URL은 SSRF guard를 통과해야 한다.
6. tool description은 prompt injection / tool poisoning 공격면이므로 scan한다.
7. credential / token / Authorization 헤더는 로그에 redact한다.
8. 단일 사용자라도 위 7개 원칙은 그대로 적용한다.

## 2. Auth Model

### 2.1 Actors
| Actor | 설명 |
|---|---|
| User | 본인 1명 |
| External AI Client | Claude Code (Mac mini/MacBook), 옵션 ChatGPT/Cursor/OpenClaw |
| CoreMCP Resource Server | `/mcp` |
| Authorization Server | (옵션) 자체 단순 AS |
| Downstream MCP | proxy 대상 |

### 2.2 Token Types

| Token | Issuer | Audience | 용도 |
|---|---|---|---|
| Personal Bearer Token | CoreMCP bootstrap | CoreMCP /v1, /mcp | 정적 인증 (기본) |
| OAuth Access Token (JWT, 옵션) | 자체 AS | CoreMCP /mcp | client별 인증 (Phase P3+) |
| Refresh Token (옵션) | 자체 AS | CoreMCP AS | access 갱신 |
| One-Time Connection Token | CoreMCP | exchange endpoint | OpenClaw 등 |
| Downstream Credential | 외부 provider | downstream service | downstream 호출 |

### 2.3 Forbidden Flow
```text
Claude Code → CoreMCP: Authorization: Bearer <coremcp_token>
CoreMCP → Downstream: Authorization: Bearer <coremcp_token>    # 금지
```

### 2.4 Correct Flow
```text
Claude Code → CoreMCP: Authorization: Bearer <coremcp_token>
CoreMCP → Vault: resolve secret_ref
CoreMCP → Downstream: Authorization: Bearer <downstream_token>
```

---

## 3. Token Model (Dual — ADR-030)

CoreMCP는 두 종류의 personal token을 사용한다.

### 3.1 Admin Token (`cmcp_admin_*`)
- 용도: Web Admin / `/v1/*` REST API root 권한
- 저장: 파일 `~/.coremcp/admin-token` (chmod 600)
- DB 미저장
- 발급: bootstrap 시 자동 생성 또는 `python -c "import secrets; print('cmcp_admin_' + secrets.token_urlsafe(32))"`
- rotation: 파일 재작성 + API 프로세스 재시작 또는 SIGHUP
- 1개만 활성. 회전 시 즉시 invalidate.

### 3.2 Client Token (`cmcp_client_*`)
- 용도: `/mcp` endpoint 호출 (Claude Code Mac mini, MacBook 등 각각 별도)
- 저장: DB `personal_access_tokens.token_hash` (sha256). 평문은 응답에서 1회만 노출.
- external_connection 1개당 1~N개 binding
- revocable: external_connection 또는 token 단위로
- 발급:
  - Web UI "Add new client connection" → 신규 row + 평문 응답
  - 또는 `/v1/external-connections/exchange` (OTT)

### 3.3 인증 검증 흐름

```python
import hmac, hashlib

def verify_admin_bearer(presented: str) -> bool:
    expected = read_admin_token_file()
    return hmac.compare_digest(presented, expected)

def verify_client_bearer(presented: str) -> ExternalConnection | None:
    digest = hashlib.sha256(presented.encode()).hexdigest()
    row = db.fetch_one(
        "SELECT * FROM personal_access_tokens "
        "WHERE token_hash = ? AND revoked_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)",
        (digest,)
    )
    if not row:
        return None
    return resolve_external_connection(row.external_connection_id)
```

### 3.4 Endpoint 별 허용 token
| Endpoint 그룹 | admin token | client token |
|---|---|---|
| `/v1/*` REST admin | yes | no |
| `/mcp` MCP gateway | yes (fallback) | yes (primary) |
| `/.well-known/*` | n/a | n/a |
| `/health` `/ready` `/live` | no auth | no auth |

`/mcp`는 admin token도 받지만, 운영 권장은 client token. 둘 모두 동일 user_id 식별. external_connection_id는 client token만 채워짐.

### 3.5 Rotation
- admin: file 재작성, 모든 web 세션 무효화
- client: revoke API → 해당 external_connection에 연결된 token rows의 `revoked_at` 채움
- external_connection 삭제 시 ON DELETE CASCADE로 token 정리

### 3.6 보관 안전
- admin token 파일: chmod 600 + `.gitignore`
- client token 평문은 발급 1회 + 사용자에 안전 보관 책임
- Web UI localStorage에는 admin token만 (XSS 차단 — CSP)
- Tailscale 외부 노출 시 HTTPS 강제

---

## 4. OAuth 2.1 (옵션, Phase P3+)

### 4.0 AUTH_MODE (ADR-032)

| AUTH_MODE | 401 응답 | /.well-known/oauth-protected-resource | OAuth endpoints |
|---|---|---|---|
| `static_bearer` (default) | `WWW-Authenticate: Bearer realm="coremcp"` | **404 Not Found (default)** — `EXPOSE_RESOURCE_METADATA_IN_STATIC_MODE=true` 환경 변수로 활성 가능하지만 OAuth client 혼선 위험 | 404 |
| `oauth` | `WWW-Authenticate: Bearer realm="coremcp", resource_metadata="..."` | full RFC 9728 metadata | /oauth/* 활성 |

`AUTH_MODE` 환경 변수로 전환. UI에서 즉시 토글 미허용 (재시작 필요).

권장: static_bearer 모드에서는 `/.well-known/oauth-protected-resource`를 **노출하지 않는다** (404). OAuth client가 metadata를 보고 잘못된 flow를 시도하면 보안/사용성 모두 저하. metadata를 노출하려면 `EXPOSE_RESOURCE_METADATA_IN_STATIC_MODE=true`로 명시 opt-in. (ADR-032)

이하 §4.1 ~ §4.8은 `AUTH_MODE=oauth` 모드 활성 시에만 적용.

ChatGPT custom MCP / Cursor 등이 OAuth를 요구할 때 활성화. 단일 사용자라 단순화.

### 4.1 Protected Resource Metadata
```http
GET /.well-known/oauth-protected-resource
```
```json
{
  "resource": "http://localhost:8787/mcp",
  "authorization_servers": ["http://localhost:8787"],
  "scopes_supported": ["mcp:tools.read", "mcp:tools.call", "mcp:connections.manage"],
  "bearer_methods_supported": ["header"]
}
```

### 4.2 Authorization Server Metadata
자체 단순 AS. 단일 사용자라 consent screen은 자동 승인 옵션.
```json
{
  "issuer": "http://localhost:8787",
  "authorization_endpoint": "http://localhost:8787/oauth/authorize",
  "token_endpoint": "http://localhost:8787/oauth/token",
  "registration_endpoint": "http://localhost:8787/oauth/register",
  "revocation_endpoint": "http://localhost:8787/oauth/revoke",
  "jwks_uri": "http://localhost:8787/.well-known/jwks.json",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none"]
}
```

### 4.3 PKCE S256 mandatory
plain reject. code_verifier 길이 43-128.

### 4.4 OAuth Client Registration 우선순위 (ADR-036)

CoreMCP는 OAuth client를 다음 우선순위로 인식한다. 상위가 매치되면 하위는 skip.

| 순위 | 방식 | 위치 | 비고 |
|---|---|---|---|
| 1 | Pre-registered | §4.4.1 | 가장 안전, 본인이 사전 등록 |
| 2 | CIMD | §4.4.2 | dynamic client에 preferred |
| 3 | DCR fallback | §4.4.3 | CIMD 미지원 client 전용 |

처리 endpoint:
- pre-registered: DB의 oauth_clients lookup
- CIMD: client_id가 HTTPS URL이면 metadata fetch
- DCR: /oauth/register endpoint (fallback)

#### 4.4.1 Pre-registered Client

oauth_clients 테이블에 본인이 사전 등록한 client. 가장 안전.

발급 절차:
- Web Admin UI Settings/OAuth에서 client_name + redirect_uris 입력
- CoreMCP가 client_id (cmcp_oauth_client_*) + client_secret_hash 발급
- public client (token_endpoint_auth_method=none) 에는 secret 미발급

#### 4.4.2 Client ID Metadata Documents (CIMD) — Preferred (ADR-036)

client_id가 HTTPS URL인 경우 해당 URL을 fetch해 metadata 검증. ChatGPT Apps, Anthropic Claude Connectors가 권장.

흐름:
1. client가 authorize request에 `client_id=https://client.example.com/.well-known/oauth-client` 전달
2. CoreMCP가 해당 URL GET (SSRF guard, §7.5)
3. response 검증:
   - HTTPS 강제
   - size ≤ 32KB
   - content-type: application/json
   - JSON parse 성공
   - redirect_uris host가 client_id URL host와 일치 (또는 known good list)
   - grant_types / response_types 지원 범위
   - **fetched metadata의 `client_id` 필드 == 요청 URL (byte-exact, case-sensitive)** — 응답에서 client_id가 다른 URL을 가리키면 spoofing 시도, reject
   - **Content-Type**: `application/json`. `charset` 파라미터 허용 (예: `application/json; charset=utf-8`, `application/json;charset=UTF-8`). 그 외 reject (e.g. `text/html`, `application/xml`)
   - **TTL 정책**: CoreMCP는 **fixed 1h TTL** 적용 (downstream의 `Cache-Control` / `Expires` 헤더 무시). 이유:
     - downstream cache header를 신뢰하면 악성 metadata가 무한 캐시되어 invalidation 불가
     - 또는 매우 짧은 TTL로 DoS 유발
     - fixed TTL은 운영자가 예측 가능한 보안 boundary
4. metadata in-memory 캐시 (TTL 1h)
5. 동일 client_id 재요청 시 캐시 사용

장점:
- client URL이 곧 identity → brand impersonation 방어
- 운영자가 client를 사전 등록할 필요 없음
- metadata 변경 시 자동 반영 (캐시 만료 후)

Rate limit: CIMD fetch 30/hour/IP.

#### 4.4.3 Dynamic Client Registration (RFC 7591) — Fallback

CIMD 미지원 client용. 일부 ChatGPT/Cursor 버전이 사용.

흐름:
- POST /oauth/register (RFC 7591)
- client_metadata 검증 (redirect_uris HTTPS, scope subset, host known list)
- DCR rate limit 10/hour/IP
- client_secret은 public client(none)에 미발급
- 미사용 client는 90d 후 자동 정리

Rate limit: DCR 10/hour/IP.

DCR은 abuse 위험이 큼 (악성 client metadata 등록 가능). 운영자는 weekly DCR 등록 로그 검토 권장.

### 4.5 Resource Indicator (RFC 8707)
authorize와 token request 양쪽에 `resource=http://localhost:8787/mcp`. JWT aud claim과 일치 검증.

### 4.6 Token Format
RS256 JWT. claims:
```json
{
  "iss": "http://localhost:8787",
  "sub": "usr_local",
  "aud": "http://localhost:8787/mcp",
  "exp": ...,
  "jti": "...",
  "scope": "mcp:tools.read mcp:tools.call",
  "external_connection_id": "ext_..."
}
```

### 4.7 Revocation
- /oauth/revoke endpoint
- jti를 Redis 또는 in-memory denylist에 TTL=exp까지

### 4.8 단일 사용자 단순화
- consent screen: 본인이 한 번 "Always allow this client" 체크 → user_consents 행
- 신규 client_id는 첫 authorize에서 자동 dashboard 알림

---

## 5. One-Time Connection Token

OpenClaw 등 OAuth 미지원 local agent용. SaaS 원본과 동일하게 유지.

### 5.1 Properties
- prefix `cmcp_otk_`
- entropy 256-bit
- TTL 10분
- 1회 사용
- DB에는 hash만
- user_id + toolbox_id + client_type binding
- created_ip / user_agent 기록

### 5.2 Exchange
```text
User → Dashboard "Connect OpenClaw" 클릭
CoreMCP → DB: token_hash 저장
CoreMCP → User: connection_prompt 표시
User → OpenClaw 채팅 paste
OpenClaw → CoreMCP /v1/external-connections/exchange
CoreMCP → token verify (hash, expiry, used, IP/UA 비교)
CoreMCP → external_connections 생성
CoreMCP → OpenClaw: access_token (정적 또는 JWT)
```

### 5.3 보안
- hash compare는 hmac.compare_digest
- 사용/만료/revoke 상태 명시
- IP/UA mismatch는 strict 모드에서 reject, lenient 모드에서 warn

---

## 6. Downstream Credential Security

### 6.1 MVP 지원 타입
- none
- bearer_token
- api_key_header
- oauth_delegated: Phase P3+
- api_key_query: 차단

### 6.2 Vault Backend

#### macOS Keychain (권장)
```python
import keyring
keyring.set_password("coremcp", f"svc:{service_id}:bearer", token)
token = keyring.get_password("coremcp", f"svc:{service_id}:bearer")
```

장점: OS native, Mac mini 잠금 해제 시에만 접근. login.keychain 사용.

#### Fernet (대안)
```python
from cryptography.fernet import Fernet
from pathlib import Path

key = Path("~/.coremcp/secret.key").expanduser().read_bytes()
f = Fernet(key)
ciphertext = f.encrypt(b"ghp_xxx")
# DB의 secret_ref는 "fernet:<row_id>", 별도 secrets_blob 테이블에 ciphertext 저장
```

장점: file 기반, headless 환경 지원.
단점: master key가 평문 file. chmod 600 필수.

### 6.2.3 운영 모드 선택 (ADR-031)

`SECRET_BACKEND` 환경 변수로 결정:

| 모드 | 권장 시나리오 | 장점 | 단점 |
|---|---|---|---|
| `keychain` (default) | Mac mini **자동 로그인 활성** 환경 | OS native, iCloud Keychain sync, 권한 모델 우수 | 잠금 해제 안 된 경우 credential resolve 실패 (R-106) |
| `fernet` | **headless / 무인 운영** 환경, 자동 로그인 미설정 | login.keychain 의존 없음, 재부팅 후 즉시 사용 가능 | master key 파일 보관 부담, KMS 대비 약함 |

운영 가이드:
- 본인이 평소 Mac mini를 로그인 상태로 둠 → `keychain` 권장
- Mac mini가 무인 부팅 / 원격 SSH 위주 → `fernet` 권장
- 향후 SaaS 전환 시 → AWS KMS envelope 으로 마이그레이션

전환 절차 (keychain → fernet 또는 역방향):
1. 모든 credential을 web UI로 한 번 다시 입력 (재암호화)
2. SECRET_BACKEND 환경 변수 변경
3. API 재시작
4. 기존 secret_ref 정리 (cleanup job 또는 수동)

### 6.3 Storage Format
DB `service_credentials.secret_ref`:
- Keychain: `keychain:coremcp:svc_<id>:<credential_type>`
- Fernet: `fernet:<row_id>`

`masked_value`: `ghp_••••abcd` 같은 UI 표시용.

### 6.4 Rotation
1. 새 secret 입력
2. validation 실행
3. 성공 시 vault에 새 entry, secret_ref 교체
4. 이전 secret destroy (Keychain delete 또는 fernet row delete)
5. audit_log 기록

### 6.5 Display Rules
UI / API 응답:
```text
Bearer ••••abcd
X-API-Key ••••1234
```

평문은 등록/회전 form 외 어디에도 표시 안 함.

### 6.6 Logging Rules
금지:
- credential 평문
- Authorization 헤더 평문
- Set-Cookie 평문
- tool arguments 중 secret 같은 패턴

허용:
- credential_type
- masked_value
- secret_ref
- error_code

---

## 7. SSRF Protection

단일 사용자라도 본인이 잘못 입력하거나 악성 redirect에 걸릴 수 있음. 핵심만 유지.

### 7.1 URL 요구사항
- scheme: `https` 강제
- 예외: `http://localhost:*` 또는 `http://127.0.0.1:*` 또는 `http://[::1]:*` — 본인이 만든 로컬 fake MCP 테스트용 허용
- 예외: `http://*.local` (mDNS) — 옵션
- no userinfo (`user:pass@`)
- no fragment
- query string 허용하되 credential 포함 여부 검사
- port allowlist: 443 + localhost 임의 + 1024 이상 (옵션)

### 7.2 SSRF 정책 — Allowlist 기반 (ADR-033)

기본 정책: **모든 private/loopback/CGNAT는 차단, 명시 allowlist만 허용**.

```text
기본 차단:
  0.0.0.0/8
  10.0.0.0/8
  127.0.0.0/8 (loopback)
  100.64.0.0/10 (CGNAT / Tailscale)
  169.254.0.0/16 (link-local + metadata)
  172.16.0.0/12
  192.168.0.0/16
  198.18.0.0/15 (benchmarking)
  ::1/128
  fc00::/7
  fe80::/10
  ff00::/8
  169.254.169.254 (cloud metadata)
```

환경 변수로 예외 허용:

| 환경 변수 | 기본 | 의미 |
|---|---|---|
| `ALLOW_PRIVATE_DOWNSTREAM` | false | 사설망 downstream 등록 허용 (10/172/192) |
| `ALLOW_TAILSCALE_DOWNSTREAM` | false | 100.64.0.0/10 (Tailscale CGNAT) 허용 |
| `ALLOWED_PRIVATE_CIDRS` | empty | 명시 CIDR 콤마 구분 (예: `100.64.0.0/10,10.0.0.0/8`) |
| `ALLOW_LOOPBACK_DOWNSTREAM` | true | localhost http 허용 (fake-mcp 개발용) |

권장 운영:
- 본인이 만든 downstream MCP가 Mac mini 로컬: `ALLOW_LOOPBACK_DOWNSTREAM=true` (default)
- Tailscale 내부의 다른 머신을 downstream으로: `ALLOW_TAILSCALE_DOWNSTREAM=true`
- 본인 LAN의 NAS 등을 downstream으로: `ALLOWED_PRIVATE_CIDRS=10.0.0.0/8` 등 명시

`169.254.169.254` (cloud metadata)은 어떤 옵션으로도 허용 불가 (hard reject).

### 7.3 DNS Rebinding
- 등록 시 DNS resolve
- 매 outbound call 직전 재resolve
- IP 변경 시 safety 재검증
- max redirect = 0 (확정)

### 7.4 Localhost 정책
개인 프로젝트 특성상 localhost는 의도된 호출. 다음 조건에서 허용:
- 사용자가 명시적으로 `http://localhost:*` 입력
- DNS resolve 결과가 loopback (127/8 or ::1)
- 그 외 IP가 loopback으로 resolve되면 reject (DNS rebinding)

loopback 정책은 `ALLOW_LOOPBACK_DOWNSTREAM` 환경 변수로 토글 가능. CI/테스트 환경에서는 활성, 운영에서 의심스러우면 비활성.

### 7.5 Metadata Fetch SSRF (CIMD / OAuth Discovery)

CIMD client metadata, OAuth issuer discovery 등 CoreMCP가 외부 URL을 fetch하는 모든 경우에 SSRF guard 동일 적용:

- HTTPS 강제 (localhost 예외 없음 — 외부 메타데이터이므로)
- private IP / CGNAT / metadata IP 차단
- redirect = 0
- response size cap 32KB
- content-type 검증: `application/json`만 허용
- fetch timeout 5s
- response 캐시 TTL 1h (서명 검증 없이 신뢰하면 안 됨)

악성 client가 CIMD URL로 내부 metadata endpoint를 가리키게 하면 cloud credentials 누설 위험. 이 fetch도 일반 downstream SSRF guard와 동일한 보호 적용.

CIMD response 검증의 추가 조건 (client_id byte-exact match, content-type charset 허용, TTL fixed 1h)은 §4.4.2 참조.

---

## 8. Tool Metadata Security

### 8.1 Tool Poisoning Risk
악성 MCP가 tool description에 LLM 조작 문구를 넣을 수 있다. 본인이 등록한 서비스라도 downstream 측에서 schema가 바뀔 수 있으므로 scan.

### 8.2 Scanner Rules
다음 패턴 매칭 시 warning + risk_level 상향:
- "ignore previous instructions"
- "reveal secrets"
- "always call this tool"
- "send tokens"
- "exfiltrate"
- "hidden instruction"
- "system prompt"
- 매우 긴 description (>1024 chars)
- markdown link to unknown domain
- base64 encoded suspicious blob

### 8.3 Unicode / Homoglyph
- NFKC normalize
- zero-width strip (U+200B, U+200C, U+200D, U+FEFF)
- RTL override 제거 (U+202E)
- Cyrillic 'а' vs Latin 'a' 같은 confusable은 경고 (Unicode Confusables list)
- emoji 허용

### 8.4 결과
scan 결과는 `service_tools.metadata_scan` JSON에 저장:
```json
{
  "scanned_at": "...",
  "rules_matched": ["ignore_previous"],
  "risk_level": "high",
  "warnings": ["Description contains 'ignore previous instructions'"]
}
```

UI에 경고 표시. critical risk는 etected → tool disabled 옵션.

---

## 9. Session Security

### 9.1 MCP Session ID
- cryptographically random (uuid4 또는 secrets.token_urlsafe(16))
- user_id binding (in-memory dict 또는 DB)
- expiry 24h
- 모든 request에서 bearer token 재검증
- session id를 인증으로 사용 금지

### 9.2 In-Memory Store
```python
# 단일 process 기준
sessions: dict[str, McpSession] = {}
# key: session_id, value: {user_id, external_connection_id, protocol_version, created_at, last_seen_at, expires_at}
```

multi-process 확장 시 Redis 또는 DB.

### 9.3 Invalid Session
- missing required session id (initialize 이후): 400
- expired: 404
- user mismatch (token에 binding된 user와 session의 user 불일치): 403 + audit alert

---

## 10. Rate Limiting

단일 사용자라 per-user 의미 없음. 다음을 적용:

| Target | Limit | Scope |
|---|---|---|
| `/mcp` POST | 600/min | global |
| tools/call | 300/min | global |
| service validation | 60/hour | global |
| one-time token create | 10/hour | global |
| token exchange | 30/min | global per IP |
| DCR (옵션) | 30/hour | global per IP |
| downstream call to service X | 300/min | per service |
| client token 발급 | 30/hour | per admin token |
| client token 검증 실패 | 60/min | global (brute force 감지) |
| admin token 검증 실패 | 10/min | global |

목적은 본인 실수(loop) 또는 외부 공격(Tailscale 노출 시) 방어.

Error mapping 정책은 ADR-034 (Error Mapping = Protocol vs Tool Result Separation) 준수.

---

## 11. Audit Events

다음 액션을 audit_logs에 기록:
```text
user.bootstrap
user.token_rotate
service.create
service.update
service.delete
service.validate
service.refresh_tools
credential.create
credential.rotate
credential.delete
toolbox.create
toolbox.item.add
toolbox.item.remove
toolbox.item.enable
toolbox.item.disable
external_connection.create
external_connection.revoke
connection_token.create
connection_token.exchange
mcp.tools.call
oauth.client_register (옵션)
oauth.token_revoke (옵션)
security.ssrf_blocked
security.token_invalid
security.scanner_warning
debug_trace.enable
debug_trace.disable
admin_token.rotate
client_token.issue
client_token.revoke
auth_mode.change
secret_backend.change
ssrf_policy.change
oauth.cimd_fetch
oauth.cimd_fetch_failed
security.origin_blocked
security.metadata_ssrf_blocked
```

---

## 12. CORS / Origin

`/mcp` POST/GET의 Origin allowlist:
- `http://localhost:*`
- `http://127.0.0.1:*`
- `http://*.local`
- Tailscale 도메인 (환경 변수)

`/v1/*` REST API는 admin Web UI origin만:
- `http://localhost:3000`
- 옵션 Tailscale

CORS preflight에 `Authorization`, `Idempotency-Key`, `X-Request-Id` allow.

### 12.4 Invalid Origin 응답

CORS allowlist에 없는 Origin에서 온 request:
- preflight (OPTIONS): 403 Forbidden + audit_log `security.origin_blocked`
- 실제 request: 403 + WWW-Authenticate 없음

`Origin` 헤더 누락 (curl, server-to-server)은 통과 — Origin 검증은 브라우저 한정 방어층.

`Origin`이 `null` (sandboxed iframe, file:// 등)도 403.

`Referer` 헤더 기반 추가 검증은 미적용 (신뢰할 수 없음).

---

## 13. TLS / Transport

### 13.1 Local
`http://localhost:8787`은 평문 허용 (loopback이라 도청 risk 무).

### 13.2 외부 노출 (Tailscale)
- Tailscale Serve로 TLS termination
- 또는 Caddy reverse proxy + Tailscale auto cert
- 또는 Cloudflare Tunnel (Cloudflare에서 TLS)

### 13.3 Downstream HTTPS 강제
downstream MCP 호출은 HTTPS 강제 (localhost 예외 §7.4).

---

## 14. Acceptance Checklist

- [ ] `/mcp` 모든 request bearer 검증
- [ ] Bearer 비교는 hmac.compare_digest
- [ ] CoreMCP token이 downstream으로 전달 안 됨 (integration test)
- [ ] credential 평문이 DB에 없음 (DB dump grep)
- [ ] credential 평문이 logs에 없음 (regex redact)
- [ ] SSRF guard private IP + DNS rebinding 차단
- [ ] localhost http는 허용되되 외부 host의 loopback resolve는 차단
- [ ] tool metadata scanner regex 매칭
- [ ] Unicode NFKC + zero-width strip
- [ ] session id가 bearer 없이는 무효
- [ ] one-time token hash 저장
- [ ] one-time token 1회 사용 후 invalid
- [ ] audit events 빠짐없이 기록
- [ ] rate limit (global) 작동
- [ ] Origin / CORS 정책 적용
- [ ] admin token은 DB에 없음 (grep)
- [ ] client token은 hash만 DB에 있음
- [ ] external_connection revoke 시 token CASCADE
- [ ] AUTH_MODE 전환 후 401 응답 헤더 정상
- [ ] static_bearer 모드에서 resource_metadata에 authorization_servers 없음
- [ ] ALLOWED_PRIVATE_CIDRS 미설정 시 Tailscale IP 차단
- [ ] ALLOWED_PRIVATE_CIDRS 설정 시 명시 대역만 허용
- [ ] keychain ↔ fernet 전환 절차 검증
- [ ] CIMD fetch가 SSRF guard 통과
- [ ] CIMD 응답 size > 32KB reject
- [ ] CIMD content-type application/json 강제
- [ ] Invalid Origin 403 + audit
- [ ] metadata fetch redirect 0
- [ ] static_bearer 모드에서 `/.well-known/oauth-protected-resource`가 404 응답 (default)
- [ ] `EXPOSE_RESOURCE_METADATA_IN_STATIC_MODE=true` 시에만 응답
- [ ] CIMD fetched metadata의 client_id가 요청 URL과 byte-exact 일치 검증
- [ ] CIMD content-type `application/json; charset=utf-8` 같은 charset 허용
- [ ] CIMD TTL fixed 1h (downstream cache header 무시)

---

## 15. 개인 컨텍스트라 제외하는 영역

production_docs_donotuse/06-security-auth.md에 있지만 본 프로젝트에 제외:

- MFA / Email verify / Password reset (가입자 없음)
- Account Takeover Defense (본인 머신)
- per-user rate limit (per-process global로 대체)
- refresh token family detection (refresh 미사용 시 무관)
- Bug Bounty / Vulnerability Disclosure
- Compliance (16-compliance.md 제외)
- KMS envelope encryption (macOS Keychain 또는 fernet으로 대체)
- Key rotation policy 의 일부 (JWKS rotation은 OAuth 활성 시만, KEK 개념 무)
- CI Supply chain (개인 dev 환경)
- SOC2 / ISO27001 checklist
- 자체 OAuth Authorization Server full 구현은 default OFF (`AUTH_MODE=static_bearer`)
- 자체 KMS / cross-region 키 관리 (SaaS 전환 후)
- OAuth client registration (Pre-registered / CIMD / DCR)은 AUTH_MODE=oauth 활성 시(P3+)에 발효. static_bearer 모드(default)에서는 무관.
- CIMD First, DCR Fallback 정책(ADR-036)이 적용되며, 본인이 자체 OAuth AS를 운영할 때만 의미.
- error mapping 분류는 ADR-034 참조 (07-mcp-proxy-spec.md §8.3 매핑 표)

원래 SaaS 단계로 확장하면 `15-future-saas-migration.md` 참조.
