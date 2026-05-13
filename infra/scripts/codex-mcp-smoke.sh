#!/usr/bin/env bash
set -euo pipefail

COREMCP_API_URL="${COREMCP_API_URL:-http://127.0.0.1:8787}"
COREMCP_MCP_URL="${COREMCP_MCP_URL:-${COREMCP_API_URL%/}/mcp}"
CODEX_MCP_NAME="${CODEX_MCP_NAME:-coremcp}"
TOKEN_FILE="${COREMCP_CODEX_TOKEN_FILE:-$HOME/.coremcp/codex-client-token}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 69
  fi
}

require_cmd codex
require_cmd curl
require_cmd python3

if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "Codex client token not found at $TOKEN_FILE" >&2
  echo "Run infra/scripts/codex-mcp-install.sh --force first." >&2
  exit 65
fi

export COREMCP_CLIENT_TOKEN
COREMCP_CLIENT_TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"

config_json="$(codex mcp get "$CODEX_MCP_NAME" --json)"
python3 -c '
import json
import sys

config = json.loads(sys.stdin.read())
transport = config.get("transport", {})
assert transport.get("type") == "streamable_http", transport
assert transport.get("bearer_token_env_var") == "COREMCP_CLIENT_TOKEN", transport
' <<< "$config_json"
echo "PASS codex mcp config: $CODEX_MCP_NAME"

curl -fsS "$COREMCP_API_URL/ready" >/dev/null
echo "PASS CoreMCP ready: $COREMCP_API_URL/ready"

headers="$(mktemp)"
body="$(mktemp)"
cleanup() {
  rm -f "$headers" "$body"
}
trap cleanup EXIT

curl -fsS -D "$headers" -o "$body" -X POST "$COREMCP_MCP_URL" \
  -H "Authorization: Bearer $COREMCP_CLIENT_TOKEN" \
  -H "Content-Type: application/json" \
  -H "MCP-Protocol-Version: 2025-11-25" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"codex-cli","version":"smoke"}}}'

session_id="$(
  python3 - "$headers" <<'PY'
import sys

for line in open(sys.argv[1], encoding="utf-8", errors="ignore"):
    if line.lower().startswith("mcp-session-id:"):
        print(line.split(":", 1)[1].strip())
        break
PY
)"

python3 - "$body" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1]))
if "error" in payload:
    raise SystemExit(payload["error"])
assert payload.get("result", {}).get("protocolVersion") in {"2025-06-18", "2025-11-25"}
PY
echo "PASS MCP initialize"

tools_body="$(mktemp)"
trap 'rm -f "$headers" "$body" "$tools_body"' EXIT
curl -fsS -o "$tools_body" -X POST "$COREMCP_MCP_URL" \
  -H "Authorization: Bearer $COREMCP_CLIENT_TOKEN" \
  -H "Content-Type: application/json" \
  -H "MCP-Protocol-Version: 2025-11-25" \
  -H "Mcp-Session-Id: $session_id" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

tool_count="$(
  python3 - "$tools_body" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1]))
if "error" in payload:
    raise SystemExit(payload["error"])
print(len(payload.get("result", {}).get("tools", [])))
PY
)"
echo "PASS MCP tools/list via Codex client token: ${tool_count} tools"
