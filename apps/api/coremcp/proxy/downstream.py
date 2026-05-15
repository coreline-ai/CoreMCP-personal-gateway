import json
from collections.abc import Awaitable, Callable
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
        session_id_callback: Callable[[str], Awaitable[None] | None] | None = None,
        notification_callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
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
            "Accept": "application/json, text/event-stream",
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
                if name.lower() in {"authorization", "x-api-key", "idempotency-key"}:
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
            response = await self.client.send(request, follow_redirects=False, stream=True)
            if 300 <= response.status_code < 400:
                await response.aclose()
                raise DownstreamMcpError("downstream redirect is not allowed", code=-32003)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                await response.aclose()
                raise DownstreamMcpError(f"downstream request failed: {exc}") from exc
            downstream_session_id = response.headers.get("Mcp-Session-Id")
            if downstream_session_id:
                await self._emit_session_id(session_id_callback, downstream_session_id)
        except httpx.TimeoutException as exc:
            raise DownstreamTimeoutError("downstream request timed out", code=-32008) from exc
        except httpx.HTTPError as exc:
            raise DownstreamMcpError(f"downstream request failed: {exc}") from exc

        try:
            content_type = response.headers.get("content-type", "").lower()
            media_type = content_type.partition(";")[0].strip()
            if media_type == "text/event-stream":
                data = await self._read_sse_json_response(
                    response,
                    notification_callback=notification_callback,
                    expect_response=expect_response,
                    request_id=request_id,
                )
            else:
                content = await response.aread()
                if not expect_response and (response.status_code in {202, 204} or not content):
                    return {"jsonrpc": "2.0", "id": request_id, "result": {}}

                if not content:
                    raise DownstreamMcpError("downstream returned empty response")
                if len(content) > self.max_response_bytes:
                    raise DownstreamMcpError(
                        f"downstream response exceeds {self.max_response_bytes} bytes",
                        code=-32009,
                    )
                if media_type != "application/json":
                    raise DownstreamMcpError("downstream returned non-JSON content-type", code=-32010)

                try:
                    data = response.json()
                except ValueError as exc:
                    raise DownstreamMcpError("downstream returned non-JSON response") from exc
        finally:
            await response.aclose()

        return self._jsonrpc_response_or_raise(data, method=method)

    def _jsonrpc_response_or_raise(self, data: Any, *, method: str) -> dict[str, Any]:
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

    async def _read_sse_json_response(
        self,
        response: httpx.Response,
        *,
        notification_callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
        expect_response: bool,
        request_id: Any,
    ) -> dict[str, Any]:
        if not expect_response:
            async for _ in response.aiter_bytes():
                pass
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}

        total_bytes = 0
        buffer = ""
        final_data: dict[str, Any] | None = None

        async for chunk in response.aiter_bytes():
            total_bytes += len(chunk)
            if total_bytes > self.max_response_bytes:
                raise DownstreamMcpError(
                    f"downstream response exceeds {self.max_response_bytes} bytes",
                    code=-32009,
                )
            buffer += chunk.decode("utf-8", errors="replace")
            while "\n\n" in buffer:
                raw_event, buffer = buffer.split("\n\n", 1)
                event_data = self._parse_sse_event_data(raw_event)
                if event_data is None:
                    continue
                try:
                    data = json.loads(event_data)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                if isinstance(data.get("method"), str) and "id" not in data:
                    await self._emit_notification(notification_callback, data)
                    continue
                final_data = data

        if buffer.strip():
            event_data = self._parse_sse_event_data(buffer)
            if event_data:
                try:
                    data = json.loads(event_data)
                except ValueError:
                    data = None
                if isinstance(data, dict):
                    if isinstance(data.get("method"), str) and "id" not in data:
                        await self._emit_notification(notification_callback, data)
                    else:
                        final_data = data

        if final_data is None:
            raise DownstreamMcpError("downstream SSE response did not include a JSON-RPC response")
        return final_data

    @staticmethod
    def _parse_sse_event_data(raw_event: str) -> str | None:
        data_lines: list[str] = []
        for line in raw_event.splitlines():
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())
        if not data_lines:
            return None
        return "\n".join(data_lines)

    @staticmethod
    async def _emit_notification(
        callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
        data: dict[str, Any],
    ) -> None:
        if callback is None:
            return
        result = callback(data)
        if result is not None:
            await result

    @staticmethod
    async def _emit_session_id(
        callback: Callable[[str], Awaitable[None] | None] | None,
        session_id: str,
    ) -> None:
        if callback is None:
            return
        result = callback(session_id)
        if result is not None:
            await result

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
