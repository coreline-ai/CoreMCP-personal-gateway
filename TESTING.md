# TESTING.md

CoreMCP 테스트 기준과 실행 명령입니다.

## Philosophy

테스트는 CoreMCP가 개인용 보안 gateway로 안전하게 동작하는지 확인하는 최소 안전망입니다. 특히 token boundary, MCP protocol negotiation, downstream proxy, icon metadata shape는 회귀 테스트로 고정합니다.

## Test commands

```bash
# Python API + fake downstream tests
make test

# Web/package type and lint checks
pnpm lint
pnpm build
pnpm test

# In-process API smoke
make smoke

# Ops script smoke
tmpdir="$(mktemp -d)" && mkdir -p "$tmpdir/data" && sqlite3 "$tmpdir/data/coremcp.sqlite3" 'create table smoke(id integer);'
COREMCP_DATA_DIR="$tmpdir" infra/scripts/backup-sqlite.sh
plutil -lint infra/launchd/com.coremcp.api.plist infra/launchd/com.coremcp.web.plist infra/launchd/com.coremcp.backup.plist infra/launchd/com.coremcp.logrotate.plist
infra/scripts/coremcp-launchctl.sh load
infra/scripts/ops-smoke.sh

# Web route smoke (requires npx or PWCLI)
infra/scripts/web-route-smoke.sh

# Codex CLI MCP smoke (non-LLM; requires make run first)
make codex-install
make codex-smoke

# Alembic migration smoke
cd apps/api && COREMCP_DB_PATH="$(mktemp -d)/coremcp.sqlite3" uv run alembic upgrade head

# Full local verification
make test && pnpm lint && pnpm build && pnpm test && make smoke && make codex-smoke && git diff --check
```

## Current verification snapshot — 2026-05-13

- `cd apps/api && uv run pytest -q`: **46 passed**.
- `cd apps/fake-mcp && uv run pytest -q`: **12 passed**.
- `pnpm lint`, `pnpm build`, `pnpm test`: **PASS**.
- `make smoke`: **PASS**.
- Alembic fresh migration smoke: **PASS** (`20260512_0001` → `20260513_0003`).
- `plutil -lint infra/launchd/*.plist`: **5 plist OK** (`fake-mcp`, `api`, `web`, `backup`, `logrotate`).
- `infra/scripts/coremcp-launchctl.sh restart && infra/scripts/ops-smoke.sh`: **PASS** (`fake-mcp/api/web/backup/logrotate` labels loaded, Fake/API/Web ready; Tailscale CLI missing so skipped).
- `make run`: **PASS** (bootstrap, Web build, launchd restart, ops smoke).
- `COREMCP_WEB_URL=http://127.0.0.1:3004 infra/scripts/web-route-smoke.sh`: **PASS** (security headers + `/services` → `/toolbox` → `/clients` → `/settings` → `/playground` → `/logs`).
- `make codex-install && make codex-smoke`: **PASS** (Codex MCP config + CoreMCP initialize/tools-list with Codex client token).
- Design docs/assets smoke: **PASS** (`docs/design/README.md`, code-level audit, component patterns, token JSON/CSS/SVG asset present).
- MCP runtime curl smoke: **PASS** (`initialize` → `tools/list` → `fake.echo tools/call`).
- CSP smoke: **PASS** (`script-src` nonce, no `unsafe-inline` in `script-src`/`style-src`).
- `/metrics` default-off smoke: **PASS** (`404`).
- `git diff --check`: **PASS**.

Remaining items are split as follows:

| Category | Items |
|---|---|
| 목적 부합 코드 미구현 | 없음 — one-time token, `/metrics`, service detail, tool-level override, request_id, schema drift, cancellation downstream forward 포함 구현 완료 |
| 외부환경 검증 필요 | actual macOS reboot recovery (`--post-reboot`), Tailscale CLI install/login/Serve/ACL smoke, real external OAuth client compatibility |
| 선택 Polish | 실제 모바일 기기 visual QA, 장기 운영 관측 튜닝 |

## Current test layers

| Layer | Path | Command | Status |
|---|---|---|---|
| API unit/integration | `apps/api/tests/` | `cd apps/api && uv run pytest` | 46 tests passing |
| Fake MCP fixture | `apps/fake-mcp/tests/` | `cd apps/fake-mcp && uv run pytest` | 12 tests passing |
| Web lint/type/build | `apps/web/` | `pnpm lint && pnpm build` | passing |
| Workspace no-op tests | `packages/*`, `apps/web` | `pnpm test` | passing |
| In-process smoke | `apps/api/coremcp/smoke.py` | `make smoke` | passing |
| Alembic migration | `apps/api/alembic/` | `uv run alembic upgrade head` | passing |
| Web route E2E | `apps/web/app/**` | `infra/scripts/web-route-smoke.sh` | passing |
| Web design system docs | `docs/design/` | `rg -n "CoreMCP Design System" docs/design` | passing |
| Codex CLI MCP | `infra/scripts/codex-*.sh` | `make codex-install && make codex-smoke` | passing |
| OAuth/client auth flow | `apps/api/tests/test_mcp_gateway.py` | DCR/CIMD, PKCE, JWT/JWKS, refresh, revoke, one-time token exchange | passing |
| Ops scripts | `infra/scripts`, `infra/launchd` | backup/restore/log rotation/plutil + fake-mcp/api/web/backup/logrotate label logic | plutil + actual launchd load/ops smoke passing; Tailscale/reboot are environment checks |

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
- Schema drift는 validation summary에 changed/added/removed count로 기록되고 invalid refresh 시 기존 active catalog를 보존합니다.
- Client/OAuth token scopes는 `tools/list`에 `mcp:tools.read`, `tools/call`에 `mcp:tools.call`을 요구하고, 부족하면 downstream 호출 없이 policy deny로 기록합니다.
- `notifications/cancelled`는 202로 수락되고 invocation status `cancelled`로 기록됩니다.
- OAuth mode는 static bearer endpoint 숨김, PKCE 실패 reject, CIMD SSRF/redirect guard, revoke 후 `/mcp` 401을 유지합니다.
- Web route split은 직접 URL 진입과 navigation click을 모두 smoke로 확인합니다.
- Fernet fallback vault는 실제 ciphertext를 저장하고 legacy base64 값은 읽기 호환합니다.
- Web Admin은 nonce 기반 CSP/security headers를 `middleware.ts`에서 적용하고 `script-src`/`style-src`에 `unsafe-inline`을 사용하지 않습니다.
- `ALLOW_TAILSCALE_DOWNSTREAM=true`는 100.64.0.0/10 downstream을 명시적으로 허용합니다.
- `ICON_SVG_ENABLED=false` default는 SVG icon을 차단하고 PNG/WebP icon만 통과시킵니다.
