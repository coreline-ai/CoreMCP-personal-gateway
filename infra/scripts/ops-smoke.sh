#!/usr/bin/env bash
set -euo pipefail

API_URL="${COREMCP_API_URL:-http://127.0.0.1:8787}"
WEB_URL="${COREMCP_WEB_URL:-http://127.0.0.1:3003}"
FAKE_URL="${COREMCP_FAKE_MCP_URL:-http://127.0.0.1:8790}"
PLIST_DIR="${COREMCP_PLIST_DIR:-$(pwd)/infra/launchd}"
REQUIRE_TAILSCALE=0
POST_REBOOT=0

usage() {
  cat >&2 <<'EOF'
Usage: infra/scripts/ops-smoke.sh [--post-reboot] [--require-tailscale]

Environment:
  COREMCP_API_URL   API readiness URL base. Default: http://127.0.0.1:8787
  COREMCP_WEB_URL   Web admin URL base.     Default: http://127.0.0.1:3003
  COREMCP_FAKE_MCP_URL Fake MCP URL base.   Default: http://127.0.0.1:8790
  COREMCP_PLIST_DIR launchd plist dir.      Default: ./infra/launchd
EOF
}

for arg in "$@"; do
  case "$arg" in
    --post-reboot)
      POST_REBOOT=1
      ;;
    --require-tailscale)
      REQUIRE_TAILSCALE=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 64
      ;;
  esac
done

pass() { printf 'PASS %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*" >&2; }
fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

require_launchd_label() {
  local label="$1"
  launchctl list | awk -v label="$label" '$3 == label && $2 == 0 { found = 1 } END { exit found ? 0 : 1 }'
}

plutil -lint \
  "$PLIST_DIR/com.coremcp.api.plist" \
  "$PLIST_DIR/com.coremcp.web.plist" \
  "$PLIST_DIR/com.coremcp.fake-mcp.plist" \
  "$PLIST_DIR/com.coremcp.backup.plist" \
  "$PLIST_DIR/com.coremcp.logrotate.plist" \
  "$PLIST_DIR/com.coremcp.refresh.plist" >/dev/null
pass "launchd plist syntax"

for label in \
  "com.coremcp.fake-mcp" \
  "com.coremcp.api" \
  "com.coremcp.web" \
  "com.coremcp.backup" \
  "com.coremcp.logrotate" \
  "com.coremcp.refresh"; do
  require_launchd_label "$label" || fail "$label is not loaded with last exit status 0"
done
pass "launchd labels loaded"

curl -fsS "$API_URL/ready" >/dev/null
pass "API ready: $API_URL/ready"

curl -fsS "$FAKE_URL/health" >/dev/null
pass "Fake MCP healthy: $FAKE_URL/health"

curl -fsSI "$WEB_URL/" >/dev/null
pass "Web ready: $WEB_URL/"

if command -v tailscale >/dev/null 2>&1; then
  if ! tailscale status >/dev/null 2>&1; then
    if [[ "$REQUIRE_TAILSCALE" -eq 1 ]]; then
      fail "tailscale CLI exists but is not connected"
    fi
    warn "tailscale CLI exists but is not connected; skipped Tailscale smoke"
  else
    pass "Tailscale status"
    if tailscale_ip="$(tailscale ip -4 2>/dev/null | head -n 1)" && [[ -n "$tailscale_ip" ]]; then
      pass "Tailscale IPv4: $tailscale_ip"
    else
      warn "Tailscale IPv4 unavailable"
    fi
  fi
else
  if [[ "$REQUIRE_TAILSCALE" -eq 1 ]]; then
    fail "tailscale CLI is not installed"
  fi
  warn "tailscale CLI is not installed; skipped Tailscale smoke"
fi

if [[ "$POST_REBOOT" -eq 1 ]]; then
  if command -v sysctl >/dev/null 2>&1; then
    boot_time="$(sysctl -n kern.boottime 2>/dev/null | awk -F'[=,]' '{ gsub(/[^0-9]/, "", $2); print $2 }' || true)"
    if [[ -n "${boot_time:-}" ]]; then
      pass "post-reboot boot epoch observed: $boot_time"
    else
      warn "post-reboot boot epoch unavailable"
    fi
  fi
  pass "post-reboot launchd/API/Web readiness check completed"
fi
