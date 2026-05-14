#!/usr/bin/env bash
set -euo pipefail

API_URL="${COREMCP_API_URL:-http://127.0.0.1:8787}"
WEB_URL="${COREMCP_WEB_URL:-http://127.0.0.1:3003}"
EXTERNAL_API_URL="${COREMCP_EXTERNAL_API_URL:-}"
EXTERNAL_WEB_URL="${COREMCP_EXTERNAL_WEB_URL:-}"
REQUIRE_TAILSCALE=0
POST_REBOOT=0

usage() {
  cat >&2 <<'USAGE'
Usage: infra/scripts/external-env-validate.sh [--post-reboot] [--require-tailscale]

Environment:
  COREMCP_API_URL          Local API URL. Default: http://127.0.0.1:8787
  COREMCP_WEB_URL          Local Web URL. Default: http://127.0.0.1:3003
  COREMCP_EXTERNAL_API_URL Optional Tailscale/Caddy API URL for remote smoke.
  COREMCP_EXTERNAL_WEB_URL Optional Tailscale/Caddy Web URL for remote smoke.

This script does not configure Tailscale/OAuth clients. It verifies that the
host is ready for those manual checks and fails when --require-tailscale is set.
USAGE
}

for arg in "$@"; do
  case "$arg" in
    --post-reboot) POST_REBOOT=1 ;;
    --require-tailscale) REQUIRE_TAILSCALE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 64 ;;
  esac
done

pass() { printf 'PASS %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*" >&2; }
fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

ops_args=()
[[ "$POST_REBOOT" -eq 1 ]] && ops_args+=(--post-reboot)
[[ "$REQUIRE_TAILSCALE" -eq 1 ]] && ops_args+=(--require-tailscale)
if [[ "${#ops_args[@]}" -gt 0 ]]; then
  COREMCP_API_URL="$API_URL" COREMCP_WEB_URL="$WEB_URL" infra/scripts/ops-smoke.sh "${ops_args[@]}"
else
  COREMCP_API_URL="$API_URL" COREMCP_WEB_URL="$WEB_URL" infra/scripts/ops-smoke.sh
fi
pass "local ops smoke completed"

if [[ -n "$EXTERNAL_API_URL" ]]; then
  curl -fsS "$EXTERNAL_API_URL/ready" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"' \
    || fail "external API ready check failed: $EXTERNAL_API_URL/ready"
  pass "external API ready: $EXTERNAL_API_URL/ready"
else
  warn "COREMCP_EXTERNAL_API_URL not set; skipped external API smoke"
fi

if [[ -n "$EXTERNAL_WEB_URL" ]]; then
  curl -fsSI "$EXTERNAL_WEB_URL/" >/dev/null || fail "external Web check failed: $EXTERNAL_WEB_URL/"
  pass "external Web ready: $EXTERNAL_WEB_URL/"
else
  warn "COREMCP_EXTERNAL_WEB_URL not set; skipped external Web smoke"
fi

if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
  tailscale_ip="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
  [[ -n "$tailscale_ip" ]] && pass "tailscale IPv4 observed: $tailscale_ip"
fi

pass "external environment validation completed"
