# CoreMCP Personal Implementation (Mac mini)

문서 버전: v0.1
작성일: 2026-05-11
대상: 본인 1명, Mac mini 단일 호스트, 한국어 우선
관계 문서: 다른 모든 문서(00 ~ 17)는 "이상적 SaaS 설계" reference. 본 문서는 실제 구현 명세.

---

## 1. 목적

이 문서는 CoreMCP를 **본인이 본인 Mac mini에서 사용**하기 위한 최소 구현 명세다.
SaaS 문서팩(00~17)의 80%는 다인 사용/외부 노출/규제 대응을 위한 것이며, 개인 프로젝트에는 불필요하다.

본 문서가 우선한다. 다른 문서와 충돌할 경우 본 문서가 정답이다.

---

## 2. 사용자 시나리오

```text
1. Mac mini가 24/7 작동 중
2. Mac mini의 CoreMCP가 http://localhost:8787/mcp 또는 Tailscale URL로 노출
3. 같은 머신 또는 다른 머신의 Claude Code가 CoreMCP에 연결
4. Claude Code는 GitHub MCP, Notion MCP, 자체 만든 MCP 등을 CoreMCP 통해 사용
5. 본인이 웹 UI에서 새 MCP 등록, toolbox 관리, 로그 확인
```

핵심: 사용자도 본인, 관리자도 본인, 보안 모델도 "내 머신 보호" 수준.

---

## 3. 드롭하는 영역 (SaaS 문서에 있지만 구현 안 함)

| 영역 | 사유 |
|---|---|
| OAuth 2.1 / DCR / PKCE / Resource Indicator / JWKS | 단일 사용자 → 정적 bearer token 1개로 충분 |
| Logto / 외부 OIDC provider | OAuth AS 자체가 불필요 |
| AWS KMS envelope encryption | 로컬이라 KMS API 불가능 → macOS Keychain 또는 fernet |
| PostgreSQL Row-Level Security | 단일 사용자 → 격리 대상 없음 |
| workspace / workspace_members | 멤버 없음 |
| MFA / Account Takeover Defense / session fingerprint | 본인만 접근 |
| Email verification / Password reset / Sign-up flow | 가입자 없음 |
| Right-to-Erasure / Data Export | 본인 데이터 본인이 관리 (`rm -rf ~/.coremcp/`) |
| GDPR / CCPA / 개인정보보호법 / SOC2 / ISO27001 | 처리 대상 없음 |
| Multi-region / DR / cross-region backup | 단일 호스트 |
| per-user rate limiting | 본인이 본인 제어 |
| Bug bounty / Vulnerability disclosure | 공개 서비스 아님 |
| Status page / Incident response / Postmortem | 본인 알림 |
| Marketplace / public registry / verified badge | 등록자 본인만 |
| Connected clients revoke API | 본인 직접 관리 |
| Audit log export / SIEM / NDJSON export | 로그 파일 `tail -f`로 충분 |
| Billing / Stripe / quota counter / plan tier | 과금 없음 |
| Sentry / OpenTelemetry collector | 로컬 file log + console |
| 17-mcp-client-profiles의 ChatGPT/Cursor/Windsurf 호환성 | Claude Code 하나만 |
| Workspace switcher / member invitation | 멤버 없음 |
| ToS / Privacy Policy / DPA / Subprocessor list | 외부 사용자 없음 |

---

## 4. 유지하는 영역 (개인 프로젝트에도 가치)

| 영역 | 사유 |
|---|---|
| `/mcp` endpoint + JSON-RPC handler | 핵심 |
| MCP service registry + tool schema cache | 핵심 |
| Toolbox 개념 | 여러 MCP 정리에 유용 |
| Tool alias / proxy execution | 핵심 |
| Downstream credential 암호화 저장 (macOS Keychain) | GitHub PAT 등을 평문 저장하면 안 됨 |
| SSRF guard (간소화) | 로컬에서도 실수 방지 |
| Schema hash / drift detection | downstream 업데이트 추적 |
| 파일 로그 (structlog → stdout + file) | 디버깅 |
| `notifications/tools/list_changed` emit | tools/list 갱신 UX |
| 정적 bearer token auth | 단일 사용자 인증 |
| 웹 UI (Next.js) | 본인용 admin console |
| Tool invocation log (SQLite) | 디버깅 |

---

## 5. 기술 스택 (간소화)

