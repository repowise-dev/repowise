"""SqlJobStore — default :class:`JobStore` implementation.

Backed by the ``pipeline_jobs`` table introduced in Alembic revision
``0020``. Binds a single :class:`AsyncSession`; the caller owns commit /
rollback (matching the pattern used by :class:`SqlIndexStore`).
"""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .._interfaces.job_store import JobRecord, JobState, JobStore
from ..models import PipelineJob, _now_utc


def _to_record(row: PipelineJob) -> JobRecord:
    """Project an ORM row to the immutable :class:`JobRecord` dataclass."""
    try:
        meta = json.loads(row.metadata_json) if row.metadata_json else {}
    except json.JSONDecodeError:
        meta = {}
    return JobRecord(
        id=row.id,
        repository_id=row.repository_id,
        phase=row.phase,
        state=JobState(row.state),
        cursor=row.cursor,
        started_at=row.started_at,
        updated_at=row.updated_at,
        error=row.error,
        metadata=meta,
    )


class SqlJobStore(JobStore):
    """:class:`JobStore` backed by the ``pipeline_jobs`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_job(
        self,
        *,
        repository_id: str,
        phase: str,
        metadata: dict | None = None,
    ) -> JobRecord:
        row = PipelineJob(
            id=uuid4().hex,
            repository_id=repository_id,
            phase=phase,
            state=JobState.PENDING.value,
            cursor=None,
            error=None,
            metadata_json=json.dumps(metadata or {}),
        )
        self._session.add(row)
        await self._session.flush()
        return _to_record(row)

    async def get_job(self, job_id: str) -> JobRecord | None:
        row = await self._session.get(PipelineJob, job_id)
        return _to_record(row) if row is not None else None

    async def update_state(
        self,
        job_id: str,
        state: JobState,
        *,
        cursor: str | None = None,
        error: str | None = None,
    ) -> JobRecord:
        row = await self._session.get(PipelineJob, job_id)
        if row is None:
            raise LookupError(f"No PipelineJob with id={job_id!r}")
        row.state = state.value
        if cursor is not None:
            row.cursor = cursor
        if error is not None:
            row.error = error
        row.updated_at = _now_utc()
        await self._session.flush()
        return _to_record(row)

    async def checkpoint(self, job_id: str, cursor: str) -> JobRecord:
        row = await self._session.get(PipelineJob, job_id)
        if row is None:
            raise LookupError(f"No PipelineJob with id={job_id!r}")
        row.cursor = cursor
        row.updated_at = _now_utc()
        await self._session.flush()
        return _to_record(row)

    async def find_resumable(
        self, *, repository_id: str | None = None
    ) -> list[JobRecord]:
        q = select(PipelineJob).where(
            PipelineJob.state.in_(
                [JobState.PENDING.value, JobState.RUNNING.value]
            )
        )
        if repository_id is not None:
            q = q.where(PipelineJob.repository_id == repository_id)
        q = q.order_by(PipelineJob.started_at.asc())
        result = await self._session.execute(q)
        return [_to_record(row) for row in result.scalars().all()]

    async def list_jobs(
        self,
        *,
        repository_id: str | None = None,
        phase: str | None = None,
        state: JobState | None = None,
        limit: int = 100,
    ) -> list[JobRecord]:
        q = select(PipelineJob)
        if repository_id is not None:
            q = q.where(PipelineJob.repository_id == repository_id)
        if phase is not None:
            q = q.where(PipelineJob.phase == phase)
        if state is not None:
            q = q.where(PipelineJob.state == state.value)
        q = q.order_by(PipelineJob.started_at.desc()).limit(limit)
        result = await self._session.execute(q)
        return [_to_record(row) for row in result.scalars().all()]


__all__ = ["SqlJobStore"]
