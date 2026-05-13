#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COREMCP_HOME="${COREMCP_HOME:-$HOME/.coremcp}"
ADMIN_TOKEN_FILE="${COREMCP_ADMIN_TOKEN_FILE:-$COREMCP_HOME/admin-token}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 69
  }
}

need_cmd uv
need_cmd pnpm
need_cmd python3

mkdir -p "$COREMCP_HOME/data" "$COREMCP_HOME/logs" "$COREMCP_HOME/backups"
chmod 700 "$COREMCP_HOME"

if [[ ! -s "$ADMIN_TOKEN_FILE" ]]; then
  umask 077
  python3 - <<'PY' > "$ADMIN_TOKEN_FILE"
import secrets
print("cmcp_admin_" + secrets.token_urlsafe(32))
PY
  chmod 600 "$ADMIN_TOKEN_FILE"
  echo "Created admin token: $ADMIN_TOKEN_FILE"
else
  chmod 600 "$ADMIN_TOKEN_FILE"
  echo "Admin token exists: $ADMIN_TOKEN_FILE"
fi

echo "Syncing Python dependencies..."
(cd "$ROOT_DIR/apps/api" && uv sync >/dev/null)
(cd "$ROOT_DIR/apps/fake-mcp" && uv sync >/dev/null)

echo "Installing Node dependencies..."
(cd "$ROOT_DIR" && pnpm install --frozen-lockfile >/dev/null)

echo "Applying API migrations..."
(cd "$ROOT_DIR/apps/api" && COREMCP_ADMIN_TOKEN_FILE="$ADMIN_TOKEN_FILE" uv run alembic upgrade head)

echo "Bootstrap complete."
echo "Admin token preview: $(cut -c1-18 "$ADMIN_TOKEN_FILE")…"
