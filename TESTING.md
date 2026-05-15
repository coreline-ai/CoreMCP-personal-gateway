# TESTING.md

CoreMCP 테스트 기준과 실행 명령입니다.

## Philosophy

테스트는 CoreMCP가 개인용 보안 gateway로 안전하게 동작하는지 확인하는 최소 안전망입니다. 특히 token boundary, MCP protocol negotiation, downstream proxy, icon metadata shape는 회귀 테스트로 고정합니다.

## Test commands

```bash
# Python API + fake downstream tests
make test

# Demo MCP suite tests
make test-demo

# Demo MCP suite run
make demo-run

# Web/package type and lint checks
pnpm lint
pnpm build
pnpm test

# In-process API smoke
make smoke

# Ops script smoke
tmpdir="$(mktemp -d)" && mkdir -p "$tmpdir/data" && sqlite3 "$tmpdir/data/coremcp.sqlite3" 'create table smoke(id integer);'
COREMCP_DATA_DIR="$tmpdir" infra/scripts/backup-sqlite.sh
plutil -lint infra/launchd/com.coremcp.api.plist infra/launchd/com.coremcp.web.plist infra/launchd/com.coremcp.backup.plist infra/launchd/com.coremcp.logrotate.plist infra/launchd/com.coremcp.refresh.plist
infra/scripts/coremcp-launchctl.sh load
infra/scripts/ops-smoke.sh

# Scheduled refresh smoke (no registered services)
tmpdir="$(mktemp -d)" && cd apps/api && \
  COREMCP_ADMIN_TOKEN_VALUE=refresh-smoke \
  COREMCP_ADMIN_TOKEN_FILE="$tmpdir/admin-token" \
  COREMCP_DB_PATH="$tmpdir/coremcp.sqlite3" \
  COREMCP_SECRET_BACKEND=fernet \
  COREMCP_SECRETS_FILE="$tmpdir/secrets.json" \
  FERNET_KEY_FILE="$tmpdir/secrets.key" \
  uv run python -m coremcp.refresh

# Web route smoke (requires npx or PWCLI)
infra/scripts/web-route-smoke.sh

# Web UI smoke (requires running API/Web + at least one registered service)
make ui-smoke-install  # one-time browser install
make ui-smoke

# Codex CLI MCP smoke (non-LLM; requires make run first)
make codex-install
make codex-smoke

# Alembic migration smoke
cd apps/api && COREMCP_DB_PATH="$(mktemp -d)/coremcp.sqlite3" uv run alembic upgrade head

# Full local verification
make test && pnpm lint && pnpm build && pnpm test && make smoke && make codex-smoke && make ui-smoke && git diff --check
```

## Web UI smoke

`make ui-smoke`는 Web Admin UI ↔ API ↔ registered MCP service ↔ Playground ↔ Logs 흐름을 headless Chromium으로 검증합니다.

전제:
- `make run-local` 또는 launchd로 API(`:8787`)와 Web(`:3003`)이 떠 있어야 합니다.
- `~/.coremcp/admin-token`이 존재해야 합니다.
- 하나 이상의 service가 등록되어 있고 Playground에서 호출 가능한 read-only 성격 tool이 있어야 합니다.

주요 종료 코드:

| Exit | 의미 |
|---:|---|
| 0 | 정상 |
| 10 | Dashboard 최신 데이터 로드 실패 |
| 11 | Services 페이지 service 0개 |
| 12 | Playground tool 0개 |
| 13 | Tool call 결과 오류 또는 `isError: true` |
| 14 | Logs 페이지에 직전 호출 미표시 |
| 20 | 환경 미준비(token/API/Web/service/tool 등) |

산출물:
- `dev-plan/.artifacts/ui-smoke/events.json`
- `dev-plan/.artifacts/ui-smoke/screenshots/*.png`

## Current verification snapshot — 2026-05-15

