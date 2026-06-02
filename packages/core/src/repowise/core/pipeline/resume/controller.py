"""The resume coordinator the orchestrator consults at phase boundaries.

Responsibilities, all behind one object so the orchestrator stays
DB-agnostic:

- decide whether a phase may be **skipped** on a resumed run
  (:meth:`can_skip`),
- **persist** a phase's outputs the moment it completes and record the phase
  in the ledger (:meth:`checkpoint_index`),
- serve **rehydrated** inputs for skipped phases
  (:meth:`rehydrate_index`).

A ``None`` controller (the default everywhere) means resume is disabled and
the pipeline runs exactly as before — no new DB work, no behaviour change.
Construction requires a repo_id that already exists in the database, which
is why the CLI creates the repository row *before* starting the pipeline
(this also fixes the ``pipeline_jobs.repository_id`` FK that the old
``str(repo_path)`` wiring violated — the id is now the real ``Repository.id``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import structlog

from ..persist import persist_git, persist_ingestion
from .ledger import ResumeLedger
from .phases import RESUME_PHASE_ORDER, ResumePhase
from .rehydrate import rehydrate_git_meta_map, rehydrate_graph_builder

logger = structlog.get_logger(__name__)


class ResumeController:
    """Coordinates skip/persist/rehydrate for one pipeline run."""

    def __init__(
        self,
        session_factory: Any,
        repo_id: str,
        *,
        resume: bool,
    ) -> None:
        self._sf = session_factory
        self._repo_id = repo_id
        self._resume = resume
        self._ledger = ResumeLedger(session_factory, repo_id)
        self._completed: set[ResumePhase] | None = None

    @property
    def repo_id(self) -> str:
        return self._repo_id

    # -- skip decisions --------------------------------------------------------

    async def _completed_phases(self) -> set[ResumePhase]:
        if self._completed is None:
            self._completed = await self._ledger.completed_phases() if self._resume else set()
        return self._completed

    async def can_skip(self, phase: ResumePhase) -> bool:
        """True iff *phase* — and every phase before it — completed previously.

        The prefix requirement means we never skip a phase whose inputs an
        earlier, unfinished phase would have produced.
        """
        if not self._resume:
            return False
        done = await self._completed_phases()
        cutoff = RESUME_PHASE_ORDER.index(phase)
        return all(p in done for p in RESUME_PHASE_ORDER[: cutoff + 1])

    # -- rehydration -----------------------------------------------------------

    async def rehydrate_index(self, repo_path: Any) -> tuple[Any, dict[str, dict[str, Any]]]:
        """Rebuild ``(graph_builder, git_meta_map)`` from persisted rows.

        The graph comes back finalized (metrics served from SQL, no centrality
        kernel re-run); git metadata carries every durable signal. The caller
        still re-parses source files to obtain ``parsed_files`` — those aren't
        persisted in reconstructable form, but re-parsing is far cheaper than
        the git history + centrality work this skips.
        """
        from repowise.core.persistence import get_session

        async with get_session(self._sf) as session:
            graph_builder = await rehydrate_graph_builder(session, self._repo_id, repo_path)
            git_meta_map = await rehydrate_git_meta_map(session, self._repo_id)
        return graph_builder, git_meta_map

    # -- checkpoints -----------------------------------------------------------

    async def checkpoint_index(
        self,
        *,
        parsed_files: list[Any],
        graph_builder: Any,
        git_metadata_list: list[dict],
        git_summary: Any | None = None,
        external_systems: list[dict] | None = None,
        execution_flow_report: Any | None = None,
    ) -> None:
        """Persist the INDEX phase (graph + symbols + git) and record it.

        Idempotent UPSERTs throughout, so a later full persist that re-writes
        the same rows (e.g. graph nodes with entry-point scores once execution
        flow is known) is harmless. Best-effort: a persistence hiccup is logged
        and swallowed so it can never abort the in-memory run.
        """
        from repowise.core.persistence import get_session

        view = SimpleNamespace(
            parsed_files=parsed_files,
            graph_builder=graph_builder,
            git_metadata_list=git_metadata_list,
            git_summary=git_summary,
            external_systems=external_systems or [],
            execution_flow_report=execution_flow_report,
        )
        await self._ledger.mark_started(ResumePhase.INDEX)
        try:
            async with get_session(self._sf) as session:
                await persist_ingestion(view, session, self._repo_id)
                await persist_git(view, session, self._repo_id)
        except Exception as exc:
            logger.warning("resume_checkpoint_index_failed", error=str(exc))
            return
        await self._ledger.mark_completed(ResumePhase.INDEX)
        logger.info("resume_checkpoint_index", repo_id=self._repo_id)

    async def mark_phase_complete(self, phase: ResumePhase) -> None:
        """Record *phase* as completed (analysis / generation persisted by the
        normal end-of-run persist; this just stamps the ledger)."""
        await self._ledger.mark_completed(phase)
