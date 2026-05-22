"""Streaming request body size guard for FastAPI/Starlette requests."""

from __future__ import annotations

from typing import Any

from fastapi import Request


class RequestBodyTooLarge(Exception):
    """Raised when an incoming body exceeds the configured byte limit."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__(f"request body exceeds {max_bytes} bytes")


def contains_request_body_too_large(exc: BaseException) -> bool:
    """Return true when Starlette wrapped the guard error in an exception group."""

    if isinstance(exc, RequestBodyTooLarge):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(contains_request_body_too_large(item) for item in exc.exceptions)
    return False


def install_streaming_body_limit(request: Request, *, max_bytes: int) -> None:
    """Wrap ASGI receive so chunked bodies cannot bypass Content-Length checks.

    Starlette exposes the request receive callable as a private attribute. This
    small wrapper is intentionally installed in CoreMCP's first HTTP middleware,
    before JSON/body parsing happens in route handlers. It is a defense-in-depth
    guard for clients that omit or lie about Content-Length.
    """

    original_receive = request._receive  # noqa: SLF001 - Starlette has no public replacement hook.
    seen = 0

    async def limited_receive() -> dict[str, Any]:
        nonlocal seen
        message = await original_receive()
        if message.get("type") == "http.request":
            body = message.get("body", b"")
            if body:
                seen += len(body)
                if seen > max_bytes:
                    raise RequestBodyTooLarge(max_bytes)
        return message  # type: ignore[return-value]  # starlette Message is a TypedDict superset of dict[str, Any]

    request._receive = limited_receive  # noqa: SLF001 - see docstring above.
