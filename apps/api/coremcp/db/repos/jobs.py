"""Background job repository facade."""

from __future__ import annotations

from typing import Any

from ._base import RepositoryDomainFacade


class JobRepository(RepositoryDomainFacade):
    """Thin facade for background job records."""

    def create_job(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("create_job", *args, **kwargs)

    def update_job(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("update_job", *args, **kwargs)

    def get_job(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_job", *args, **kwargs)

    def mark_stuck_jobs_failed(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("mark_stuck_jobs_failed", *args, **kwargs)
