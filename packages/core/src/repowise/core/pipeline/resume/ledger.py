"""Durable record of which pipeline phases have completed for a repo.

Async wrapper over the ``pipeline_jobs`` table (via ``SqlJobStore``). A phase
is "done" iff a COMPLETED job row exists for it. On crash, the in-flight
phase's job is left in RUNNING — never COMPLETED — so a re-run re-does it.

Each method opens and **commits** its own short-lived session, because the
whole point is cross-process durability: a phase marked completed must
survive the process being killed. Every method is best-effort — a ledger
write must never crash the pipeline, so failures are logged and swallowed
(worst case is redoing work, never losing it).
"""

from __future__ import annotations

from typing import Any

import structlog

from .phases import ResumePhase

logger = structlog.get_logger(__name__)


def _store(session: Any) -> Any:
    from repowise.core.persistence.stores.sql_job_store import SqlJobStore

    return SqlJobStore(session)


class ResumeLedger:
    """Records phase lifecycle for one repository, committing each write."""

    def __init__(self, session_factory: Any, repo_id: str) -> None:
        self._sf = session_factory
        self._repo_id = repo_id
        # phase value -> job id, so mark_completed can transition the row we
        # opened in mark_started (the id survives across sessions once committed).
        self._job_ids: dict[str, str] = {}

    async def completed_phases(self) -> set[ResumePhase]:
        """Return the set of phases already COMPLETED for this repo."""
        from repowise.core.persistence import get_session
        from repowise.core.persistence._interfaces.job_store import JobState

        try:
            async with get_session(self._sf) as session:
                jobs = await _store(session).list_jobs(repository_id=self._repo_id)
        except Exception as exc:
            logger.debug("resume_ledger_read_failed", error=str(exc))
            return set()
        done: set[ResumePhase] = set()
        for j in jobs:
            if j.state != JobState.COMPLETED:
                continue
            try:
                done.add(ResumePhase(j.phase))
            except ValueError:
                continue  # an unrelated job phase — ignore
        return done

    async def mark_started(self, phase: ResumePhase) -> None:
        from repowise.core.persistence import get_session
        from repowise.core.persistence._interfaces.job_store import JobState

        try:
            async with get_session(self._sf) as session:
                store = _store(session)
                job = await store.create_job(repository_id=self._repo_id, phase=str(phase))
                await store.update_state(job.id, JobState.RUNNING)
                self._job_ids[str(phase)] = job.id
        except Exception as exc:
            logger.debug("resume_ledger_start_failed", phase=str(phase), error=str(exc))

    async def mark_completed(self, phase: ResumePhase) -> None:
        from repowise.core.persistence import get_session
        from repowise.core.persistence._interfaces.job_store import JobState

        try:
            async with get_session(self._sf) as session:
                store = _store(session)
                job_id = self._job_ids.get(str(phase))
                if job_id is None:
                    # No open job (e.g. mark_started failed) — open one so the
                    # COMPLETED state still lands.
                    job = await store.create_job(repository_id=self._repo_id, phase=str(phase))
                    job_id = job.id
                await store.update_state(job_id, JobState.COMPLETED)
        except Exception as exc:
            logger.debug("resume_ledger_complete_failed", phase=str(phase), error=str(exc))
