"""Domain repository facades for gradual Repository call-site migration."""

from __future__ import annotations

from .audit import AuditRepository
from .catalog import CatalogRepository
from .connections import ConnectionRepository
from .credentials import CredentialRepository
from .jobs import JobRepository
from .services import ServiceRepository
from .toolbox import ToolboxRepository

__all__ = [
    "AuditRepository",
    "CatalogRepository",
    "ConnectionRepository",
    "CredentialRepository",
    "JobRepository",
    "ServiceRepository",
    "ToolboxRepository",
]