### 5.1 Backend
```text
- Python 3.12+
- FastAPI (단일 프로세스)
- Pydantic v2
- SQLAlchemy 2.0 (SQLite or local PostgreSQL)
- Alembic (선택)
- httpx async (downstream MCP 호출)
- structlog → stdout + ~/.coremcp/logs/coremcp.log
- keyring (macOS Keychain 접근)
- python-jose 또는 PyJWT (정적 bearer token 검증, 단순)
```

### 5.2 Frontend
```text
- Next.js 15+ (App Router)
- TypeScript
- Tailwind CSS
- shadcn/ui
- TanStack Query
- 한국어 단일 locale (i18n 라이브러리 불필요)
```

### 5.3 데이터 저장
```text
권장: SQLite (~/.coremcp/db.sqlite3)
- 단일 사용자, 단일 프로세스에 충분
- 백업: ~/.coremcp/ 디렉토리 자체를 iCloud/Time Machine에 동기화

대안: PostgreSQL 15 via Docker
- SQLAlchemy의 dialect만 바꾸면 됨
- 추후 SaaS 전환 시 마이그레이션 비용 낮음
```

### 5.4 Cache
```text
권장: Python in-memory dict + TTL
- single process라 pub/sub 불필요
- TTL은 cachetools.TTLCache 또는 자체 구현

대안: Redis via Docker
- 미래 multi-process 확장 시
```

### 5.5 Worker
```text
권장: FastAPI BackgroundTasks 또는 asyncio TaskGroup
- validation/refresh job은 즉시 비동기 실행
- Arq/Celery 불필요

대안: Arq + Redis
- 미래 worker 분리 시
```

### 5.6 Secret 저장
```text
권장: macOS Keychain (keyring 라이브러리)
- code:
    keyring.set_password("coremcp", f"service:{service_id}:bearer", token)
    token = keyring.get_password("coremcp", f"service:{service_id}:bearer")
- Mac mini 잠금 해제 상태에서만 접근

대안: fernet symmetric encryption
- master key는 ~/.coremcp/secret.key (chmod 600)
- code:
    from cryptography.fernet import Fernet
    f = Fernet(Path("~/.coremcp/secret.key").read_bytes())
    ciphertext = f.encrypt(b"ghp_xxx")
```

---

## 6. 디렉토리 구조 (간소화)

```text
coremcp/
├── apps/
│   ├── api/
│   │   ├── coremcp/
│   │   │   ├── main.py              # FastAPI app
│   │   │   ├── config.py
│   │   │   ├── db.py                # SQLAlchemy + SQLite
│   │   │   ├── auth.py              # static bearer token check
│   │   │   ├── mcp_gateway/
│   │   │   │   ├── routes.py        # /mcp POST/GET
│   │   │   │   ├── handlers.py      # initialize/tools/list/tools/call
│   │   │   │   └── session.py       # in-memory session map
│   │   │   ├── registry/
│   │   │   │   ├── routes.py        # /v1/mcp-services
│   │   │   │   ├── service.py
│   │   │   │   ├── validation.py    # initialize/tools/list 검증
│   │   │   │   └── ssrf.py          # 간소화된 URL guard
│   │   │   ├── toolbox/
│   │   │   │   ├── routes.py        # /v1/toolboxes
│   │   │   │   └── service.py
│   │   │   ├── proxy/
│   │   │   │   ├── client.py        # httpx async downstream client
│   │   │   │   └── executor.py
│   │   │   ├── credentials/
│   │   │   │   └── vault.py         # keyring wrapper
│   │   │   └── logging.py
│   │   ├── alembic/                 # 선택 (SQLite도 migration 가능)
│   │   └── tests/
│   └── web/
│       ├── app/
│       │   ├── page.tsx             # Dashboard
│       │   ├── services/
│       │   ├── toolbox/
│       │   ├── logs/
│       │   └── settings/
│       ├── components/
│       └── lib/
└── docker-compose.yml               # 선택 (Postgres/Redis 사용 시만)
```

---

## 7. 핵심 인증 단순화

### 7.1 Static Bearer Token
SaaS 문서의 OAuth 2.1 / JWT / DCR 전체를 다음으로 대체:

```python
# config.py
COREMCP_PERSONAL_TOKEN = os.environ["COREMCP_PERSONAL_TOKEN"]
# 또는 ~/.coremcp/token 파일

# auth.py
def verify_bearer(authorization: str | None) -> None:
    if not authorization:
        raise HTTPException(401, "auth_required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(401, "invalid_scheme")
    if not hmac.compare_digest(token, COREMCP_PERSONAL_TOKEN):
        raise HTTPException(401, "invalid_token")
```

