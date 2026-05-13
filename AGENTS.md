# AGENTS.md

Project-specific instructions for CoreMCP implementation agents. These rules complement `CLAUDE.md`.

## Project source of truth

- Primary implementation docs: `coremcp-docs/`.
- Current implementation tracker: `dev-plan/implement_20260511_221842.md`.
- `production_docs_donotuse/` is SaaS reference only. Do not implement SaaS features from it unless explicitly requested.

## Current target

Build the personal CoreMCP gateway in phases:

1. P0: Codex CLI `exec`/client token → CoreMCP `/mcp` → fake downstream MCP → tool result.
2. P1: real service registry, per-client tokens, credential vault, SSRF guard.
3. P2: Web Admin UI.
4. P3: launchd operations and optional OAuth/CIMD.

## Required safety invariants

- Never pass CoreMCP admin/client tokens downstream.
- Never treat `Mcp-Session-Id` as authentication.
- Re-check bearer auth on every `/mcp` request.
- Store downstream credentials only through the vault abstraction.
- Do not store raw tool arguments/results unless debug trace is explicitly enabled.
- Keep `AUTH_MODE=static_bearer` as the default. OAuth/CIMD/DCR stays optional.
- Render tool icons through `src` and `<img>` only. Never inline SVG.

## Development commands

Expected commands once scaffolds exist:

```bash
cd apps/api && uv run pytest
cd apps/fake-mcp && uv run pytest
pnpm lint
pnpm build
```

Root convenience commands:

```bash
make test
make lint
make build
make codex-install
make codex-smoke
```

## Editing rules

- Keep changes surgical and tied to the active dev-plan phase.
- Match existing document terms: “도구함”, “연결된 AI client”, “MCP 추가/등록”.
- Do not add external LLM API dependencies. CoreMCP is a gateway, not an LLM service.
- Treat new SaaS-oriented pattern notes (including `coremcp-design-patterns-to-absorb.md`) as reference/backlog only. Do not implement team/workspace/marketplace/publisher/billing features unless the user explicitly requests them and an active dev-plan phase/ADR includes them.
- Update docs and dev-plan checkboxes when implementation state changes.
