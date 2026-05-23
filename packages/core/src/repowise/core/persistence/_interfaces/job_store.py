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
        """Insert a new job in :attr:`JobState.PENDING`. Returns the row."""

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
