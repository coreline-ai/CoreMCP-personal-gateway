#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CODEX_MCP_NAME="${CODEX_MCP_NAME:-coremcp}"
TOKEN_FILE="${COREMCP_CODEX_TOKEN_FILE:-$HOME/.coremcp/codex-client-token}"
WORKDIR="${COREMCP_CODEX_WORKDIR:-$PROJECT_ROOT}"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI is required." >&2
  exit 69
fi

if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "Codex client token not found at $TOKEN_FILE" >&2
  echo "Run: $PROJECT_ROOT/infra/scripts/codex-mcp-install.sh --force" >&2
  exit 65
fi

if ! codex mcp get "$CODEX_MCP_NAME" >/dev/null 2>&1; then
  echo "Codex MCP server '$CODEX_MCP_NAME' is not registered." >&2
  echo "Run: $PROJECT_ROOT/infra/scripts/codex-mcp-install.sh --force" >&2
  exit 65
fi

export COREMCP_CLIENT_TOKEN
COREMCP_CLIENT_TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"

if [[ $# -eq 0 ]]; then
  set -- "CoreMCP MCP server에서 사용 가능한 도구를 확인하고 한 줄로 요약해줘."
fi

exec codex exec -C "$WORKDIR" "$@"
