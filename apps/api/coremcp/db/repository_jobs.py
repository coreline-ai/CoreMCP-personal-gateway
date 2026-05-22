# pyright: reportAttributeAccessIssue=false
# Mixin classes rely on host-provided attributes (db, dumps_json, loads_json,
# and cross-mixin methods); the composing Repository class supplies them at
# runtime. Type checker cannot resolve them without a circular base class.
from __future__ import annotations

from typing import Any

from coremcp.db.repository_ids import new_id


class JobsRepositoryMixin:
    """Background job SQL operations for the Repository aggregate."""

    async def create_job(self, *, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        job_id = new_id("job")
        await self.db.execute(
            "INSERT INTO jobs (id, kind, payload) VALUES (?, ?, ?)",
            (job_id, kind, self.dumps_json(payload or {})),
        )
        await self.db.commit()
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
        await self.db.execute(
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
                self.dumps_json(result) if result is not None else None,
                self.dumps_json(error) if error is not None else None,
                status,
                job_id,
            ),
        )
        await self.db.commit()
        return await self.get_job(job_id)

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return self._row_to_dict(await cursor.fetchone(), json_fields=("payload", "result", "error"))

    async def mark_stuck_jobs_failed(self, *, max_age_seconds: int, now_epoch: float | None = None) -> int:
        now_expr = "strftime('%s','now')" if now_epoch is None else "?"
        params: list[Any] = []
        if now_epoch is not None:
            params.append(float(now_epoch))
        params.append(max(1, int(max_age_seconds)))
        cursor = await self.db.execute(
            f"""
            UPDATE jobs
            SET status = 'failed',
                error = ?,
                finished_at = CURRENT_TIMESTAMP
            WHERE status IN ('queued', 'running')
              AND ({now_expr} - strftime('%s', COALESCE(started_at, created_at))) >= ?
            """,
            [
                self.dumps_json({"code": "stuck_job_reaped", "message": "Job exceeded max runtime and was marked failed"}),
                *params,
            ],
        )
        await self.db.commit()
        return int(cursor.rowcount or 0)