- `cd apps/api && uv run pytest -q`: **140 passed**.
- `cd apps/fake-mcp && uv run pytest -q`: **12 passed**.
- `cd apps/demo-mcp-suite && uv run pytest -q`: **21 passed**.
- `make test`: **PASS** (API + fake-mcp + demo-mcp-suite).
- `pnpm lint`, `pnpm build`, `pnpm test`: **PASS**.
- `make smoke`: **PASS**.
- Alembic fresh migration smoke: **PASS** (`20260512_0001` → `20260515_0008`, OAuth persistence + service transport + resources/prompts cache + STDIO runtime state + service capabilities columns present).
- `plutil -lint infra/launchd/*.plist`: **6 plist OK** (`fake-mcp`, `api`, `web`, `backup`, `logrotate`, `refresh`).
- `python -m coremcp.refresh` no-service smoke: **PASS** (`services_checked=0`, exit 0).
- `infra/scripts/coremcp-launchctl.sh restart && infra/scripts/ops-smoke.sh`: **PASS** (`fake-mcp/api/web/backup/logrotate/refresh` labels loaded, Fake/API/Web ready; Tailscale CLI missing so skipped).
- `make run`: **PASS** (bootstrap, Web build, launchd restart, ops smoke).
- `COREMCP_WEB_URL=http://127.0.0.1:3004 infra/scripts/web-route-smoke.sh`: **PASS** (security headers + `/services` → `/toolbox` → `/clients` → `/settings` → `/playground` → `/logs`).
- `make ui-smoke`: **PASS** (`8` services, `40` playground tools, `demo_ops.ops_status` call, screenshots/events generated).
- `make codex-install && make codex-smoke`: **PASS** (Codex MCP config + CoreMCP initialize/tools-list with Codex client token).
- OAuth persistence smoke: **PASS** (`tests/test_oauth_persistence.py`, app recreate 후 access token/refresh rotation/revocation 유지, signing private key vault reference 저장).
- STDIO transport/CLI/ops modules: **PASS** (`tests/test_stdio_transport.py`, `tests/test_cli.py`, `tests/test_ops_stability.py`).
- MCP resources/prompts proxy smoke: **PASS** (`resources/list`, `resources/read` large-content truncate, `resources/templates/list`, `prompts/list`, `prompts/get` API regression).
- MCP catalog notification smoke: **PASS** (`notifications/{tools,resources,prompts}/list_changed` category emission, `Last-Event-Id` SSE replay, unsupported protocol downgrade warning).
- MCP batch/progress notification smoke: **PASS** (JSON-RPC batch mixed/notification-only/empty-array behavior, downstream `notifications/progress` + `notifications/resources/updated` SSE fan-out, STDIO notification callback, app/repository facade imports).
- Multi-MCP hardening smoke: **PASS** (dotted downstream tool name namespace 강제, active-service `resources/read` catalog miss no-broadcast, duplicate resource URI ambiguous reject, HTTP downstream session id mapping, downstream `notifications/tools/list_changed` fan-in).
- Multi-MCP P1 운영성 smoke: **PASS** (dynamic capability merge, tool args JSON Schema validation, per-service rate limit, `tools/list` unavailable metadata, health-probe schema drift refresh, downstream `Idempotency-Key` forwarding).
- Phase 7~10 hardening smoke: **PASS** (`tests/test_stdio_transport.py` 9 cases, `tests/test_reaper.py`, CLI token/export/import, resources/prompts cache validation/routing).
- Follow-up backlog smoke: **PASS** (STDIO crash-state DB persistence regression, Makefile CLI wrapper dry-run without token echo, Web UX Phase 6 lint/build/route smoke).
- CORS config smoke: **PASS** (`tests/test_cors.py`, default origins + custom `COREMCP_CORS_ALLOWED_ORIGINS`).
- Alembic bootstrap single-source smoke: **PASS** (`tests/test_bootstrap.py`, fresh DB + legacy 0001-like DB + idempotent connect).
- SSRF DNS pinning smoke: **PASS** (`tests/test_url_safety.py`, DNS mismatch 차단 + IP pinning Host/SNI 보존).
- Admin token rotate + OAuth rate limit smoke: **PASS** (`tests/test_admin_rate_limit.py`, `tests/test_oauth_rate_limit.py`, file-backed rotate, old-token reject, DCR 10/hour/IP, CIMD 30/hour/IP).
- Health probe/dashboard smoke: **PASS** (`/v1/dashboard/summary`, proactive service health probe fields, Prometheus health gauges).
- External validation helpers: **PASS** (`make external-env-validate` local ops smoke passed with Tailscale/external URL skipped; `COREMCP_SOAK_DURATION_SECONDS=1 COREMCP_SOAK_INTERVAL_SECONDS=1 make soak-check` passed; `make mobile-qa-checklist` printed device QA checklist).
- Design docs/assets smoke: **PASS** (`docs/design/README.md`, code-level audit, component patterns, token JSON/CSS/SVG asset present).
- MCP runtime curl smoke: **PASS** (`initialize` → `tools/list` → `fake.echo tools/call`).
- CSP smoke: **PASS** (`script-src` nonce, no `unsafe-inline` in `script-src`/`style-src`).
- `/metrics` default-off smoke: **PASS** (`404`).
- `git diff --check`: **PASS**.

