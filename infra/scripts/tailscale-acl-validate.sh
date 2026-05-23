#!/usr/bin/env bash
# tailscale-acl-validate — verify Tailscale is reachable and the configured
# host name is online. Does NOT push or modify ACL JSON — that stays an
# infrastructure operator responsibility.
#
# Usage:
#   COREMCP_TAILSCALE_HOST=coremcp-api ./infra/scripts/tailscale-acl-validate.sh
#
# Exit codes:
#   0   tailscale CLI present, status returned, target host (if set) is online
#   1   any failure — message printed to stderr
set -euo pipefail

TARGET_HOST="${COREMCP_TAILSCALE_HOST:-}"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale-acl-validate FAIL: tailscale CLI not on PATH — install with 'brew install --cask tailscale' (macOS) or equivalent" >&2
  exit 1
fi

# `tailscale status --json` returns the running tailnet state. If the CLI is
# installed but not logged in, this returns BackendState != "Running".
if ! status_json=$(tailscale status --json 2>&1); then
  echo "tailscale-acl-validate FAIL: 'tailscale status' refused (not logged in?)" >&2
  echo "$status_json" >&2
  exit 1
fi

backend=$(echo "$status_json" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("BackendState", "Unknown"))' 2>/dev/null || echo "ParseError")
if [[ "$backend" != "Running" ]]; then
  echo "tailscale-acl-validate FAIL: BackendState=$backend (expected Running) — run 'tailscale up' first" >&2
  exit 1
fi

self_name=$(echo "$status_json" | python3 -c 'import sys, json; d=json.load(sys.stdin); print(d.get("Self", {}).get("HostName", "unknown"))' 2>/dev/null || echo "unknown")
echo "tailscale-acl-validate OK: BackendState=Running, Self=$self_name"

if [[ -z "$TARGET_HOST" ]]; then
  echo "  (COREMCP_TAILSCALE_HOST unset — host reachability check skipped)"
  exit 0
fi

# Check the target host is in the peer set.
peer_match=$(echo "$status_json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
target = '$TARGET_HOST'
for peer in (data.get('Peer') or {}).values():
    if peer.get('HostName') == target:
        online = peer.get('Online')
        print('online' if online else 'offline')
        sys.exit(0)
print('not-found')
" 2>/dev/null)

case "$peer_match" in
  online)
    echo "tailscale-acl-validate OK: peer '$TARGET_HOST' is online"
    ;;
  offline)
    echo "tailscale-acl-validate FAIL: peer '$TARGET_HOST' is offline" >&2
    exit 1
    ;;
  not-found)
    echo "tailscale-acl-validate FAIL: peer '$TARGET_HOST' is not in the tailnet" >&2
    exit 1
    ;;
  *)
    echo "tailscale-acl-validate FAIL: peer lookup failed (output: $peer_match)" >&2
    exit 1
    ;;
esac
