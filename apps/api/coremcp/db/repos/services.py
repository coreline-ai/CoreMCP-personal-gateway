"""MCP service registry repository facade."""

from __future__ import annotations

from typing import Any

from ._base import RepositoryDomainFacade


class ServiceRepository(RepositoryDomainFacade):
    """Thin facade for MCP service registry operations."""

    def create_mcp_service(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("create_mcp_service", *args, **kwargs)

    def get_mcp_service(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_mcp_service", *args, **kwargs)

    def list_mcp_services(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("list_mcp_services", *args, **kwargs)

    def update_mcp_service(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("update_mcp_service", *args, **kwargs)

    def mark_service_validated(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("mark_service_validated", *args, **kwargs)

    def mark_service_health_probe(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("mark_service_health_probe", *args, **kwargs)

    def soft_delete_mcp_service(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("soft_delete_mcp_service", *args, **kwargs)
