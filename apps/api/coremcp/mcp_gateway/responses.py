"""JSON-RPC envelope helpers and HTTP error response builders.

Pure functions extracted from ``coremcp/main.py``. They have no dependencies
on FastAPI ``Request`` / app state and can be imported anywhere that needs
to construct a JSON-RPC reply or a standard CoreMCP API error response.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

JSONRPC_VERSION = "2.0"


def jsonrpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def api_error(code: str, message: str, *, status_code: int = 400, details: Any | None = None) -> JSONResponse:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return JSONResponse(payload, status_code=status_code)


def accepted(payload: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(payload or {"status": "accepted"}, status_code=202)


def not_found(resource: str = "resource") -> JSONResponse:
    return api_error("not_found", f"{resource} not found", status_code=404)


def tool_error_result(
    error_code: str,
    message: str,
    *,
    downstream_code: int | None = None,
    reason: str | None = None,
    retry_after_seconds: float | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"error_code": error_code}
    if downstream_code is not None:
        meta["downstream_code"] = downstream_code
    if reason is not None:
        meta["reason"] = reason
    if retry_after_seconds is not None:
        meta["retry_after_seconds"] = retry_after_seconds
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
        "_meta": {"coremcp": meta},
    }


__all__ = [
    "JSONRPC_VERSION",
    "accepted",
    "api_error",
    "jsonrpc_error",
    "jsonrpc_result",
    "not_found",
    "tool_error_result",
]
