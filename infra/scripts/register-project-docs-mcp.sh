#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
API_URL="${COREMCP_API_URL:-http://127.0.0.1:8787}"
ADMIN_TOKEN_FILE="${COREMCP_ADMIN_TOKEN_FILE:-$HOME/.coremcp/admin-token}"
PROJECT_DOCS_ROOT="${PROJECT_DOCS_ROOT:-/Users/hwanchoi/projects}"
PROJECT_DOCS_APP_DIR="${PROJECT_DOCS_APP_DIR:-$PROJECT_ROOT/apps/project-docs-mcp}"
PYTHON3="${PROJECT_DOCS_PYTHON:-$(command -v python3)}"

if [[ ! -s "$ADMIN_TOKEN_FILE" ]]; then
  echo "Admin token not found: $ADMIN_TOKEN_FILE" >&2
  exit 65
fi
if [[ ! -d "$PROJECT_DOCS_ROOT" ]]; then
  echo "PROJECT_DOCS_ROOT is not a directory: $PROJECT_DOCS_ROOT" >&2
  exit 66
fi
if [[ ! -d "$PROJECT_DOCS_APP_DIR/project_docs_mcp" ]]; then
  echo "Project Docs MCP package not found: $PROJECT_DOCS_APP_DIR" >&2
  exit 66
fi

export COREMCP_API_URL="$API_URL"
export COREMCP_ADMIN_TOKEN_VALUE
COREMCP_ADMIN_TOKEN_VALUE="$(tr -d '\r\n' < "$ADMIN_TOKEN_FILE")"
export PROJECT_DOCS_ROOT PROJECT_DOCS_APP_DIR PYTHON3

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
project_root = os.environ["PROJECT_DOCS_ROOT"]
app_dir = os.environ["PROJECT_DOCS_APP_DIR"]
python3 = os.environ["PYTHON3"]
slug = "project_docs"

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
    "name": "Project Docs MCP",
    "slug": slug,
    "description": "Read-only README.md/Markdown search for /Users/hwanchoi/projects.",
    "transport_type": "stdio",
    "endpoint_url": "stdio://project_docs",
    "auth_type": "none",
    "category": "personal-docs",
    "stdio_command": python3,
    "stdio_args": ["-m", "project_docs_mcp.main"],
    "stdio_env": {"PROJECT_DOCS_ROOT": project_root},
    "stdio_cwd": app_dir,
    "stdio_idle_timeout_seconds": 300,
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

code, tools = request("GET", "/v1/playground/tools/list?limit=200")
require_ok(code, tools, "playground tools")
project_tools = [item.get("name") or item.get("exposed_name") for item in tools.get("items", []) if "project_docs" in str(item.get("name") or item.get("exposed_name"))]
print("project docs tools:", ", ".join(project_tools[:10]) or "<none visible yet>")
PY
