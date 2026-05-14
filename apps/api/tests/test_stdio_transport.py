from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from coremcp.proxy import DownstreamMcpError, DownstreamTimeoutError, DownstreamToolError, StdioMcpTransport


SCRIPT = r'''
import json
import os
import sys


def send(message):
    print(json.dumps(message), flush=True)


for line in sys.stdin:
    try:
        request = json.loads(line)
    except Exception:
        continue

    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion"),
                "serverInfo": {"name": "stdio-fixture", "version": "0.1.0"},
            },
        })
    elif method == "tools/list":
        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {"name": "echo", "description": "Echo text.", "inputSchema": {"type": "object"}},
                    {"name": "env", "description": "Show selected env.", "inputSchema": {"type": "object"}},
                    {"name": "error", "description": "Raise error.", "inputSchema": {"type": "object"}},
                ]
            },
        })
    elif method == "tools/call":
        name = params.get("name")
        if name == "error":
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "fixture tool exploded"}})
        elif name == "env":
            keys = [
                "SAFE_TOKEN",
                "DOWNSTREAM_API_KEY",
                "COREMCP_ADMIN_TOKEN",
                "COREMCP_ADMIN_TOKEN_VALUE",
                "COREMCP_CLIENT_TOKEN",
                "COREMCP_CLIENT_TOKEN_VALUE",
                "Authorization",
                "authorization",
                "HTTP_AUTHORIZATION",
            ]
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": json.dumps({key: os.environ.get(key) for key in keys}, sort_keys=True)}]},
            })
        else:
            text = (params.get("arguments") or {}).get("text", "")
            send({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "echo:" + text}]}})
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    elif method == "stderr":
        sys.stderr.write(str(params.get("text", "")))
        sys.stderr.flush()
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    elif method == "crash":
        sys.stderr.write("fixture crash before response\n")
        sys.stderr.flush()
        sys.exit(7)
    elif method == "rpc_error":
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
    elif method == "mismatch":
        send({"jsonrpc": "2.0", "id": "other-id", "result": {"wrong": True}})
    elif method == "no_response":
        pass
    else:
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
'''


@pytest.fixture
def stdio_script(tmp_path: Path) -> Path:
    script = tmp_path / "fixture_mcp.py"
    script.write_text(textwrap.dedent(SCRIPT), encoding="utf-8")
    return script


async def _new_client(stdio_script: Path, **kwargs: Any) -> StdioMcpTransport:
    return StdioMcpTransport([sys.executable, str(stdio_script)], timeout=2.0, **kwargs)


