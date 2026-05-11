# fake-mcp

Test-only downstream MCP server for CoreMCP P0/P1 integration tests.

## Endpoints

- `GET /health` — process health check.
- `POST /mcp` — JSON-RPC MCP-like endpoint implementing `initialize`, `tools/list`, `tools/call`, and `ping`.
- `GET /_test/authorization` — **test-only** endpoint that exposes Authorization headers received by `/mcp` for token-boundary tests.
- `POST /_test/reset-state` — **test-only** endpoint that clears in-memory test state.

The `/_test/*` endpoints are not production APIs and exist only to verify that CoreMCP forwards downstream service credentials while not leaking CoreMCP client/admin tokens.
