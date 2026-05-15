"""Repository facade boundary for future domain split.

The current implementation remains in `repository.py`. This module gives tests,
CLI, and future route modules a stable import target while the god-object
repository is decomposed into service/toolbox/token/audit repositories.
"""

from __future__ import annotations

from .repository import DEFAULT_TOOLBOX_ID, LOCAL_USER_ID, Repository, new_id
from .repos import (
    AuditRepository,
    CatalogRepository,
    ConnectionRepository,
    CredentialRepository,
    JobRepository,
    ServiceRepository,
    ToolboxRepository,
)


class RepositoryFacades:
    """Domain facade bundle for future Repository call-site migration."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.audit = AuditRepository(repository)
        self.catalog = CatalogRepository(repository)
        self.connections = ConnectionRepository(repository)
        self.credentials = CredentialRepository(repository)
        self.jobs = JobRepository(repository)
        self.services = ServiceRepository(repository)
        self.toolbox = ToolboxRepository(repository)


__all__ = [
    "AuditRepository",
    "CatalogRepository",
    "ConnectionRepository",
    "CredentialRepository",
    "DEFAULT_TOOLBOX_ID",
    "JobRepository",
    "LOCAL_USER_ID",
    "Repository",
    "RepositoryFacades",
    "ServiceRepository",
    "ToolboxRepository",
    "new_id",
]
