"""Shared helpers for thin domain repository facades."""

from __future__ import annotations

from typing import Any

from coremcp.db.repository import Repository


class RepositoryDomainFacade:
    """Base class for domain facades that delegate to the legacy Repository."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def _delegate(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        return getattr(self.repository, method_name)(*args, **kwargs)
