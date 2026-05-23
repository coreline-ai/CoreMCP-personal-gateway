#!/usr/bin/env bash
# register-mcp — generic CoreMCP stdio MCP registration macro.
#
# Reads MCP definition from environment variables, then creates/updates the
# service via /v1/mcp-services, validates it, and adds it to the default
# toolbox. Same body shape as register-project-docs-mcp.sh /
# register-git-workspace-mcp.sh, but parameterised so a new MCP can be tried
# without copying a new script.
#
# Required:
#   MCP_NAME           Human-readable name
#   MCP_SLUG           Unique slug (snake_case)
#   MCP_COMMAND        Absolute path to stdio command (e.g. /opt/homebrew/bin/python3)
#
# Optional:
#   MCP_DESCRIPTION    Description shown in admin UI
#   MCP_ARGS           JSON array of command arguments (default "[]")
#   MCP_CWD            Working directory (absolute)
#   MCP_ENV            JSON object of extra env vars (default "{}")
#   MCP_CATEGORY       Category tag (default "personal-docs")
#   MCP_IDLE_TIMEOUT   stdio idle timeout in seconds (default 300)
#
# Inherited:
#   COREMCP_API_URL            (default http://127.0.0.1:8787)
#   COREMCP_ADMIN_TOKEN_FILE   (default ~/.coremcp/admin-token)
#
# Example:
#   MCP_NAME="My MCP" MCP_SLUG="my_mcp" MCP_COMMAND=/usr/bin/python3 \
#     MCP_ARGS='["-m", "my_mcp.main"]' MCP_CWD=/path/to/my_mcp \
#     infra/scripts/register-mcp.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
API_URL="${COREMCP_API_URL:-http://127.0.0.1:8787}"
ADMIN_TOKEN_FILE="${COREMCP_ADMIN_TOKEN_FILE:-$HOME/.coremcp/admin-token}"

: "${MCP_NAME:?MCP_NAME is required (human-readable name)}"
: "${MCP_SLUG:?MCP_SLUG is required (unique slug, snake_case)}"
: "${MCP_COMMAND:?MCP_COMMAND is required (absolute path to stdio command)}"

MCP_DESCRIPTION="${MCP_DESCRIPTION:-Custom stdio MCP service ($MCP_SLUG).}"
MCP_ARGS="${MCP_ARGS:-[]}"
MCP_CWD="${MCP_CWD:-}"
MCP_ENV="${MCP_ENV:-{\}}"
MCP_CATEGORY="${MCP_CATEGORY:-personal-docs}"
MCP_IDLE_TIMEOUT="${MCP_IDLE_TIMEOUT:-300}"

if [[ ! -s "$ADMIN_TOKEN_FILE" ]]; then
  echo "Admin token not found: $ADMIN_TOKEN_FILE" >&2
  exit 65
fi
if [[ ! -x "$MCP_COMMAND" && ! -f "$MCP_COMMAND" ]]; then
  echo "MCP_COMMAND not found or not executable: $MCP_COMMAND" >&2
  exit 66
fi

export COREMCP_API_URL="$API_URL"
export COREMCP_ADMIN_TOKEN_VALUE
COREMCP_ADMIN_TOKEN_VALUE="$(tr -d '\r\n' < "$ADMIN_TOKEN_FILE")"
export MCP_NAME MCP_SLUG MCP_DESCRIPTION MCP_COMMAND MCP_ARGS MCP_CWD MCP_ENV MCP_CATEGORY MCP_IDLE_TIMEOUT

python3 - <<'PY'
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

api_url = os.environ["COREMCP_API_URL"].rstrip("/")
token = os.environ["COREMCP_ADMIN_TOKEN_VALUE"]
slug = os.environ["MCP_SLUG"]
name = os.environ["MCP_NAME"]
description = os.environ["MCP_DESCRIPTION"]
command = os.environ["MCP_COMMAND"]
category = os.environ["MCP_CATEGORY"]
idle_timeout = int(os.environ["MCP_IDLE_TIMEOUT"])

try:
    args = json.loads(os.environ["MCP_ARGS"])
    assert isinstance(args, list), "MCP_ARGS must be a JSON array"
except (json.JSONDecodeError, AssertionError) as exc:
    print(f"FAIL parsing MCP_ARGS: {exc}", file=sys.stderr)
    raise SystemExit(2)

try:
    env_extra = json.loads(os.environ["MCP_ENV"]) if os.environ["MCP_ENV"].strip() else {}
    assert isinstance(env_extra, dict), "MCP_ENV must be a JSON object"
except (json.JSONDecodeError, AssertionError) as exc:
    print(f"FAIL parsing MCP_ENV: {exc}", file=sys.stderr)
    raise SystemExit(2)

cwd = os.environ.get("MCP_CWD") or None
headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def request(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None
    local_headers = dict(headers)
    if body is not None:
        local_headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{api_url}{path}", method=method, headers=local_headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"error": raw}
        return exc.code, payload


def require_ok(code: int, payload: Any, action: str) -> Any:
    if code >= 400:
        print(f"FAIL {action}: HTTP {code} {payload}", file=sys.stderr)
        raise SystemExit(1)
    return payload


payload = {
    "name": name,
    "slug": slug,
    "description": description,
    "transport_type": "stdio",
    "endpoint_url": f"stdio://{slug}",
    "auth_type": "none",
    "category": category,
    "stdio_command": command,
    "stdio_args": args,
    "stdio_env": env_extra,
    "stdio_cwd": cwd,
    "stdio_idle_timeout_seconds": idle_timeout,
}

code, services = request("GET", "/v1/mcp-services?limit=100")
require_ok(code, services, "list services")
service = next((item for item in services.get("items", []) if item.get("slug") == slug), None)
if service is None:
    code, service = request("POST", "/v1/mcp-services", payload)
    service = require_ok(code, service, "create service")
    print(f"created service {service['id']} slug={slug}")
else:
    service_id = service["id"]
    code, service = request("PATCH", f"/v1/mcp-services/{service_id}", payload)
    service = require_ok(code, service, "update service")
    print(f"updated service {service['id']} slug={slug}")

service_id = service["id"]
code, validation = request("POST", f"/v1/mcp-services/{service_id}/validate")
validation = require_ok(code, validation, "validate service")
print(f"validated service {service_id}: tools={validation.get('tool_count') or len(validation.get('tools', []))}")

code, toolboxes = request("GET", "/v1/toolboxes?limit=100")
require_ok(code, toolboxes, "list toolboxes")
default_box = next((item for item in toolboxes.get("items", []) if item.get("is_default")), None) or {"id": "tbx_default"}
toolbox_id = default_box["id"]
code, toolbox = request("GET", f"/v1/toolboxes/{urllib.parse.quote(toolbox_id)}")
toolbox = require_ok(code, toolbox, "get toolbox")
existing = next((item for item in toolbox.get("items", []) if item.get("service_id") == service_id), None)
if existing is None:
    code, item = request("POST", f"/v1/toolboxes/{urllib.parse.quote(toolbox_id)}/items", {"service_id": service_id, "enabled": True})
    item = require_ok(code, item, "add toolbox item")
    print(f"added to toolbox {toolbox_id}: item={item.get('id')}")
elif not existing.get("enabled"):
    code, item = request("PATCH", f"/v1/toolboxes/{urllib.parse.quote(toolbox_id)}/items/{urllib.parse.quote(existing['id'])}", {"enabled": True})
    item = require_ok(code, item, "enable toolbox item")
    print(f"enabled toolbox item {existing['id']}")
else:
    print(f"already in toolbox {toolbox_id}: item={existing.get('id')}")
PY
