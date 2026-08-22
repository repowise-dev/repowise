"""Helpers shared across pipeline phases."""

from __future__ import annotations

import contextlib
from typing import Any

from repowise.core.pipeline.progress import ProgressCallback


def _phase_done(progress: ProgressCallback | None, phase: str) -> None:
    """Best-effort call to ``progress.on_phase_done`` — older callbacks may
    not implement it, so fall back to a no-op silently.
    """
    if progress is None:
        return
    fn = getattr(progress, "on_phase_done", None)
    if callable(fn):
        with contextlib.suppress(Exception):
            fn(phase)


def limit_to_top_pagerank(
    parsed_files: list[Any],
    graph_builder: Any,
    n: int = 10,
) -> list[Any]:
    """Return the *n* highest-PageRank files from *parsed_files*.

    Shared by the orchestrator's in-pipeline generation path and
    ``run_generation`` (init's separate generation phase), so ``--test-run``
    cannot drift between two copies of the same truncation.
    """
    try:
        import networkx as nx

        ranks = nx.pagerank(graph_builder.graph())
    except Exception:
        ranks = {}
    return sorted(
        parsed_files,
        key=lambda pf: ranks.get(pf.file_info.path, 0),
        reverse=True,
    )[:n]


#: Default ``--test-run`` file cap (highest-PageRank files).
TEST_RUN_FILE_LIMIT = 10