### 7.2 Claude Code 등록
```bash
# Mac mini 내부 등록
claude mcp add --transport http coremcp http://localhost:8787/mcp \
  --header "Authorization: Bearer $(cat ~/.coremcp/token)"

# 다른 머신에서 (Tailscale 사용 시)
claude mcp add --transport http coremcp https://macmini.tail-scale.ts.net/mcp \
  --header "Authorization: Bearer <token>"
```

### 7.3 토큰 생성
```bash
# 최초 1회
python -c "import secrets; print('cmcp_personal_' + secrets.token_urlsafe(32))" \
  > ~/.coremcp/token
chmod 600 ~/.coremcp/token
```

### 7.4 토큰 rotation
필요 시 위 명령으로 새 토큰 생성 → Claude Code 재등록. 자동화 불필요.

---

## 8. 외부 노출 옵션

### 8.1 옵션 A: localhost only (가장 안전)
- Claude Code도 Mac mini 위에서 실행
- 외부 노출 없음
- 작업 환경이 Mac mini 1대로 고정될 때

### 8.2 옵션 B: Tailscale (권장)
- Tailscale tailnet 가입 후 Mac mini를 노드로 등록
- Magic DNS: `http://macmini:8787/mcp` 또는 `https://macmini.ts.net/mcp`
- HTTPS는 Tailscale Serve 또는 caddy reverse proxy로 추가
- 본인 기기에서만 접근, 외부 차단

### 8.3 옵션 C: Cloudflare Tunnel (선택)
- `cloudflared tunnel`로 public URL 노출
- Cloudflare Access로 Google OIDC 추가 인증 (선택)
- Mac mini 포트포워딩 없이 외부 접근

### 8.4 권장
**8.2 Tailscale** — 본인이 보유한 기기에서만 접근 가능, 설치 5분, 무료.

---

## 9. 데이터 모델 (간소화)

05-database-schema.md를 다음과 같이 간소화:

### 9.1 사용 안 하는 테이블
- workspaces, workspace_members (단일 사용자)
- user_consents, oauth_clients, oauth_authorization_codes, oauth_access_tokens, oauth_refresh_tokens, jwks_keys (OAuth 미사용)
- api_keys (정적 bearer token 1개로 대체)
- billing_usage_counters (과금 없음)
- connection_tokens (one-time token 미사용)

### 9.2 사용하지만 단순화하는 테이블
- users: 단 1행. 마이그레이션 시 `INSERT INTO users (id, email) VALUES (..., 'me@local')`
- external_connections: 단 1~2행 (Claude Code Mac mini용, Claude Code laptop용 등)
- mcp_sessions: in-memory dict로 대체 가능 (DB 미저장)

### 9.3 유지 테이블
- mcp_services
- service_tools
- tool_aliases
- service_validation_runs
- toolboxes (1개, is_default=true)
- toolbox_items
- user_service_connections (또는 service_credentials 중 1개만 사용)
- tool_invocations (디버깅용)
- audit_logs (기본 동작만)

### 9.4 SQLite 호환 변경
- `UUID DEFAULT gen_random_uuid()` → `TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16))))`
- `TIMESTAMPTZ` → `TIMESTAMP` (SQLite는 timezone 없음, UTC 일관 사용)
- `JSONB` → `JSON` (또는 TEXT)
- `INET` → `TEXT`
- `ARRAY` → `TEXT` (콤마 구분) 또는 JSON
- `PARTITION BY` → 없음 (단일 user는 무관)
- `ENUM` → `CHECK (col IN (...))`
- RLS 정책 → 미적용

PostgreSQL 사용 시 위 변환 불필요.

---

## 10. SSRF Guard 간소화

06-security-auth.md §7 전체 IP allow/deny + DNS rebinding + egress proxy → 다음으로 축소:

```python
PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
]

def check_url_safe(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        # 단, localhost http는 허용 (로컬 fake MCP 테스트용)
        if not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
            raise ValueError("unsafe_url: https required")
    # DNS resolve 후 private network 체크는 옵션
```

이유: 본인이 본인이 등록하는 거라 악의적 시나리오가 사실상 없음. 단 로컬 fake MCP는 허용해야 개발 편함.

---

## 11. 캐시 정책 간소화

```python
# Tool catalog cache: in-memory dict
from cachetools import TTLCache

catalog_cache: dict[str, dict] = TTLCache(maxsize=100, ttl=3600)
# key: f"catalog:{user_id}" (user_id 사실상 1개)

def invalidate_user(user_id: str):
    catalog_cache.pop(f"catalog:{user_id}", None)
```

Redis pub/sub 불필요. single process라 in-process invalidate로 충분.

---

## 12. Worker 간소화

