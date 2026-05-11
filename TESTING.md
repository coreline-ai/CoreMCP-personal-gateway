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

# Full local verification used in the first scaffold integration
make test && pnpm lint && pnpm build && pnpm test && git diff --check
```

## Current test layers

| Layer | Path | Command | Status |
|---|---|---|---|
| API unit/integration | `apps/api/tests/` | `cd apps/api && uv run pytest` | 13 tests passing |
| Fake MCP fixture | `apps/fake-mcp/tests/` | `cd apps/fake-mcp && uv run pytest` | 6 tests passing |
| Web lint/type/build | `apps/web/` | `pnpm lint && pnpm build` | passing |
| Workspace no-op tests | `packages/*`, `apps/web` | `pnpm test` | passing |
| Actual local smoke | `apps/api` + `apps/fake-mcp` uvicorn | manual command in session log | passing |

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