Remaining Work Classification — 2026-05-14:

| Category | Items |
|---|---|
| 목적 부합 core 미구현 | 현재 known blocker 없음. personal gateway 목적 범위의 core blocker는 로컬 검증 기준 해소됨 |
| 이번 안정화 batch 완료 | STDIO process cap/default idle timeout/delete cleanup, admin `/v1` + `/mcp` fixed-window rate limit, CLI import hardening, Multi-MCP namespace/session/resource routing/P1 운영성 hardening 구현 및 테스트 통과 |
| 외부환경 검증 필요 | actual macOS reboot recovery (`--post-reboot`), Tailscale CLI install/login/Serve/ACL smoke, real external OAuth client compatibility, 실제 모바일 visual QA, long soak — 자동화 entrypoint는 `make external-env-validate`, `make mobile-qa-checklist`, `make soak-check` |
| 선택 Polish | Web Admin UX polish, proactive health probe, dashboard/metric tuning은 지속 개선 대상 |

Stabilization batch note:

- Commit split is recommended in `dev-plan/implement_20260514_224500.md`, but this docs/code patch does not create commits or push unless the user explicitly asks.
- STDIO resource limits, admin/MCP rate limit, and CLI import hardening are integrated. Focus remaining validation on actual reboot, Tailscale Serve/ACL, real external OAuth client compatibility, physical mobile QA, and long soak on the operations host.

## External environment / mobile / soak commands

Run these on the actual operations host when local smoke is already green:

```bash
# Local ops plus optional external URL checks
make external-env-validate
COREMCP_EXTERNAL_API_URL=https://<tailscale-or-public-host>/health \
COREMCP_EXTERNAL_WEB_URL=https://<tailscale-or-public-host>/ \
  make external-env-validate

# Actual mobile browser checklist; prints URLs and manual pass/fail prompts
make mobile-qa-checklist

# Long soak; tune duration/interval for the environment
COREMCP_SOAK_DURATION_SECONDS=3600 \
COREMCP_SOAK_INTERVAL_SECONDS=30 \
  make soak-check
```

Record actual reboot recovery, Tailscale login/Serve/ACL, real external OAuth client compatibility, mobile visual QA, and long soak results in this section after the batch is verified.

## Current test layers

| Layer | Path | Command | Status |
|---|---|---|---|
| API unit/integration | `apps/api/tests/` | `cd apps/api && uv run pytest` | 108 tests passing |
| Fake MCP fixture | `apps/fake-mcp/tests/` | `cd apps/fake-mcp && uv run pytest` | 12 tests passing |
| Demo MCP suite | `apps/demo-mcp-suite/tests/` | `cd apps/demo-mcp-suite && uv run pytest` | 21 tests passing; 8 local demo MCP endpoints |
| Web lint/type/build | `apps/web/` | `pnpm lint && pnpm build` | passing |
| Workspace no-op tests | `packages/*`, `apps/web` | `pnpm test` | passing |
| In-process smoke | `apps/api/coremcp/smoke.py` | `make smoke` | passing |
| Alembic migration | `apps/api/alembic/` | `uv run alembic upgrade head` | passing |
| Web route E2E | `apps/web/app/**` | `infra/scripts/web-route-smoke.sh` | passing |
| Web UI E2E | `infra/scripts/ui-smoke.py` | `make ui-smoke` | passing locally with running API/Web |
| Web design system docs | `docs/design/` | `rg -n "CoreMCP Design System" docs/design` | passing |
| Codex CLI MCP | `infra/scripts/codex-*.sh` | `make codex-install && make codex-smoke` | passing |
| OAuth/client auth flow | `apps/api/tests/test_mcp_gateway.py` | DCR/CIMD, PKCE, JWT/JWKS, refresh, revoke, one-time token exchange | passing |
| Ops scripts | `infra/scripts`, `infra/launchd` | backup/restore/log rotation/scheduled refresh/plutil + fake-mcp/api/web/backup/logrotate/refresh label logic | plutil + actual launchd load/ops smoke passing; Tailscale/reboot are environment checks |

