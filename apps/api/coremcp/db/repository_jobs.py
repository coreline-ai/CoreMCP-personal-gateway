"""Background job repository.

ADR-046 Step 1 / Phase 2 (2026-05-23 cycle): the original
``JobsRepositoryMixin`` is replaced by ``JobsRepository``, a composition
class that takes a ``Repository`` and uses it explicitly. ``Repository``
no longer inherits from this module — it composes ``self.jobs =
JobsRepository(self)`` and provides four backward-compat delegate
methods so legacy call sites (``repository.create_job(...)`` etc.) keep
working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from coremcp.db.repository_ids import new_id

if TYPE_CHECKING:
    from coremcp.db.repository import Repository


class JobsRepository:
    """Background job SQL operations, composed onto ``Repository``.

    Methods read/write through the bound ``Repository`` instance via its
    ``db`` / ``dumps_json`` / ``_row_to_dict`` helpers. No mixin pattern;
    the type checker resolves every attribute explicitly.
    """

    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    async def create_job(self, *, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        job_id = new_id("job")
        await self._repo.db.execute(
            "INSERT INTO jobs (id, kind, payload) VALUES (?, ?, ?)",
            (job_id, kind, self._repo.dumps_json(payload or {})),
        )
        await self._repo.db.commit()
        return await self.get_job(job_id) or {}

    async def update_job(
        self,
        job_id: str,
        *,
        status: str,
        progress: float | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        await self._repo.db.execute(
            """
            UPDATE jobs
            SET status = ?, progress = COALESCE(?, progress), result = COALESCE(?, result),
                error = COALESCE(?, error), started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                finished_at = CASE WHEN ? IN ('success', 'failed', 'cancelled') THEN CURRENT_TIMESTAMP ELSE finished_at END
            WHERE id = ?
            """,
            (
                status,
                progress,
                self._repo.dumps_json(result) if result is not None else None,
                self._repo.dumps_json(error) if error is not None else None,
                status,
                job_id,
            ),
        )
        await self._repo.db.commit()
        return await self.get_job(job_id)

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        cursor = await self._repo.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return self._repo._row_to_dict(  # noqa: SLF001 - host helper
            await cursor.fetchone(), json_fields=("payload", "result", "error")
        )

    async def mark_stuck_jobs_failed(self, *, max_age_seconds: int, now_epoch: float | None = None) -> int:
        now_expr = "strftime('%s','now')" if now_epoch is None else "?"
        params: list[Any] = []
        if now_epoch is not None:
            params.append(float(now_epoch))
        params.append(max(1, int(max_age_seconds)))
        cursor = await self._repo.db.execute(
            f"""
            UPDATE jobs
            SET status = 'failed',
                error = ?,
                finished_at = CURRENT_TIMESTAMP
            WHERE status IN ('queued', 'running')
              AND ({now_expr} - strftime('%s', COALESCE(started_at, created_at))) >= ?
            """,
            [
                self._repo.dumps_json({"code": "stuck_job_reaped", "message": "Job exceeded max runtime and was marked failed"}),
                *params,
            ],
        )
        await self._repo.db.commit()
        return int(cursor.rowcount or 0)


# Backward-compat alias for any external code that still imports the mixin.
# The class itself is no longer a Python mixin; subclassing it as a mixin will
# fail at construction time because of the required ``repository`` argument.
# This alias only exists so historical imports keep resolving.
JobsRepositoryMixin = JobsRepository


__all__ = ["JobsRepository", "JobsRepositoryMixin"]