@pytest.mark.asyncio
async def test_stdio_initialize_tools_list_tools_call_and_ping(stdio_script: Path) -> None:
    client = await _new_client(stdio_script)
    try:
        initialized = await client.request(
            "initialize",
            {"protocolVersion": "2025-03-26", "clientInfo": {"name": "test"}},
            request_id="init-1",
            protocol_version="2025-03-26",
            session_id="ignored-session",
            correlation_id="corr-1",
        )
        assert initialized["id"] == "init-1"
        assert initialized["result"]["protocolVersion"] == "2025-03-26"

        tools = await client.request("tools/list", request_id="list-1")
        assert [tool["name"] for tool in tools["result"]["tools"]] == ["echo", "env", "error"]

        called = await client.request(
            "tools/call",
            {"name": "echo", "arguments": {"text": "hello"}},
            request_id="call-1",
        )
        assert called["result"]["content"] == [{"type": "text", "text": "echo:hello"}]

        pong = await client.request("ping", request_id="ping-1")
        assert pong == {"jsonrpc": "2.0", "id": "ping-1", "result": {}}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stdio_tool_error_and_protocol_error(stdio_script: Path) -> None:
    client = await _new_client(stdio_script)
    try:
        with pytest.raises(DownstreamToolError) as tool_error:
            await client.request("tools/call", {"name": "error", "arguments": {}}, request_id="tool-error")
        assert tool_error.value.code == -32001
        assert "fixture tool exploded" in str(tool_error.value)

        with pytest.raises(DownstreamMcpError) as protocol_error:
            await client.request("rpc_error", request_id="rpc-error")
        assert protocol_error.value.code == -32601
        assert "Method not found" in str(protocol_error.value)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stdio_matches_response_id_and_times_out_on_missing_match(stdio_script: Path) -> None:
    client = await _new_client(stdio_script)
    try:
        with pytest.raises(DownstreamTimeoutError):
            await client.request("mismatch", request_id="expected-id", timeout=0.2)

        # The mismatched response was ignored and the transport remains usable.
        pong = await client.request("ping", request_id="after-timeout")
        assert pong["id"] == "after-timeout"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stdio_no_response_timeout(stdio_script: Path) -> None:
    client = await _new_client(stdio_script)
    try:
        with pytest.raises(DownstreamTimeoutError):
            await client.request("no_response", request_id="never", timeout=0.2)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stdio_expect_response_false_returns_empty_result(stdio_script: Path) -> None:
    client = await _new_client(stdio_script)
    try:
        response = await client.request("no_response", request_id="notify-1", expect_response=False)
        assert response == {"jsonrpc": "2.0", "id": "notify-1", "result": {}}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stdio_env_is_explicit_and_filters_coremcp_authorization_tokens(stdio_script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COREMCP_ADMIN_TOKEN", "parent-admin-token")
    monkeypatch.setenv("COREMCP_CLIENT_TOKEN", "parent-client-token")
    monkeypatch.setenv("Authorization", "Bearer parent")

    client = await _new_client(
        stdio_script,
        env={
            "SAFE_TOKEN": "safe",
            "DOWNSTREAM_API_KEY": "downstream-key",
            "COREMCP_ADMIN_TOKEN": "admin-secret",
            "COREMCP_ADMIN_TOKEN_VALUE": "admin-secret-value",
            "COREMCP_CLIENT_TOKEN": "client-secret",
            "COREMCP_CLIENT_TOKEN_VALUE": "client-secret-value",
            "Authorization": "Bearer explicit",
            "authorization": "Bearer lowercase",
            "HTTP_AUTHORIZATION": "Bearer http",
        },
    )
    try:
        response = await client.request("tools/call", {"name": "env", "arguments": {}}, request_id="env-1")
        env = json.loads(response["result"]["content"][0]["text"])
        assert env["SAFE_TOKEN"] == "safe"
        assert env["DOWNSTREAM_API_KEY"] == "downstream-key"
        assert env["COREMCP_ADMIN_TOKEN"] is None
        assert env["COREMCP_ADMIN_TOKEN_VALUE"] is None
        assert env["COREMCP_CLIENT_TOKEN"] is None
        assert env["COREMCP_CLIENT_TOKEN_VALUE"] is None
        assert env["Authorization"] is None
        assert env["authorization"] is None
        assert env["HTTP_AUTHORIZATION"] is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stdio_snapshot_exposes_runtime_state_and_stderr_tail(stdio_script: Path) -> None:
    client = await _new_client(stdio_script)
    try:
        initial = client.snapshot()
        assert initial["started_at"] is None
        assert initial["last_used_at"] is None
        assert initial["restart_count"] == 0
        assert initial["last_exit_code"] is None
        assert initial["last_error"] is None
        assert initial["is_running"] is False
        assert initial["stderr_tail"] == ""
        assert initial["pending_requests"] == 0

        before = time.time()
        await client.request("ping", request_id="snapshot-ping")
        await client.request("stderr", {"text": "x" * 9000}, request_id="stderr-1")

        snapshot = client.snapshot()
        assert snapshot["is_running"] is True
        assert client.is_running is True
        assert snapshot["started_at"] is not None
        assert snapshot["started_at"] >= before
        assert snapshot["last_used_at"] is not None
        assert snapshot["last_used_at"] >= snapshot["started_at"]
        assert snapshot["restart_count"] == 0
        assert snapshot["last_exit_code"] is None
        assert snapshot["last_error"] is None
        assert snapshot["pending_requests"] == 0
        assert isinstance(snapshot["stderr_tail"], str)
        assert len(snapshot["stderr_tail"].encode("utf-8")) <= 8192
        assert snapshot["stderr_tail"] == "x" * 8192
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stdio_reap_idle_stops_process_and_restarts_on_next_request(stdio_script: Path) -> None:
    client = await _new_client(stdio_script, idle_timeout_seconds=0.01)
    try:
        await client.request("ping", request_id="idle-before")
        before_reap = client.snapshot()
        assert before_reap["is_running"] is True
        assert before_reap["last_used_at"] is not None

        assert await client.reap_idle(now=before_reap["last_used_at"]) is False
        assert await client.reap_idle(now=before_reap["last_used_at"] + 1.0) is True

        reaped = client.snapshot()
        assert reaped["is_running"] is False
        assert reaped["last_exit_code"] is not None
        assert "idle timeout" in str(reaped["last_error"])

        pong = await client.request("ping", request_id="idle-after")
        assert pong["id"] == "idle-after"

        restarted = client.snapshot()
        assert restarted["is_running"] is True
        assert restarted["restart_count"] == 1
        assert restarted["started_at"] is not None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stdio_crash_records_exit_and_restarts_on_next_request(stdio_script: Path) -> None:
    client = await _new_client(stdio_script)
    try:
        with pytest.raises(DownstreamMcpError) as crash_error:
            await client.request("crash", request_id="crash-1")
        assert "exit code 7" in str(crash_error.value)

        crashed = client.snapshot()
        assert crashed["is_running"] is False
        assert crashed["last_exit_code"] == 7
        assert "exit code 7" in str(crashed["last_error"])
        assert "fixture crash before response" in crashed["stderr_tail"]
        assert crashed["restart_count"] == 0

        pong = await client.request("ping", request_id="after-crash")
        assert pong["id"] == "after-crash"

        restarted = client.snapshot()
        assert restarted["is_running"] is True
        assert restarted["restart_count"] == 1
        assert restarted["last_exit_code"] == 7
    finally:
        await client.aclose()
