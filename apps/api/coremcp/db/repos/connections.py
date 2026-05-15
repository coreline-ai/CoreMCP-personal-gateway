"""External AI client connection repository facade."""

from __future__ import annotations

from typing import Any

from ._base import RepositoryDomainFacade


class ConnectionRepository(RepositoryDomainFacade):
    """Thin facade for 연결된 AI client connection records."""

    def create_external_connection(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("create_external_connection", *args, **kwargs)

    def get_external_connection(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_external_connection", *args, **kwargs)

    def list_external_connections(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("list_external_connections", *args, **kwargs)

    def revoke_external_connection(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("revoke_external_connection", *args, **kwargs)