## Conventions

- Python tests use `pytest` with async support where needed.
- API tests use `httpx.ASGITransport` or `httpx.MockTransport` to avoid external services.
- Fake downstream tests use FastAPI `TestClient`.
- Web currently relies on TypeScript/lint/build checks; add component/E2E tests when UI logic becomes stateful.
- New protocol behavior must include regression tests.
- Bug fixes must include a failing regression test first when practical.

## Security regression expectations

Always keep tests for:

- CoreMCP Authorization header is never forwarded downstream.
- Unknown tool returns JSON-RPC `-32602`.
- Unsupported MCP method returns JSON-RPC `-32601`.
- Downstream `tools/call` JSON-RPC error becomes `result.isError=true`.
- Protocol version negotiation covers `2025-06-18`, `2025-11-25`, missing version, and future version.
- Icon metadata uses MCP `src`, not `url`.
- `cmcp_client_*` token은 DB hash로 검증되고 revoke 후 `/mcp`가 401을 반환합니다.
- `codex_cli` external connection은 client token을 발급할 수 있고 Codex MCP config는 `COREMCP_CLIENT_TOKEN` env var만 참조합니다.
- Service validation은 SSRF metadata endpoint를 차단하고 audit event를 남깁니다.
- DB catalog 기반 `tools/list`/`tools/call`이 fake downstream fixture로 end-to-end 성공합니다.
- Downstream redirect는 follow하지 않고 차단합니다.
- Downstream timeout은 `tools/call`에서 `result.isError=true`와 `_meta.coremcp.error_code=downstream_timeout`으로 반환하고 invocation `status=timeout`을 기록합니다.
- `Idempotency-Key` 중복 `tools/call`은 in-memory result cache를 재사용합니다.
- Tool-level override는 disabled/hidden tool을 `tools/list`에서 숨기고, disabled/visible_only call을 downstream 호출 없이 policy deny로 기록합니다.
- Request id는 API response header, downstream `X-Request-ID`, invocation/audit log에서 연결됩니다.
- Schema drift는 validation summary에 changed/added/removed count와 `schema_diff.added/removed/changed` detail로 기록되고 invalid refresh 시 기존 active catalog를 보존합니다.
- Tool preset(`readonly`, `dangerous_off`, `full_access`)은 tool-level override에 일괄 적용되고 hidden/visible/callable 정책을 회귀 테스트로 고정합니다.
- Downstream response sanitizer는 JSON `Content-Type`과 `COREMCP_DOWNSTREAM_MAX_RESPONSE_BYTES` size cap을 강제합니다.
- Client/OAuth token scopes는 `tools/list`에 `mcp:tools.read`, `tools/call`에 `mcp:tools.call`을 요구하고, 부족하면 downstream 호출 없이 policy deny로 기록합니다.
- `notifications/cancelled`는 202로 수락되고 invocation status `cancelled`로 기록됩니다.
- OAuth mode는 static bearer endpoint 숨김, PKCE 실패 reject, CIMD SSRF/redirect guard, revoke 후 `/mcp` 401을 유지합니다.
- Web route split은 직접 URL 진입과 navigation click을 모두 smoke로 확인합니다.
- Fernet fallback vault는 실제 ciphertext를 저장하고 legacy base64 값은 읽기 호환합니다.
- Web Admin은 nonce 기반 CSP/security headers를 `middleware.ts`에서 적용하고 `script-src`/`style-src`에 `unsafe-inline`을 사용하지 않습니다.
- `ALLOW_TAILSCALE_DOWNSTREAM=true`는 100.64.0.0/10 downstream을 명시적으로 허용합니다.
- `ICON_SVG_ENABLED=false` default는 SVG icon을 차단하고 PNG/WebP icon만 통과시킵니다.
