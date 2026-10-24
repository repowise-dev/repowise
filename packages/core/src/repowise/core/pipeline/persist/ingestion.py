"""Ingestion-phase persistence: files, symbols, graph and edges."""

from __future__ import annotations

from typing import Any

import structlog

# Patchable steps are looked up on the package at call time, so a patch on
# ``repowise.core.pipeline.persist`` reaches this module too.
from repowise.core.pipeline import persist as _persist

from .graph import _edge_row, has_graph_edges, stamp_edges_parser_fingerprint

logger = structlog.get_logger("repowise.core.pipeline.persist")


async def persist_ingestion(
    result: Any, session: Any, repo_id: str, *, timings: Any | None = None
) -> int:
    """Persist ingestion-phase outputs: graph nodes/edges, external systems,
    symbols, and the per-file security scan.

    Every write here is an idempotent UPSERT keyed by ``(repo_id, …)``, so
    this is safe to call incrementally (per phase) and to re-run on resume.

    ``timings`` is an optional :class:`PhaseTimings` table; when given, the
    security scan is timed into it as ``persist.security_scan`` so a caller that
    times the whole write can see how much of it was the scan.

    Returns the number of symbols written (for the summary log). Mutates
    ``sym.file_path`` on symbols that lack one — callers should treat the
    parsed-file symbols as consumed after this call.
    """
    from repowise.core.persistence import (
        batch_upsert_graph_edges,
        batch_upsert_symbols,
        bulk_upsert_external_systems,
        link_graph_nodes_to_external_systems,
    )
    from repowise.core.pipeline.phase_timing import timed

    # ---- Graph nodes ---------------------------------------------------------
    # Prefer the full candidate-score map (all scored entry points), falling
    # back to deriving from the builder when no report rode along on the
    # result. Passing ``None`` lets persist_graph_nodes derive them itself.
    report = result.execution_flow_report
    ep_scores: dict[str, float] | None
    if report is not None and getattr(report, "entry_point_scores", None):
        ep_scores = dict(report.entry_point_scores)
    elif report is not None and getattr(report, "flows", None):
        ep_scores = {
            f.entry_point_id: f.entry_point_score
            for f in report.flows
            if hasattr(f, "entry_point_id") and hasattr(f, "entry_point_score")
        }
    else:
        ep_scores = None
    await _persist.persist_graph_nodes(session, repo_id, result.graph_builder, ep_scores)

    # ---- Graph edges ---------------------------------------------------------
    graph = result.graph_builder.graph()
    edges = [_edge_row(u, v, data) for u, v, data in graph.edges(data=True)]
    fresh_store = not await has_graph_edges(session, repo_id)
    if edges:
        await batch_upsert_graph_edges(session, repo_id, edges)
    if fresh_store:
        from repowise.core.ingestion.parse_cache import parser_fingerprint

        await stamp_edges_parser_fingerprint(session, repo_id, parser_fingerprint())

    # ---- External systems (C4 L1) -------------------------------------------
    # Persist before symbols so the FK linkage step below sees the IDs.
    external_systems = getattr(result, "external_systems", None) or []
    if external_systems:
        id_map = await bulk_upsert_external_systems(session, repo_id, external_systems)
        from repowise.core.persistence.crud import build_external_system_link_map

        name_to_id = build_external_system_link_map(external_systems, id_map)
        await link_graph_nodes_to_external_systems(session, repo_id, name_to_id)

    # ---- Symbols -------------------------------------------------------------
    # NOTE: This mutates sym.file_path on the caller's PipelineResult objects.
    # The guard prevents double-set on retries, but callers should treat the
    # result as consumed after this call.
    all_symbols = []
    for pf in result.parsed_files:
        for sym in pf.symbols:
            if not getattr(sym, "file_path", None):
                sym.file_path = pf.file_info.path
            all_symbols.append(sym)
    if all_symbols:
        await batch_upsert_symbols(session, repo_id, all_symbols)

    # ---- Security scan -------------------------------------------------------
    # Best-effort — never breaks the rest of the phase.
    try:
        with timed(timings, "persist.security_scan"):
            await _persist.persist_security_findings(result, session, repo_id)
    except Exception as _sec_err:
        logger.warning("security_scan_skipped", error=str(_sec_err))

    return len(all_symbols)
