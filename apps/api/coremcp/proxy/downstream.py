from typing import Any
from urllib.parse import urlparse

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

    def __init__(self, url: str, client: httpx.AsyncClient, *, max_response_bytes: int = 1024 * 1024) -> None:
        self.url = url
        self.client = client
        self.max_response_bytes = max(1, max_response_bytes)

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
        timeout: httpx.Timeout | float | None = None,
    ) -> dict[str, Any]:
        request_url = url or self.url
        if url_safety_checker is not None:
            try:
                if safety_result is not None:
                    safety_result = url_safety_checker.assert_same_safe_destination(safety_result, request_url)
                else:
                    safety_result = url_safety_checker.assert_safe(request_url)
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
            send_url = request_url
            extensions: dict[str, Any] | None = None
            if safety_result is not None:
                send_url, pinned_host_header, pinned_sni_hostname = self._pinned_destination(request_url, safety_result)
                if pinned_host_header:
                    headers["Host"] = pinned_host_header
                if pinned_sni_hostname:
                    extensions = {"sni_hostname": pinned_sni_hostname}

            request = self.client.build_request(
                "POST",
                send_url,
                json=payload,
                headers=headers,
                timeout=timeout,
                extensions=extensions,
            )
            response = await self.client.send(request, follow_redirects=False)
            if 300 <= response.status_code < 400:
                raise DownstreamMcpError("downstream redirect is not allowed", code=-32003)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise DownstreamTimeoutError("downstream request timed out", code=-32008) from exc
        except httpx.HTTPError as exc:
            raise DownstreamMcpError(f"downstream request failed: {exc}") from exc

        if not expect_response and (response.status_code in {202, 204} or not response.content):
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}

        if not response.content:
            raise DownstreamMcpError("downstream returned empty response")
        if len(response.content) > self.max_response_bytes:
            raise DownstreamMcpError(
                f"downstream response exceeds {self.max_response_bytes} bytes",
                code=-32009,
            )
        content_type = response.headers.get("content-type", "").lower()
        media_type = content_type.partition(";")[0].strip()
        if media_type != "application/json":
            raise DownstreamMcpError("downstream returned non-JSON content-type", code=-32010)

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

    @staticmethod
    def _pinned_destination(request_url: str, safety_result: UrlSafetyResult) -> tuple[str, str | None, str | None]:
        """Return an IP-pinned URL while preserving the original HTTP authority.

        For DNS hosts, CoreMCP connects to the pre-validated IP address so httpx
        does not perform another DNS lookup after SSRF validation. The original
        host is kept in the Host header, and HTTPS keeps SNI on the original
        hostname via httpcore's request extension.
        """

        if safety_result.allowed_by == "host_allowlist" or not safety_result.resolved_ips:
            return request_url, None, None

        original = urlparse(request_url)
        original_host = original.hostname
        if not original_host:
            return request_url, None, None

        pinned_ip = safety_result.resolved_ips[0]
        pinned_url = str(httpx.URL(request_url).copy_with(host=pinned_ip))
        host_header = original_host
        if original.port is not None:
            host_header = f"{host_header}:{original.port}"
        sni_hostname = original_host if original.scheme == "https" else None
        return pinned_url, host_header, sni_hostname
