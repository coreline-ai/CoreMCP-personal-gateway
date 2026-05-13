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
  "${PW[@]}" eval "() => { if (location.pathname !== '$path') throw new Error('expected $path, got ' + location.pathname); return location.pathname; }" >/dev/null
  echo "PASS route $path"
}

click_route() {
  local path="$1"
  "${PW[@]}" eval "() => { const link = document.querySelector('a[href=\"$path\"]'); if (!link) throw new Error('missing link $path'); link.click(); }" >/dev/null
}

check_security_headers

"${PW[@]}" open "$WEB_URL/services" >/dev/null
check_path "/services"

for path in /toolbox /clients /settings /playground /logs; do
  click_route "$path"
  check_path "$path"
done

console_logs=(.playwright-cli/console-*.log)
if (( ${#console_logs[@]} > 0 )); then
  cat "${console_logs[@]}" >&2
  exit 1
fi
