from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .git_runner import GitRunError
from .security import GitWorkspaceSecurityError, resolve_root
from .tools import (
    repo_blame,
    repo_branch_list,
    repo_diff,
    repo_list,
    repo_log,
    repo_recent_activity,
    repo_status,
)

JSONRPC_VERSION = "2.0"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-06-18", "2025-11-25"}
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
LATEST_PROTOCOL_VERSION = "2025-11-25"
SERVER_VERSION = "0.1.0"

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def object_schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _read_only_tool(name: str, title: str, description: str, input_schema: dict[str, Any]) -> dict[str, Any]:
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
    _read_only_tool(
        "repo_list",
        "List repositories",
        "List git repositories under GIT_WORKSPACE_ROOT with branch / dirty / ahead / behind / last_commit_at.",
        object_schema({"pattern": {"type": "string", "description": "Optional substring filter on repo name."}}),
    ),
    _read_only_tool(
        "repo_status",
        "Show repository status",
        "Return branch, dirty flag, and untracked / staged / modified file lists for one repository.",
        object_schema({"name": {"type": "string"}}, required=["name"]),
    ),
    _read_only_tool(
        "repo_log",
        "Show commit log",
        "Return recent commits for one repository (default limit=20, max 200).",
        object_schema(
            {
                "name": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                "since": {"type": "string", "description": "Optional `git log --since=` value, e.g. '7 days ago'."},
                "author": {"type": "string"},
            },
            required=["name"],
        ),
    ),
    _read_only_tool(
        "repo_branch_list",
        "List branches",
        "Return local branches (and optionally origin remotes) with HEAD sha and last commit timestamp.",
        object_schema(
            {
                "name": {"type": "string"},
                "include_remote": {"type": "boolean", "default": False},
            },
            required=["name"],
        ),
    ),
    _read_only_tool(
        "repo_diff",
        "Show diff",
        "Return git diff text and short stats for one ref, with secret redaction and a default 64KB body cap.",
        object_schema(
            {
                "name": {"type": "string"},
                "ref": {"type": "string", "default": "HEAD"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "truncate_bytes": {"type": "integer", "minimum": 1024, "maximum": 1000000, "default": 65536},
            },
            required=["name"],
        ),
    ),
    _read_only_tool(
        "repo_blame",
        "Blame file",
        "Return git blame line-by-line for one file with optional line range and a 1000-line cap.",
        object_schema(
            {
                "name": {"type": "string"},
                "path": {"type": "string"},
                "line_start": {"type": "integer", "minimum": 1},
                "line_end": {"type": "integer", "minimum": 1},
            },
            required=["name", "path"],
        ),
    ),
    _read_only_tool(
        "repo_recent_activity",
        "Recent activity",
        "Aggregate commits / files_changed / dominant author per repository within the last N days.",
        object_schema({"days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 7}}),
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


class GitWorkspaceMcpServer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.handlers: dict[str, ToolHandler] = {
            "repo_list": self._repo_list,
            "repo_status": self._repo_status,
            "repo_log": self._repo_log,
            "repo_branch_list": self._repo_branch_list,
            "repo_diff": self._repo_diff,
            "repo_blame": self._repo_blame,
            "repo_recent_activity": self._repo_recent_activity,
        }

    @classmethod
    def from_env(cls) -> "GitWorkspaceMcpServer":
        return cls(resolve_root(os.environ.get("GIT_WORKSPACE_ROOT")))

    async def dispatch(self, payload: dict[str, Any]) -> dict[str, Any] | None:
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
            protocol_version = (
                requested
                if requested in SUPPORTED_PROTOCOL_VERSIONS
                else (DEFAULT_PROTOCOL_VERSION if requested is None else LATEST_PROTOCOL_VERSION)
            )
            return json_rpc_result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "Git Workspace MCP", "version": SERVER_VERSION},
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
                return json_rpc_result(request_id, await handler(arguments))
            except GitWorkspaceSecurityError as exc:
                return json_rpc_error(request_id, -32602, str(exc), {"tool": name})
            except GitRunError as exc:
                return json_rpc_error(request_id, -32000, str(exc), {"tool": name, "stderr": exc.stderr[:512]})
        if method == "ping":
            return json_rpc_result(request_id, {})
        return json_rpc_error(request_id, -32601, "Method not found", {"method": method})

    async def _repo_list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = arguments.get("pattern") if isinstance(arguments.get("pattern"), str) else None
        payload = await repo_list(self.root, pattern=pattern)
        return text_result(f"{payload['count']} git repos under {self.root}", payload)

    async def _repo_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = _required_str(arguments, "name")
        payload = await repo_status(self.root, name=name)
        status_word = "dirty" if payload["dirty"] else "clean"
        return text_result(f"{name} on {payload['branch']} is {status_word}", payload)

    async def _repo_log(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = _required_str(arguments, "name")
        limit = _optional_int(arguments, "limit", 20)
        since = arguments.get("since") if isinstance(arguments.get("since"), str) else None
        author = arguments.get("author") if isinstance(arguments.get("author"), str) else None
        payload = await repo_log(self.root, name=name, limit=limit, since=since, author=author)
        return text_result(f"{payload['count']} commit(s) for {name}", payload)

    async def _repo_branch_list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = _required_str(arguments, "name")
        include_remote = bool(arguments.get("include_remote", False))
        payload = await repo_branch_list(self.root, name=name, include_remote=include_remote)
        return text_result(f"{payload['count']} branch(es) for {name}", payload)

    async def _repo_diff(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = _required_str(arguments, "name")
        ref = arguments.get("ref", "HEAD")
        paths_value = arguments.get("paths")
        paths_list = paths_value if isinstance(paths_value, list) else None
        truncate_bytes = _optional_int(arguments, "truncate_bytes", 65_536)
        payload = await repo_diff(self.root, name=name, ref=ref, paths=paths_list, truncate_bytes=truncate_bytes)
        stat = payload["stat"]
        return text_result(
            f"{name} diff {payload['ref']} — {stat['files']} files, +{stat['insertions']} -{stat['deletions']}",
            payload,
        )

    async def _repo_blame(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = _required_str(arguments, "name")
        path = _required_str(arguments, "path")
        line_start = arguments.get("line_start") if isinstance(arguments.get("line_start"), int) and not isinstance(arguments.get("line_start"), bool) else None
        line_end = arguments.get("line_end") if isinstance(arguments.get("line_end"), int) and not isinstance(arguments.get("line_end"), bool) else None
        payload = await repo_blame(self.root, name=name, path=path, line_start=line_start, line_end=line_end)
        return text_result(f"{name} blame {path} — {payload['count']} line(s)", payload)

    async def _repo_recent_activity(self, arguments: dict[str, Any]) -> dict[str, Any]:
        days = _optional_int(arguments, "days", 7)
        payload = await repo_recent_activity(self.root, days=days)
        return text_result(f"{payload['count']} active repo(s) in the last {payload['days']} day(s)", payload)


def _required_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GitWorkspaceSecurityError(f"{key} is required")
    return value.strip()


def _optional_int(arguments: dict[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


async def _serve_async(server: GitWorkspaceMcpServer) -> int:
    loop = asyncio.get_running_loop()
    while True:
        raw_line = await loop.run_in_executor(None, sys.stdin.readline)
        if not raw_line:
            return 0
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
                response = await server.dispatch(payload)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def main() -> int:
    try:
        server = GitWorkspaceMcpServer.from_env()
    except Exception as exc:
        print(f"Git Workspace MCP startup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Git Workspace MCP ready root={server.root} session={uuid.uuid4().hex}", file=sys.stderr)
    return asyncio.run(_serve_async(server))
