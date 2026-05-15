"""도구함 repository facade."""

from __future__ import annotations

from typing import Any

from ._base import RepositoryDomainFacade


class ToolboxRepository(RepositoryDomainFacade):
    """Thin facade for 도구함 items and overrides."""

    def list_toolboxes(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("list_toolboxes", *args, **kwargs)

    def add_toolbox_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("add_toolbox_item", *args, **kwargs)

    def get_toolbox_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_toolbox_item", *args, **kwargs)

    def list_toolbox_items(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("list_toolbox_items", *args, **kwargs)

    def update_toolbox_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("update_toolbox_item", *args, **kwargs)

    def delete_toolbox_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("delete_toolbox_item", *args, **kwargs)

    def list_tool_overrides(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("list_tool_overrides", *args, **kwargs)

    def upsert_tool_override(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("upsert_tool_override", *args, **kwargs)