Arq 대신 FastAPI BackgroundTasks:

```python
@app.post("/v1/mcp-services")
async def create_service(payload: CreateService, bg: BackgroundTasks):
    service = await registry.create(payload)
    bg.add_task(run_validation, service.id)
    return service
```

긴 작업(validation, schema refresh)도 단일 프로세스 내에서 asyncio로 처리. timeout 35s이므로 부담 없음.

---

## 13. Frontend 간소화

08-frontend-ux.md의 11개 페이지 중 필요한 것만:

### 13.1 구현
- `/` Dashboard (default toolbox + recent calls)
- `/services` MCP services 목록
- `/services/new` 등록 form
- `/services/[id]` 상세 + validation report + tools
- `/toolbox` toolbox 관리 (enable/disable)
- `/logs` tool_invocations + audit_logs 단일 뷰
- `/settings` token rotate, 기본 정보

### 13.2 미구현
- /pricing
- /billing
- /sign-up, /login (정적 token으로 dashboard에 BasicAuth 또는 token 입력)
- /marketplace (Phase 4 기능)
- /playground (Phase 2 기능)
- /developer (Phase 2 기능)
- /workspace switcher
- Connected Clients revoke (수동 token 회전으로 대체)

### 13.3 Web UI 인증
```text
가장 단순: Dashboard 진입 시 token 입력 → localStorage 저장 → API 호출에 헤더 첨부
또는: Dashboard를 localhost:3000으로만 띄우고 인증 없음
```

---

## 14. 로깅

```python
# logging.py
import structlog, sys
from pathlib import Path

log_path = Path.home() / ".coremcp" / "logs" / "coremcp.log"
log_path.parent.mkdir(parents=True, exist_ok=True)

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

# tail -f ~/.coremcp/logs/coremcp.log | jq
```

redaction은 기본 패턴만 (authorization, token, api_key). 본인이 본인 로그를 보는 것이므로 엄격할 필요 없음.

---

## 15. 실행 방법

### 15.1 최초 setup
```bash
# 디렉토리
mkdir -p ~/.coremcp/{logs,data}
chmod 700 ~/.coremcp

# 토큰
python -c "import secrets; print('cmcp_personal_' + secrets.token_urlsafe(32))" \
  > ~/.coremcp/token
chmod 600 ~/.coremcp/token

# repo
git clone <repo> coremcp
cd coremcp

# backend
cd apps/api
uv venv && source .venv/bin/activate  # 또는 poetry
uv pip install -e .
alembic upgrade head  # SQLite or Postgres

# frontend
cd ../web
pnpm install
pnpm build
```

### 15.2 daemon 실행 (Mac mini 24/7)
```bash
# launchd plist: ~/Library/LaunchAgents/com.coremcp.api.plist
launchctl load ~/Library/LaunchAgents/com.coremcp.api.plist
launchctl load ~/Library/LaunchAgents/com.coremcp.web.plist

# 또는 단순히 tmux/screen 세션
tmux new -s coremcp 'cd apps/api && uvicorn coremcp.main:app --host 0.0.0.0 --port 8787'
```

### 15.3 백업
```bash
# 주기적 SQLite 백업
sqlite3 ~/.coremcp/data/db.sqlite3 ".backup ~/.coremcp/data/db.backup.$(date +%Y%m%d).sqlite3"

# 또는 ~/.coremcp/ 전체를 iCloud Drive 또는 Time Machine에 포함
```

---

## 16. 마일스톤 (개인 프로젝트용)

09-implementation-plan.md의 11개 milestone은 SaaS 출시 기준. 개인 프로젝트는 다음 4단계로 충분:

### Phase P0: Vertical Slice (1주)
- FastAPI + SQLite 셋업
- `/mcp` POST endpoint (initialize/tools/list/tools/call)
- hardcoded user_id, hardcoded toolbox
- fake downstream MCP로 tools/call 동작 확인
- Claude Code 연결 테스트 (`--header` 방식)

Exit: Mac mini의 Claude Code가 CoreMCP 통해 fake tool 호출 성공.

### Phase P1: Real Downstream (1주)
- mcp_services 등록 API + URL safety guard (간소화)
- validation worker (BackgroundTask) → tools/list 캐시
- credential vault (keyring)
- 실제 GitHub MCP 또는 Notion MCP 1개 연결
- Claude Code에서 실제 tool 호출 성공

Exit: 본인이 등록한 실제 MCP를 Claude Code에서 사용 가능.

### Phase P2: Web UI (1~2주)
- Next.js 셋업 (shadcn/ui)
- Dashboard / Services / Toolbox / Logs 페이지
- token 입력 → localStorage
- 한국어 단일 locale

