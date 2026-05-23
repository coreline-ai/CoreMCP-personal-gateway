#!/usr/bin/env bash
# redis-smoke — verify the configured Redis rate-limit backend is reachable.
#
# Reads COREMCP_RATE_LIMIT_REDIS_URL from the environment (or the running
# launchd plist if present) and runs `redis-cli ping`. If COREMCP_RATE_LIMIT_BACKEND
# is unset / "memory", prints an explanatory line and exits 0 — running this
# smoke is opt-in.
set -euo pipefail

BACKEND="${COREMCP_RATE_LIMIT_BACKEND:-memory}"
URL="${COREMCP_RATE_LIMIT_REDIS_URL:-}"

if [[ "$BACKEND" != "redis" ]]; then
  echo "redis-smoke: backend is '$BACKEND' (not 'redis') — nothing to check."
  exit 0
fi

if [[ -z "$URL" ]]; then
  echo "redis-smoke FAIL: COREMCP_RATE_LIMIT_BACKEND=redis but COREMCP_RATE_LIMIT_REDIS_URL is unset" >&2
  exit 1
fi

if ! command -v redis-cli >/dev/null 2>&1; then
  echo "redis-smoke FAIL: redis-cli not on PATH — install with 'brew install redis' or equivalent" >&2
  exit 1
fi

# `redis-cli -u <url> ping` is the canonical reachability check.
if ! response=$(redis-cli -u "$URL" ping 2>&1); then
  echo "redis-smoke FAIL: ping to $URL refused" >&2
  echo "$response" >&2
  exit 1
fi

if [[ "$response" != "PONG" ]]; then
  echo "redis-smoke FAIL: unexpected ping response: $response" >&2
  exit 1
fi

echo "redis-smoke OK: $URL → PONG"
