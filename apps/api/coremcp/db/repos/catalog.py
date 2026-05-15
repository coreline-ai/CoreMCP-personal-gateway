"""MCP catalog repository facade."""

from __future__ import annotations

from typing import Any

from ._base import RepositoryDomainFacade


class CatalogRepository(RepositoryDomainFacade):
    """Thin facade for service-discovered and exposed catalog operations."""

    def replace_service_tools(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("replace_service_tools", *args, **kwargs)

    def get_service_tool(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_service_tool", *args, **kwargs)

    def list_service_tools(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("list_service_tools", *args, **kwargs)

    def replace_service_resources(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("replace_service_resources", *args, **kwargs)

    def apply_resource_shadow_policy(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("apply_resource_shadow_policy", *args, **kwargs)

    def replace_service_resource_templates(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("replace_service_resource_templates", *args, **kwargs)

    def replace_service_prompts(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("replace_service_prompts", *args, **kwargs)

    def get_service_resource(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_service_resource", *args, **kwargs)

    def get_service_resource_template(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_service_resource_template", *args, **kwargs)

    def get_service_prompt(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_service_prompt", *args, **kwargs)

    def get_catalog_tools(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_catalog_tools", *args, **kwargs)

    def list_catalog_resources(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("list_catalog_resources", *args, **kwargs)

    def list_catalog_resource_templates(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("list_catalog_resource_templates", *args, **kwargs)

    def list_catalog_prompts(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("list_catalog_prompts", *args, **kwargs)

    def get_catalog_resource_by_uri(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_catalog_resource_by_uri", *args, **kwargs)

    def get_catalog_prompt_by_exposed_name(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_catalog_prompt_by_exposed_name", *args, **kwargs)
