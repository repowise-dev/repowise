"""Incremental fast→full upgrade helpers.

A ``--mode fast`` index persists the full structural graph + materialized
metrics but skips FULL git signals and LLM doc generation. Upgrading it to a
full index should *not* redo the structural work: the graph is already on disk.

:func:`rehydrate_graph_builder` reconstructs a finalized :class:`GraphBuilder`
from the persisted ``graph_nodes`` / ``graph_edges`` / ``graph_metrics`` rows so
doc generation can run against it without re-parsing, re-resolving imports/
calls/heritage, or recomputing centrality. The git-tier backfill is handled
separately by ``ingestion.git_indexer.backfill.backfill_full_tier``; the CLI
``repowise update --full`` flow stitches the two together (see
``cli.commands.upgrade_flow``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


async def rehydrate_graph_builder(
    session: Any,
    repo_id: str,
    repo_path: Path | str | None = None,
) -> Any:
    """Rebuild a :class:`GraphBuilder` from persisted rows for *repo_id*.

    Reads every persisted node and edge plus the materialized metric snapshot
    and hands them to ``GraphBuilder.from_persisted``. The returned builder is
    already finalized (``_built = True``) with file-level metrics served from
    SQL — no NetworkX centrality kernel runs and no resolution pass fires.

    Raises ``ValueError`` if the repo has no persisted graph nodes, which means
    it was never indexed (so there is nothing to upgrade).
    """
    from repowise.core.ingestion.graph import GraphBuilder
    from repowise.core.persistence import (
        get_all_graph_edges,
        get_all_graph_nodes,
        get_graph_metrics,
    )

    nodes = await get_all_graph_nodes(session, repo_id)
    if not nodes:
        raise ValueError(
            f"No persisted graph nodes for repo {repo_id!r}; run `repowise init` first."
        )
    edges = await get_all_graph_edges(session, repo_id)
    metrics = await get_graph_metrics(session, repo_id)

    builder = GraphBuilder.from_persisted(nodes, edges, metrics, repo_path=repo_path)
    log.info(
        "upgrade.graph_rehydrated",
        repo_id=repo_id,
        nodes=len(nodes),
        edges=len(edges),
        has_metrics=bool(metrics),
    )
    return builder
