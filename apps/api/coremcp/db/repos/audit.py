"""Audit, metrics, and dashboard repository facade."""

from __future__ import annotations

from typing import Any

from ._base import RepositoryDomainFacade


class AuditRepository(RepositoryDomainFacade):
    """Thin facade for audit logs, invocation logs, and aggregate metrics."""

    def count_active_client_tokens(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("count_active_client_tokens", *args, **kwargs)

    def log_audit(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("log_audit", *args, **kwargs)

    def log_invocation(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("log_invocation", *args, **kwargs)

    def count_invocations(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("count_invocations", *args, **kwargs)

    def metrics_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("metrics_snapshot", *args, **kwargs)

    def dashboard_summary(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("dashboard_summary", *args, **kwargs)

    def recent_invocations(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("recent_invocations", *args, **kwargs)

    def recent_audit_logs(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("recent_audit_logs", *args, **kwargs)
