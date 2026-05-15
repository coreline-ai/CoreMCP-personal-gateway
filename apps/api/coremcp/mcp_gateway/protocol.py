from datetime import date
from typing import Any

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-11-25")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
LATEST_PROTOCOL_VERSION = "2025-11-25"


def _protocol_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _is_future_protocol(value: str) -> bool:
    parsed = _protocol_date(value)
    latest = _protocol_date(LATEST_PROTOCOL_VERSION)
    return bool(parsed and latest and parsed > latest)


def negotiate_protocol_version(requested: str | None) -> str:
    """Negotiate MCP protocol version.

    - Missing version keeps Claude Code compatibility by assuming 2025-06-18.
    - Supported versions are echoed.
    - Future versions are downgraded to the latest supported version.
    - Other unsupported versions currently fall back to the latest supported
      version for P0 compatibility.
    """

    if not requested:
        return DEFAULT_PROTOCOL_VERSION
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    if _is_future_protocol(requested):
        return LATEST_PROTOCOL_VERSION
    return LATEST_PROTOCOL_VERSION


def protocol_negotiation_warning(requested: Any, negotiated: str) -> dict[str, str] | None:
    if requested is None:
        return None
    requested_text = str(requested)
    if requested_text in SUPPORTED_PROTOCOL_VERSIONS:
        return None
    if _is_future_protocol(requested_text):
        code = "future_protocol_downgraded"
        message = "future protocol downgraded to latest supported version"
    else:
        code = "unsupported_protocol_downgraded"
        message = "unsupported protocol downgraded to latest supported version"
    return {
        "code": code,
        "warning": message,
        "requested_protocol_version": requested_text,
        "negotiated_protocol_version": negotiated,
    }
