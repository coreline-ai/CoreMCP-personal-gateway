from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .indexer import list_docs, list_projects, read_doc, search_docs, summarize_project
from .security import ProjectDocsSecurityError, resolve_root

JSONRPC_VERSION = "2.0"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-06-18", "2025-11-25"}
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
LATEST_PROTOCOL_VERSION = "2025-11-25"
SERVER_VERSION = "0.1.0"

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def object_schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def tool(name: str, title: str, description: str, input_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": input_schema,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


TOOLS = [
    tool(
        "project_list",
        "List projects",
        "List direct child projects under PROJECT_DOCS_ROOT with README/md counts.",
        object_schema({"query": {"type": "string", "description": "Optional project name filter."}}),
    ),
    tool(
        "project_docs_list",
        "List project Markdown docs",
        "List README and Markdown files for one project.",
        object_schema(
            {
                "project": {"type": "string", "description": "Direct child project directory name."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
            },
            required=["project"],
        ),
    ),
    tool(
        "project_docs_search",
        "Search project Markdown docs",
        "Search Markdown files by keyword across all projects or one project.",
        object_schema(
            {
                "query": {"type": "string"},
                "project": {"type": "string", "description": "Optional direct child project directory name."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            required=["query"],
        ),
    ),
    tool(
        "project_doc_read",
        "Read one Markdown doc",
        "Read a single .md/.markdown file under one project with truncation.",
        object_schema(
            {
                "project": {"type": "string"},
                "path": {"type": "string", "description": "Path relative to the project root."},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 20000},
            },
            required=["project", "path"],
        ),
    ),
    tool(
        "project_summary",
        "Summarize project README",
        "Return README title/headings/snippet for one project.",
        object_schema({"project": {"type": "string"}}, required=["project"]),
    ),
]


def json_rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def json_rpc_error(request_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def text_result(text: str, structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
        "isError": False,
    }


class ProjectDocsMcpServer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.handlers: dict[str, ToolHandler] = {
            "project_list": self._project_list,
            "project_docs_list": self._project_docs_list,
            "project_docs_search": self._project_docs_search,
            "project_doc_read": self._project_doc_read,
            "project_summary": self._project_summary,
        }

    @classmethod
    def from_env(cls) -> "ProjectDocsMcpServer":
        return cls(resolve_root(os.environ.get("PROJECT_DOCS_ROOT")))

    def dispatch(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        request_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}

        if payload.get("jsonrpc") != JSONRPC_VERSION or not isinstance(method, str):
            return json_rpc_error(request_id, -32600, "Invalid Request")
        if not isinstance(params, dict):
            return json_rpc_error(request_id, -32602, "Invalid params")

        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "initialize":
            requested = params.get("protocolVersion")
            protocol_version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else (DEFAULT_PROTOCOL_VERSION if requested is None else LATEST_PROTOCOL_VERSION)
            return json_rpc_result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "Project Docs MCP", "version": SERVER_VERSION},
                },
            )
        if method == "tools/list":
            return json_rpc_result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return json_rpc_error(request_id, -32602, "Invalid params", {"reason": "name and object arguments are required"})
            handler = self.handlers.get(name)
            if handler is None:
                return json_rpc_error(request_id, -32602, "Unknown tool", {"tool": name})
            try:
                return json_rpc_result(request_id, handler(arguments))
            except (ProjectDocsSecurityError, ValueError, OSError) as exc:
                return json_rpc_error(request_id, -32602, str(exc), {"tool": name})
        if method == "ping":
            return json_rpc_result(request_id, {})
        return json_rpc_error(request_id, -32601, "Method not found", {"method": method})

    def _project_list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = list_projects(self.root, query=arguments.get("query") if isinstance(arguments.get("query"), str) else None)
        return text_result(f"{payload['count']} projects under {self.root}", payload)

    def _project_docs_list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = _required_str(arguments, "project")
        limit = _optional_int(arguments, "limit", 200)
        payload = list_docs(self.root, project=project, limit=limit)
        return text_result(f"{payload['count']} Markdown docs in {project}", payload)

    def _project_docs_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = _required_str(arguments, "query")
        project = arguments.get("project") if isinstance(arguments.get("project"), str) and arguments.get("project") else None
        limit = _optional_int(arguments, "limit", 10)
        payload = search_docs(self.root, query=query, project=project, limit=limit)
        scope = project or "all projects"
        return text_result(f"{payload['count']} matches for {query!r} in {scope}", payload)

    def _project_doc_read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = _required_str(arguments, "project")
        path = _required_str(arguments, "path")
        max_chars = _optional_int(arguments, "max_chars", 20_000)
        payload = read_doc(self.root, project=project, path=path, max_chars=max_chars)
        return text_result(payload["content"], payload)

    def _project_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = _required_str(arguments, "project")
        payload = summarize_project(self.root, project=project)
        return text_result(f"{payload['title']} — {payload['md_count']} Markdown docs", payload)


def _required_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional_int(arguments: dict[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def serve(server: ProjectDocsMcpServer) -> int:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            response = json_rpc_error(None, -32700, "Parse error")
        else:
            if not isinstance(payload, dict):
                response = json_rpc_error(None, -32600, "Invalid Request")
            else:
                response = server.dispatch(payload)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


def main() -> int:
    try:
        server = ProjectDocsMcpServer.from_env()
    except Exception as exc:
        print(f"Project Docs MCP startup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Project Docs MCP ready root={server.root} session={uuid.uuid4().hex}", file=sys.stderr)
    return serve(server)