Exit: 웹에서 MCP 등록/관리/로그 확인.

### Phase P3: 운영 (1주)
- launchd로 daemon 등록
- SQLite 백업 cron
- 로그 rotation
- Tailscale 연동 (선택)
- Mac mini 재부팅 후 자동 시작 확인

Exit: 무인 운영 가능.

총 4~5주, 1인 작업 기준.

---

## 17. 본 문서가 무효화하는 SaaS 문서 영역

다음 영역은 **읽지 않아도 됨** (개인 프로젝트에 무관):

- 01-prd.md §4.2 Persona B (MCP 서버 개발자 marketplace 등록), §4.3 Persona C (팀 관리자), §4.4 Persona D (multi-client)
- 01-prd.md §6.2 Should Have, §6.3 Could Have, §6.4 Won't Have (대부분 개인용에는 무관)
- 01-prd.md FR-008(Connected Clients revoke), FR-009(One-time token), FR-013(Email verify), FR-014~016 전체
- 02-trd.md §11 배포 구조 (Cloudflare/WAF/Load Balancer)
- 04-api-spec.md §2.3 / §8 OAuth 전체, External Connection API 전체, /me 인증 관련
- 05-database-schema.md §2 workspaces, §10.2 audit_logs partition, §11 OAuth tables 전체, §15 RLS 정책
- 06-security-auth.md §3~§5 OAuth/DCR/PKCE/Resource Indicator 전체, §10 rate limit per-user, §13~§17 Account Security/MFA/Bug Bounty
- 07-mcp-proxy-spec.md §6.4 capabilities forwarding (sampling/elicitation reject는 그대로 적용)
- 08-frontend-ux.md §3.12~§3.16 신규 페이지 (Sign up/MFA/Billing/Pricing/Workspace)
- 09-implementation-plan.md §4 Milestone 0~10 (본 문서 §16이 대체)
- 11-risk-review.md R-013/R-016/R-017/R-018/R-019/R-020 (개인용 무관)
- 12-operations-observability.md §11 Incident Response/Postmortem/Status Page
- 14-pricing.md 전체
- 16-compliance.md 전체
- 17-mcp-client-profiles.md ChatGPT/Cursor/Windsurf 섹션 (Claude Code만 유지)

---

## 18. 미래 SaaS 전환 시 체크리스트

언젠가 다인 사용으로 확장하기로 결정하면 다음을 진행:

- [ ] ADR-023/024/025를 Superseded로 표시 + 새 ADR 작성
- [ ] OAuth AS 도입 (Logto self-host, ADR-011 재활성)
- [ ] PostgreSQL 마이그레이션 (SQLite → PG)
- [ ] RLS 정책 활성화 (ADR-017)
- [ ] workspace / workspace_members 활성화
- [ ] billing 시스템 (Stripe + 14-pricing.md 재검토)
- [ ] Privacy Policy / ToS / DPA 작성 (16-compliance.md)
- [ ] DCR / PKCE / Resource Indicator (ADR-022)
- [ ] Multi-region 검토 (16-compliance.md §6)

본 문서의 데이터 모델 간소화(§9)는 위 전환을 어렵게 만들 수 있으니, **확장 가능성이 있다면 처음부터 PostgreSQL + UUID + JSONB 사용**을 권장한다.

---

## 19. Open Questions (개인 프로젝트 한정)

1. 웹 UI는 Mac mini의 같은 process에서 serve할지, 별도 Next.js dev server로 운영할지?
2. SQLite vs PostgreSQL 어느 쪽으로 시작할지? (마이그레이션 비용 vs 운영 편의)
3. 로그 retention 며칠? 본인 디스크 사정.
4. Mac mini 외 다른 기기(MacBook)에서도 본인이 접근할지? → Tailscale 필요 여부.
5. 본인이 만든 MCP server를 같은 Mac mini에 띄울 건가? → CoreMCP가 자기 자신의 다른 process를 downstream으로 등록 가능.
6. Claude Code 외에 Codex / Cursor도 본인이 쓰는가? → 쓰면 17-mcp-client-profiles 일부 부활.

---

## 20. 요약

- 본 문서가 **현재 진행 중인 개인 프로젝트의 정본**
- 다른 모든 문서(00~17)는 reference, 학습 자료, 미래 확장 청사진
- 충돌 시 본 문서 우선
- 4~5주 개발 일정으로 Mac mini에서 본인이 본인 도구함 운영 목표
- ADR-023/024/025 Accepted 상태로 본 결정을 박아둠
