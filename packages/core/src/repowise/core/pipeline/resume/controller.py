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

from ..persist import persist_analysis, persist_git, persist_ingestion
from .ledger import ResumeLedger
from .phases import RESUME_PHASE_ORDER, ResumePhase
from .rehydrate import (
    rehydrate_dead_code_report,
    rehydrate_decision_report,
    rehydrate_git_meta_map,
    rehydrate_graph_builder,
)

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
        # True once the INDEX phase is provably on disk — either freshly
        # checkpointed this run or rehydrated from a prior run. The caller
        # keys its "skip the index in the final persist" decision on this, so
        # it must NEVER be True unless the index really was persisted (a
        # best-effort checkpoint failure leaves it False → full persist).
        self._index_persisted = False

    @property
    def repo_id(self) -> str:
        return self._repo_id

    @property
    def index_persisted(self) -> bool:
        return self._index_persisted

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
        # The index is, by definition, already persisted (we just read it).
        self._index_persisted = True
        return graph_builder, git_meta_map

    async def rehydrate_analysis(self) -> tuple[Any, Any]:
        """Rebuild ``(dead_code_report, decision_report)`` from persisted rows.

        Used **only** as generation input on a resumed run whose ANALYSIS phase
        already completed — so the resume skips re-running dead-code detection,
        health scoring and (the costly) decision extraction. The returned views
        are deliberately thin (just what the page generator reads) and must
        never be written back: the persisted analysis rows are authoritative.
        Raises on a DB error so the caller can fall back to recomputing.
        """
        from repowise.core.persistence import get_session

        async with get_session(self._sf) as session:
            dead_code_report = await rehydrate_dead_code_report(session, self._repo_id)
            decision_report = await rehydrate_decision_report(session, self._repo_id)
        return dead_code_report, decision_report

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
        self._index_persisted = True
        await self._ledger.mark_completed(ResumePhase.INDEX)
        logger.info("resume_checkpoint_index", repo_id=self._repo_id)

    async def checkpoint_analysis(
        self,
        *,
        dead_code_report: Any | None,
        health_report: Any | None,
        decision_report: Any | None,
        git_metadata_list: list[dict],
        vector_store: Any | None = None,
    ) -> None:
        """Persist the ANALYSIS phase (dead code + health + decisions) and record it.

        Mirrors :meth:`checkpoint_index`: writing these rows the moment the
        analysis phase finishes — *before* the long generation phase — is what
        lets a generation interrupt resume past analysis instead of recomputing
        it (decision extraction in particular can run for minutes). Best-effort:
        a persistence hiccup is logged and swallowed, leaving ANALYSIS unmarked
        so the resumed run simply recomputes it. ``generated_pages`` is empty at
        this point, so harvested-decision folding still happens at the final
        end-of-run persist.
        """
        from repowise.core.persistence import get_session

        view = SimpleNamespace(
            dead_code_report=dead_code_report,
            health_report=health_report,
            decision_report=decision_report,
            git_metadata_list=git_metadata_list,
            generated_pages=None,
            vector_store=vector_store,
        )
        await self._ledger.mark_started(ResumePhase.ANALYSIS)
        try:
            async with get_session(self._sf) as session:
                await persist_analysis(view, session, self._repo_id)
        except Exception as exc:
            logger.warning("resume_checkpoint_analysis_failed", error=str(exc))
            return
        await self._ledger.mark_completed(ResumePhase.ANALYSIS)
        logger.info("resume_checkpoint_analysis", repo_id=self._repo_id)

    async def mark_phase_complete(self, phase: ResumePhase) -> None:
        """Record *phase* as completed (analysis / generation persisted by the
        normal end-of-run persist; this just stamps the ledger)."""
        await self._ledger.mark_completed(phase)
