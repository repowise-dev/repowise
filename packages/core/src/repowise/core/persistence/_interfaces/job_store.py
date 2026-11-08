"""JobStore — pluggable checkpoint/resume state for pipeline runs.

A *job* here is one execution of a pipeline phase against a repository.
Storing per-phase state lets a long-running index survive crashes: on
restart the orchestrator looks for incomplete jobs and resumes them at
their last recorded cursor.

The in-tree default is
:class:`repowise.core.persistence.stores.sql_job_store.SqlJobStore`, which
backs the contract with the ``pipeline_jobs`` table introduced in
Alembic revision ``0020``.

This phase introduces the contract only. The orchestrator integration —
writing checkpoints on a fixed cadence and offering ``--resume`` on
startup — lands in a follow-up that addresses large-repo scale.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class JobState(enum.StrEnum):
    """Lifecycle states for a pipeline job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


#: Error text recorded on a job reaped as stale: the process that owned it
#: exited (crash, dropped connection, Ctrl-C) without finishing, so the row
#: no longer describes in-flight work.
STALE_JOB_ERROR = "abandoned: process exited without finishing"


@dataclass(frozen=True)
class JobRecord:
    """One pipeline job row.

    ``cursor`` is an opaque string that the phase implementation
    interprets — typically a file path, a commit SHA, or a batch index.
    ``metadata`` carries any auxiliary state (e.g. provider / model used)
    so the resume path doesn't have to recompute it.
    """

    id: str
    repository_id: str
    phase: str
    state: JobState
    cursor: str | None
    started_at: datetime
    updated_at: datetime
    error: str | None
    metadata: dict


class JobStore(ABC):
    """Pluggable contract for pipeline job checkpoints."""

    @abstractmethod
    async def create_job(
        self,
        *,
        repository_id: str,
        phase: str,
        metadata: dict | None = None,
    ) -> JobRecord:
        """Insert a new job in :attr:`JobState.PENDING`. Returns the row.

        Creating a job starts a new run for the repository, so it first
        reaps any stale :attr:`JobState.RUNNING` jobs for that repository
        (see :meth:`reap_stale_jobs`): a previous run that died without
        finishing must not keep claiming to be in flight.
        """

    @abstractmethod
    async def get_job(self, job_id: str) -> JobRecord | None: ...

    @abstractmethod
    async def update_state(
        self,
        job_id: str,
        state: JobState,
        *,
        cursor: str | None = None,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> JobRecord:
        """Update lifecycle state. ``cursor`` advances on each checkpoint;
        ``error`` is populated on failure transitions."""

    @abstractmethod
    async def checkpoint(self, job_id: str, cursor: str) -> JobRecord:
        """Record progress without changing :class:`JobState`.

        Implementations should be cheap — this is called on a fixed cadence
        inside a tight loop. The OSS default coalesces writes via a single
        UPDATE per call.
        """

    async def reap_stale_jobs(self, *, repository_id: str) -> list[JobRecord]:
        """Mark stale :attr:`JobState.RUNNING` jobs INTERRUPTED.

        A job is stale when its owning run is gone: it was left ``running``
        by a crash, a dropped connection, or Ctrl-C, and nothing will ever
        update it again. Reaping records :attr:`JobState.INTERRUPTED` with
        :data:`STALE_JOB_ERROR` so status reads stop reporting dead work as
        in flight, and so ``find_resumable`` no longer offers it for resume.

        The default implementation reaps through :meth:`find_resumable` +
        :meth:`update_state`; stores may override it with a bulk UPDATE.
        Only ``RUNNING`` rows are reaped — ``PENDING`` rows never started,
        and terminal rows are history.
        """
        reaped: list[JobRecord] = []
        for job in await self.find_resumable(repository_id=repository_id):
            if job.state is JobState.RUNNING:
                reaped.append(await self.update_state(job.id, JobState.INTERRUPTED, error=STALE_JOB_ERROR))
        return reaped

    @abstractmethod
    async def find_resumable(
        self, *, repository_id: str | None = None
    ) -> list[JobRecord]:
        """Return jobs in :attr:`JobState.RUNNING` (or ``PENDING``) that
        the orchestrator should offer to resume on startup.

        Filtered to ``repository_id`` when provided.
        """

    @abstractmethod
    async def list_jobs(
        self,
        *,
        repository_id: str | None = None,
        phase: str | None = None,
        state: JobState | None = None,
        limit: int = 100,
    ) -> list[JobRecord]:
        """Read-side query for the dashboard / CLI ``status`` command."""
