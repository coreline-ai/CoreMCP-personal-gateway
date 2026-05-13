#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COREMCP_HOME="${COREMCP_HOME:-$HOME/.coremcp}"
ADMIN_TOKEN_FILE="${COREMCP_ADMIN_TOKEN_FILE:-$COREMCP_HOME/admin-token}"
API_URL="${COREMCP_API_URL:-http://127.0.0.1:8787}"
WEB_URL="${COREMCP_WEB_URL:-http://127.0.0.1:3003}"
FAKE_URL="${COREMCP_FAKE_MCP_URL:-http://127.0.0.1:8790}"

export COREMCP_ADMIN_TOKEN_FILE="$ADMIN_TOKEN_FILE"
export AUTH_MODE="${AUTH_MODE:-static_bearer}"
export FAKE_MCP_URL="${FAKE_MCP_URL:-$FAKE_URL/mcp}"
export COREMCP_SSRF_ALLOW_HOSTS="${COREMCP_SSRF_ALLOW_HOSTS:-127.0.0.1,localhost}"
export NEXT_PUBLIC_COREMCP_API_BASE_URL="${NEXT_PUBLIC_COREMCP_API_BASE_URL:-$API_URL}"

pids=()

cleanup() {
  for pid in "${pids[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT INT TERM

fail_if_port_busy() {
  local port="$1"
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Stop launchd first: infra/scripts/coremcp-launchctl.sh unload" >&2
    exit 70
  fi
}

wait_url() {
  local url="$1"
  for _ in $(seq 1 60); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "Timed out waiting for $url" >&2
  return 1
}

if [[ ! -s "$ADMIN_TOKEN_FILE" ]]; then
  "$ROOT_DIR/infra/scripts/bootstrap-local.sh"
fi

fail_if_port_busy 8790
fail_if_port_busy 8787
fail_if_port_busy 3003

echo "Starting fake MCP on $FAKE_URL ..."
(cd "$ROOT_DIR/apps/fake-mcp" && uv run uvicorn fake_mcp.main:app --host 127.0.0.1 --port 8790) &
pids+=("$!")

echo "Starting CoreMCP API on $API_URL ..."
(cd "$ROOT_DIR/apps/api" && uv run uvicorn coremcp.main:app --host 127.0.0.1 --port 8787) &
pids+=("$!")

echo "Starting CoreMCP Web on $WEB_URL ..."
(cd "$ROOT_DIR/apps/web" && pnpm start --hostname 127.0.0.1 --port 3003) &
pids+=("$!")

wait_url "$FAKE_URL/health"
wait_url "$API_URL/ready"
wait_url "$WEB_URL/"

cat <<EOF

CoreMCP is running.

  Web Admin: $WEB_URL
  API ready: $API_URL/ready
  MCP URL:   $API_URL/mcp
  Fake MCP:  $FAKE_URL/mcp

Admin token:
  cat "$ADMIN_TOKEN_FILE"

Claude Code:
  claude mcp add --transport http coremcp "$API_URL/mcp" \\
    --header "Authorization: Bearer \$(cat "$ADMIN_TOKEN_FILE")"

Press Ctrl-C to stop foreground services.
EOF

while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      echo "A CoreMCP process exited; stopping all services." >&2
      exit 1
    fi
  done
  sleep 2
done
