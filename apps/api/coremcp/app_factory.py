"""Public FastAPI application factory boundary.

`coremcp.main` still owns route registration during the transition, but new
callers should import from this module so route modules can be split without
changing external imports again.
"""

from __future__ import annotations

from .main import create_app

__all__ = ["create_app"]
