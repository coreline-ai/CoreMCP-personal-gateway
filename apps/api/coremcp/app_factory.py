"""Public FastAPI application factory import boundary.

CoreMCP keeps the concrete factory in ``coremcp.main`` while route and MCP
handler implementations live in ``coremcp.api`` / ``coremcp.mcp`` modules.
External callers should import from this module so future internal moves do not
change the public application factory path again.
"""

from __future__ import annotations

from .main import create_app

__all__ = ["create_app"]
