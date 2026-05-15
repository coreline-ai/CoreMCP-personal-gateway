#!/usr/bin/env bash
set -euo pipefail
WEB_URL="${COREMCP_WEB_URL:-http://localhost:3003}"
API_URL="${COREMCP_API_URL:-http://127.0.0.1:8787}"
cat <<CHECKLIST
CoreMCP mobile browser QA checklist

1. Open Web Admin on the mobile device:
   $WEB_URL
2. Confirm token login screen, Dashboard dark theme, menu readability.
3. Visit Services, Toolbox, Clients, Settings, Playground, Logs.
4. In Playground, call a read-only demo tool and verify Logs shows the call.
5. Rotate/clear token only if you intentionally want to re-login.
6. API readiness reference:
   $API_URL/ready

If using Tailscale, set COREMCP_WEB_URL and COREMCP_API_URL to the Tailscale Serve/Caddy URLs and rerun this checklist.

Exit code 0 only means this checklist was printed. Record actual mobile pass/skip/fail evidence in TESTING.md after using a physical device/browser.
CHECKLIST
