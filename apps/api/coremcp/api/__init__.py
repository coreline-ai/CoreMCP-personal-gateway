"""FastAPI route modules for the CoreMCP API."""

from __future__ import annotations

from .admin_meta import register_admin_meta_routes
from .connections import register_connections_routes
from .meta import register_meta_routes
from .mcp_endpoint import register_mcp_routes
from .oauth import oauth_issuer, oauth_resource, register_oauth_routes
from .playground import register_playground_routes
from .services import register_services_routes
from .simulator import register_simulator_routes
from .toolboxes import register_toolboxes_routes

__all__ = [
    "oauth_issuer",
    "oauth_resource",
    "register_admin_meta_routes",
    "register_connections_routes",
    "register_mcp_routes",
    "register_meta_routes",
    "register_oauth_routes",
    "register_playground_routes",
    "register_services_routes",
    "register_simulator_routes",
    "register_toolboxes_routes",
]
