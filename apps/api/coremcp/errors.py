"""CoreMCP exception hierarchy.

The concrete exception classes still inherit from their historical Python
families (``ValueError``/``RuntimeError``) through these adapters so existing
call sites and tests keep their behavior while callers can also catch a single
``CoreMcpError`` boundary.
"""

from __future__ import annotations


class CoreMcpError(Exception):
    """Base class for CoreMCP-raised exceptions."""


class CoreMcpValueError(CoreMcpError, ValueError):
    """CoreMCP error that preserves ``ValueError`` compatibility."""


class CoreMcpRuntimeError(CoreMcpError, RuntimeError):
    """CoreMCP error that preserves ``RuntimeError`` compatibility."""
