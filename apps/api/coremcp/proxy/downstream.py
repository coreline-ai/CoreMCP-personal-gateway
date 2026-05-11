from typing import Any

import httpx


class DownstreamMcpError(RuntimeError):
    def __init__(self, message: str, *, code: int = -32000) -> None:
        super().__init__(message)
        self.code = code


class DownstreamToolError(DownstreamMcpError):
    """Downstream returned a JSON-RPC error for a tools/call execution.

    CoreMCP surfaces these as MCP tool-level errors (`result.isError=true`),
    not protocol-level JSON-RPC errors.
    """


class DownstreamMcpClient:
    """JSON-RPC client for the P0 fake downstream MCP server.

    The CoreMCP Authorization header is intentionally never forwarded. Only
    MCP/session/content negotiation headers that are safe for downstream are
    copied into the outgoing request.
    """

    def __init__(self, url: str, client: httpx.AsyncClient) -> None:
        self.url = url
        self.client = client

    async def request(
        self,
        *,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: Any = 1,
        protocol_version: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if protocol_version:
            headers["MCP-Protocol-Version"] = protocol_version
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        try:
            response = await self.client.post(self.url, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DownstreamMcpError(f"downstream request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise DownstreamMcpError("downstream returned non-JSON response") from exc

        if not isinstance(data, dict):
            raise DownstreamMcpError("downstream returned invalid JSON-RPC response")
        if "error" in data:
            error = data.get("error") or {}
            code = error.get("code", -32000) if isinstance(error, dict) else -32000
            message = error.get("message", "downstream error") if isinstance(error, dict) else "downstream error"
            if method == "tools/call":
                raise DownstreamToolError(str(message), code=int(code))
            raise DownstreamMcpError(str(message), code=int(code))
        return data
