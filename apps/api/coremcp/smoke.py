from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

import httpx

from coremcp.main import create_app
from coremcp.settings import Settings

TOKEN = "cmcp_admin_smoke"


def _rpc(method: str, params: dict[str, Any] | None = None, request_id: int | str = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


async def _downstream(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content.decode("utf-8"))
    method = body.get("method")
    request_id = body.get("id")
    if method == "initialize":
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-11-25"}})
    if method == "tools/list":
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "title": "Echo",
                            "description": "Echo smoke input.",
                            "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}},
                            "annotations": {"readOnlyHint": True},
                        }
                    ],
                    "nextCursor": None,
                },
            },
        )
    if method == "tools/call":
        params = body.get("params") or {}
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": str((params.get("arguments") or {}).get("message", ""))}], "isError": False},
            },
        )
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "result": {}})


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        downstream_client = httpx.AsyncClient(transport=httpx.MockTransport(_downstream))
        app = create_app(
            settings=Settings(
                COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
                COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
                COREMCP_DB_PATH=tmp_path / "coremcp.sqlite3",
                FAKE_MCP_URL="http://fake.local/mcp",
                COREMCP_SSRF_ALLOW_HOSTS="fake.local",
                COREMCP_SECRET_BACKEND="fernet",
                COREMCP_SECRETS_FILE=tmp_path / "secrets.json",
            ),
            http_client=downstream_client,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                headers = {"Authorization": f"Bearer {TOKEN}"}
                ready = await client.get("/ready")
                ready.raise_for_status()
                init = await client.post(
                    "/mcp",
                    headers=headers,
                    json=_rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}}),
                )
                init.raise_for_status()
                session_id = init.headers["Mcp-Session-Id"]
                listed = await client.post("/mcp", headers={**headers, "Mcp-Session-Id": session_id}, json=_rpc("tools/list", {}, 2))
                listed.raise_for_status()
                tool_name = listed.json()["result"]["tools"][0]["name"]
                called = await client.post(
                    "/mcp",
                    headers={**headers, "Mcp-Session-Id": session_id},
                    json=_rpc("tools/call", {"name": tool_name, "arguments": {"message": "smoke ok"}}, 3),
                )
                called.raise_for_status()
                print(f"smoke ok: {tool_name} -> {called.json()['result']['content'][0]['text']}")
        await downstream_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
