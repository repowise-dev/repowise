"""Orchestrator execution modes.

``OrchestratorMode`` selects how much of the pipeline runs. It is a thin policy
object over the existing :func:`run_pipeline` — not a fork of it — so the two
modes share one code path and differ only in the knobs they flip.

- ``STANDARD`` (default): the historical behaviour. FULL git indexing, all
  analysis, and (when requested) LLM doc generation.
- ``FAST``: graph + ESSENTIAL git only, no LLM doc generation. Intended for a
  quick first index of a very large repo; the deferred git signals can be
  backfilled later (see ``git_indexer.backfill``).
"""

from __future__ import annotations

import enum

from repowise.core.ingestion.git_indexer import GitIndexTier

__all__ = ["OrchestratorMode"]


class OrchestratorMode(enum.StrEnum):
    """Pipeline execution depth."""

    STANDARD = "standard"
    FAST = "fast"

    @property
    def git_tier(self) -> GitIndexTier:
        """Git-indexing tier this mode runs."""
        return (
            GitIndexTier.ESSENTIAL if self is OrchestratorMode.FAST else GitIndexTier.FULL
        )

    @property
    def allows_doc_generation(self) -> bool:
        """Whether LLM doc generation may run in this mode.

        FAST never generates docs (and never makes LLM calls), regardless of
        the ``generate_docs`` flag passed to the pipeline.
        """
        return self is not OrchestratorMode.FAST

    @property
    def sql_backed_metrics(self) -> bool:
        """Whether file-level metrics should be served from SQL post-build.

        When ``True`` the pipeline materializes the metrics snapshot to
        ``graph_metrics`` and may drop the in-memory NetworkX object (via
        ``GraphBuilder.release_graph``), serving subsequent metric reads from
        the loaded snapshot — the large-repo scaling path.

        Default off in ``STANDARD`` (doc generation traverses the live graph)
        and on in ``FAST`` (which generates no docs, so nothing needs the
        structural graph after build + persistence).
        """
        return self is OrchestratorMode.FAST
