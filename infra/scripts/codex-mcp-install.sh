#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COREMCP_API_URL="${COREMCP_API_URL:-http://127.0.0.1:8787}"
COREMCP_MCP_URL="${COREMCP_MCP_URL:-${COREMCP_API_URL%/}/mcp}"
CODEX_MCP_NAME="${CODEX_MCP_NAME:-coremcp}"
TOKEN_FILE="${COREMCP_CODEX_TOKEN_FILE:-$HOME/.coremcp/codex-client-token}"
ADMIN_TOKEN_FILE="${COREMCP_ADMIN_TOKEN_FILE:-$HOME/.coremcp/admin-token}"
FORCE=0
ROTATE_TOKEN=0

usage() {
  cat <<EOF
Usage: $0 [--force] [--rotate-token]

Registers CoreMCP as a Codex CLI MCP server and prepares a Codex-specific
client bearer token for codex exec.

Environment:
  COREMCP_API_URL            default: http://127.0.0.1:8787
  COREMCP_MCP_URL            default: \$COREMCP_API_URL/mcp
  CODEX_MCP_NAME             default: coremcp
  COREMCP_ADMIN_TOKEN        optional; falls back to ~/.coremcp/admin-token
  COREMCP_CODEX_TOKEN_FILE   default: ~/.coremcp/codex-client-token
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      FORCE=1
      shift
      ;;
    --rotate-token)
      ROTATE_TOKEN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 64
      ;;
  esac
done

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 69
  fi
}

json_get() {
  python3 -c '
import json
import sys

data = json.loads(sys.stdin.read())
value = data
for key in sys.argv[1].split("."):
    value = value[key]
print(value)
' "$1"
}

require_cmd codex
require_cmd curl
require_cmd python3

ADMIN_TOKEN="${COREMCP_ADMIN_TOKEN:-}"
if [[ -z "$ADMIN_TOKEN" && -f "$ADMIN_TOKEN_FILE" ]]; then
  ADMIN_TOKEN="$(tr -d '\r\n' < "$ADMIN_TOKEN_FILE")"
fi
if [[ -z "$ADMIN_TOKEN" ]]; then
  echo "CoreMCP admin token not found. Run make run first or set COREMCP_ADMIN_TOKEN." >&2
  exit 65
fi

mkdir -p "$(dirname "$TOKEN_FILE")"
chmod 700 "$(dirname "$TOKEN_FILE")" 2>/dev/null || true

if [[ ! -s "$TOKEN_FILE" || "$ROTATE_TOKEN" -eq 1 ]]; then
  echo "Issuing Codex CLI client token from CoreMCP..."
  curl -fsS "$COREMCP_API_URL/ready" >/dev/null

  connection_json="$(
    curl -fsS -X POST "$COREMCP_API_URL/v1/external-connections" \
      -H "Authorization: Bearer $ADMIN_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"client_type":"codex_cli","client_name":"Codex CLI exec (local)"}'
  )"
  connection_id="$(printf '%s' "$connection_json" | json_get id)"

  token_json="$(
    curl -fsS -X POST "$COREMCP_API_URL/v1/settings/client-tokens" \
      -H "Authorization: Bearer $ADMIN_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"external_connection_id\":\"$connection_id\",\"scopes\":[\"mcp:tools.read\",\"mcp:tools.call\"]}"
  )"
  client_token="$(printf '%s' "$token_json" | json_get token)"
  token_prefix="$(printf '%s' "$token_json" | json_get token_prefix)"
  printf '%s\n' "$client_token" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  echo "Saved Codex client token: $TOKEN_FILE ($token_prefix...)"
else
  echo "Using existing Codex client token: $TOKEN_FILE"
fi

if codex mcp get "$CODEX_MCP_NAME" >/dev/null 2>&1; then
  if [[ "$FORCE" -eq 1 ]]; then
    codex mcp remove "$CODEX_MCP_NAME" >/dev/null
  else
    echo "Codex MCP server '$CODEX_MCP_NAME' already exists. Use --force to replace it."
    codex mcp get "$CODEX_MCP_NAME"
    exit 0
  fi
fi

codex mcp add "$CODEX_MCP_NAME" \
  --url "$COREMCP_MCP_URL" \
  --bearer-token-env-var COREMCP_CLIENT_TOKEN

cat <<EOF

CoreMCP is registered for Codex CLI.

Run with:
  $PROJECT_ROOT/infra/scripts/codex-exec-coremcp.sh "CoreMCP 도구 목록을 확인해줘"

Non-LLM smoke:
  $PROJECT_ROOT/infra/scripts/codex-mcp-smoke.sh
EOF
