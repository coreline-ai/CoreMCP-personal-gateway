from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any

import structlog

REDACTED = "********"
SENSITIVE_KEY_PARTS = (
    "authorization",
    "token",
    "api_key",
    "api-key",
    "apikey",
    "secret",
    "credential",
    "password",
)
TOKEN_VALUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"cmcp_(?:admin|client|refresh|otk|code)_[A-Za-z0-9._-]+"
    r"|sk-[A-Za-z0-9][A-Za-z0-9_-]{6,}"
    r"|ghp_[A-Za-z0-9]{8,}"
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r")(?![A-Za-z0-9_])"
)


def redact_sensitive_data(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact_value(event_dict)


def configure_logging() -> None:
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive_data,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)


def redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key) and not _is_masked_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(item_key): redact_value(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return _redact_token_like_values(value)
    return value


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    return redact_value(value, key=key)


def _redact_token_like_values(value: str) -> str:
    return TOKEN_VALUE_PATTERN.sub(REDACTED, value)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _is_masked_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return "masked" in normalized
