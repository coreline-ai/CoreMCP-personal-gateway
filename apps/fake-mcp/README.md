# fake-mcp

Test-only downstream MCP server for CoreMCP P0/P1 integration tests.

## Endpoints

- `GET /health` — process health check.
- `POST /mcp` — JSON-RPC MCP-like endpoint implementing `initialize`, `tools/list`, `tools/call`, and `ping`.
- `GET /_test/authorization` — **test-only** endpoint that exposes Authorization headers received by `/mcp` for token-boundary tests.
- `POST /_test/reset-state` — **test-only** endpoint that clears in-memory test state.

The `/_test/*` endpoints are not production APIs and exist only to verify that CoreMCP forwards downstream service credentials while not leaking CoreMCP client/admin tokens.

## Fixture coverage

The fake server intentionally exposes production-test fixtures used by CoreMCP integration tests:

- `cancellation` — bounded long-running call fixture for timeout/cancel behavior.
- `schema-change` — changes title and input schema on each `tools/list` for drift tests.
- `icons-rich` — top-level MCP `icons[].src` metadata including an SVG data URL and PNG URL.
- `cimd-test` — marker tool paired with `/.well-known/oauth-client`.
- `dcr-test` — marker tool paired with `/oauth/register`.

Current fixture test count: `12 passed` via `cd apps/fake-mcp && uv run pytest -q`.
