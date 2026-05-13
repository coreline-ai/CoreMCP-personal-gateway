from typing import Any

import httpx

from .security import UrlSafetyChecker, UrlSafetyError, UrlSafetyResult


class DownstreamMcpError(RuntimeError):
    def __init__(self, message: str, *, code: int = -32000) -> None:
        super().__init__(message)
        self.code = code


class DownstreamToolError(DownstreamMcpError):
    """Downstream returned a JSON-RPC error for a tools/call execution.

    CoreMCP surfaces these as MCP tool-level errors (`result.isError=true`),
    not protocol-level JSON-RPC errors.
    """


class DownstreamTimeoutError(DownstreamMcpError):
    """Downstream did not complete within the configured timeout budget."""


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
        url: str | None = None,
        downstream_headers: dict[str, str] | None = None,
        url_safety_checker: UrlSafetyChecker | None = None,
        safety_result: UrlSafetyResult | None = None,
        expect_response: bool = True,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        request_url = url or self.url
        if url_safety_checker is not None:
            try:
                if safety_result is not None:
                    url_safety_checker.assert_same_safe_destination(safety_result, request_url)
                else:
                    url_safety_checker.assert_safe(request_url)
            except UrlSafetyError as exc:
                raise DownstreamMcpError(f"unsafe downstream endpoint: {exc}", code=-32003) from exc

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
        if correlation_id:
            headers["X-Request-ID"] = correlation_id
        if downstream_headers:
            for name, value in downstream_headers.items():
                # CoreMCP's own Authorization is never copied from the incoming
                # request. Only explicit service credentials may be attached by
                # registry/vault code.
                if name.lower() in {"authorization", "x-api-key"}:
                    headers[name] = value

        try:
            response = await self.client.post(request_url, json=payload, headers=headers, follow_redirects=False)
            if 300 <= response.status_code < 400:
                raise DownstreamMcpError("downstream redirect is not allowed", code=-32003)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise DownstreamTimeoutError("downstream request timed out", code=-32008) from exc
        except httpx.HTTPError as exc:
            raise DownstreamMcpError(f"downstream request failed: {exc}") from exc

        if not expect_response and (response.status_code in {202, 204} or not response.content):
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}

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
