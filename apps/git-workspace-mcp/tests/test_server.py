from __future__ import annotations

from pathlib import Path

from git_workspace_mcp.server import GitWorkspaceMcpServer


async def test_initialize_returns_capabilities(workspace_root: Path) -> None:
    server = GitWorkspaceMcpServer(workspace_root)
    response = await server.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
    )
    assert response is not None
    result = response["result"]
    assert result["protocolVersion"] == "2025-11-25"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "Git Workspace MCP"


async def test_tools_list_exposes_seven_tools(workspace_root: Path) -> None:
    server = GitWorkspaceMcpServer(workspace_root)
    response = await server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert response is not None
    tools = response["result"]["tools"]
    names = sorted(tool["name"] for tool in tools)
    assert names == [
        "repo_blame",
        "repo_branch_list",
        "repo_diff",
        "repo_list",
        "repo_log",
        "repo_recent_activity",
        "repo_status",
    ]
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False


async def test_tools_call_repo_list(workspace_root: Path) -> None:
    server = GitWorkspaceMcpServer(workspace_root)
    response = await server.dispatch(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "repo_list", "arguments": {}}}
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is False
    payload = result["structuredContent"]
    assert payload["count"] >= 2


async def test_tools_call_unknown_returns_error(workspace_root: Path) -> None:
    server = GitWorkspaceMcpServer(workspace_root)
    response = await server.dispatch(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "no_such_tool", "arguments": {}}}
    )
    assert response is not None
    assert response.get("error", {}).get("code") == -32602


async def test_security_error_returns_jsonrpc_error(workspace_root: Path) -> None:
    server = GitWorkspaceMcpServer(workspace_root)
    response = await server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "repo_status", "arguments": {"name": "not_a_repo"}},
        }
    )
    assert response is not None
    assert response.get("error", {}).get("code") == -32602


async def test_git_run_error_stderr_is_redacted(workspace_root: Path) -> None:
    server = GitWorkspaceMcpServer(workspace_root)
    secret_ref = "sk-stderrsecret999999"
    response = await server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "repo_diff", "arguments": {"name": "alpha", "ref": secret_ref}},
        }
    )
    assert response is not None
    error = response.get("error", {})
    assert error.get("code") == -32000
    stderr = error.get("data", {}).get("stderr", "")
    assert secret_ref not in stderr
    assert "***REDACTED***" in stderr
