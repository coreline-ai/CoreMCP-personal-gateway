#!/usr/bin/env bash
set -euo pipefail

WEB_URL="${COREMCP_WEB_URL:-http://127.0.0.1:3003}"

if [[ -n "${PWCLI:-}" ]]; then
  PW=("$PWCLI")
elif command -v npx >/dev/null 2>&1; then
  PW=(npx --yes --package @playwright/cli playwright-cli)
else
  echo "npx or PWCLI is required for Playwright route smoke" >&2
  exit 69
fi

shopt -s nullglob
rm -f .playwright-cli/console-*.log

cleanup() {
  "${PW[@]}" close >/dev/null 2>&1 || true
}
trap cleanup EXIT

check_security_headers() {
  local headers csp script_src style_src
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required for CSP/header smoke" >&2
    exit 69
  fi

  headers="$(curl -fsS -D - -o /dev/null "$WEB_URL/services" | tr -d '\r')"
  csp="$(printf '%s\n' "$headers" | grep -i '^content-security-policy:' | sed -E 's/^[^:]+:[[:space:]]*//' | head -n 1 || true)"
  if [[ -z "$csp" ]]; then
    echo "missing Content-Security-Policy header" >&2
    exit 1
  fi

  script_src="$(printf '%s' "$csp" | tr ';' '\n' | grep -i '^[[:space:]]*script-src[[:space:]]' | head -n 1 || true)"
  style_src="$(printf '%s' "$csp" | tr ';' '\n' | grep -i '^[[:space:]]*style-src[[:space:]]' | head -n 1 || true)"
  if [[ -z "$script_src" || "$script_src" != *"'nonce-"* ]]; then
    echo "script-src nonce is missing: $csp" >&2
    exit 1
  fi
  if [[ "$script_src" == *"'unsafe-inline'"* ]]; then
    echo "script-src must not include unsafe-inline: $script_src" >&2
    exit 1
  fi
  if [[ "$style_src" == *"'unsafe-inline'"* ]]; then
    echo "style-src must not include unsafe-inline: $style_src" >&2
    exit 1
  fi
  if [[ "$csp" != *"frame-ancestors 'none'"* ]]; then
    echo "frame-ancestors 'none' is missing: $csp" >&2
    exit 1
  fi
  if ! printf '%s\n' "$headers" | grep -qi '^x-content-type-options:[[:space:]]*nosniff'; then
    echo "missing X-Content-Type-Options: nosniff" >&2
    exit 1
  fi
  if ! printf '%s\n' "$headers" | grep -qi '^x-frame-options:[[:space:]]*DENY'; then
    echo "missing X-Frame-Options: DENY" >&2
    exit 1
  fi
  echo "PASS security headers"
}

check_path() {
  local path="$1"
  "${PW[@]}" run-code "async (page) => { const actual = new URL(page.url()).pathname; if (actual !== '$path') throw new Error('expected $path, got ' + actual); }" >/dev/null
  echo "PASS route $path"
}

click_route() {
  local path="$1"
  "${PW[@]}" run-code "async (page) => { const link = page.locator('a[href=\"$path\"]').first(); await link.waitFor({ state: 'visible', timeout: 5000 }); await link.click(); await page.waitForURL((url) => url.pathname === '$path', { timeout: 5000 }); }" >/dev/null
}

check_security_headers

"${PW[@]}" open "$WEB_URL/services" >/dev/null
check_path "/services"

for path in /toolbox /clients /settings /playground /simulator /logs; do
  click_route "$path"
  check_path "$path"
done

console_logs=(.playwright-cli/console-*.log)
if (( ${#console_logs[@]} > 0 )); then
  filtered_console_log="$(mktemp "${TMPDIR:-/tmp}/coremcp-web-route-smoke-console.XXXXXX")"
  # Playwright CLI injects a tiny inline probe script for its own page commands.
  # CoreMCP intentionally blocks that probe with nonce CSP, so the browser logs
  # one "Executing inline script violates..." error even when the app is healthy.
  # Keep failing on all other console output so real route regressions remain
  # visible.
  if ! grep -vE "Executing inline script violates the following Content Security Policy directive 'script-src 'self' 'nonce-[^']+''.*@ ${WEB_URL%/}/services:0$" "${console_logs[@]}" >"$filtered_console_log"; then
    :
  fi
  if [[ -s "$filtered_console_log" ]]; then
    cat "$filtered_console_log" >&2
    rm -f "$filtered_console_log"
    exit 1
  fi
  rm -f "$filtered_console_log"
  echo "PASS console logs"
fi
