"""Shared persistence logic for pipeline results.

Extracted from ``cli/commands/init_cmd.py`` so both the CLI and the server
can persist a ``PipelineResult`` without duplicating the upsert recipe.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def persist_pipeline_result(
    result: Any,
    session: Any,
    repo_id: str,
) -> None:
    """Persist all outputs from a :class:`PipelineResult` into the database.

    Parameters
    ----------
    result:
        A ``PipelineResult`` from ``run_pipeline()``.
    session:
        An active SQLAlchemy ``AsyncSession`` (caller manages commit/rollback).
    repo_id:
        The repository ID to associate all records with.

    Note
    ----
    FTS indexing is intentionally excluded here — callers must do it after
    this session closes to avoid SQLite write-lock conflicts.

    This function mutates ``sym.file_path`` on parsed-file symbols that
    lack one.  Callers should treat *result* as consumed after this call.
    """
    from repowise.core.persistence import (
        batch_upsert_graph_edges,
        batch_upsert_graph_nodes,
        batch_upsert_symbols,
        upsert_page_from_generated,
    )
    from repowise.core.persistence.crud import (
        bulk_upsert_decisions,
        recompute_decision_staleness,
        save_dead_code_findings,
        upsert_git_metadata_bulk,
    )

    # ---- Pages (if generated) -----------------------------------------------
    if result.generated_pages:
        for page in result.generated_pages:
            await upsert_page_from_generated(session, page, repo_id)

    # ---- Graph nodes ---------------------------------------------------------
    graph = result.graph_builder.graph()
    pr = result.graph_builder.pagerank()
    bc = result.graph_builder.betweenness_centrality()
    cd = result.graph_builder.community_detection()

    nodes = []
    for node_path in graph.nodes:
        data = graph.nodes[node_path]
        nodes.append(
            {
                "node_id": node_path,
                "symbol_count": data.get("symbol_count", 0),
                "has_error": data.get("has_error", False),
                "is_test": data.get("is_test", False),
                "is_entry_point": data.get("is_entry_point", False),
                "language": data.get("language", "unknown"),
                "pagerank": pr.get(node_path, 0.0),
                "betweenness": bc.get(node_path, 0.0),
                "community_id": cd.get(node_path, 0),
            }
        )
    if nodes:
        await batch_upsert_graph_nodes(session, repo_id, nodes)

    # ---- Graph edges ---------------------------------------------------------
    edges = []
    for u, v, data in graph.edges(data=True):
        edges.append(
            {
                "source_node_id": u,
                "target_node_id": v,
                "imported_names_json": json.dumps(data.get("imported_names", [])),
                "edge_type": data.get("edge_type", "imports"),
            }
        )
    if edges:
        await batch_upsert_graph_edges(session, repo_id, edges)

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
    # Choice: persist.py (rather than orchestrator.py) because there is already
    # a clear per-file loop over parsed_files here, and the instructions ask for
    # a minimal, non-invasive addition.  The orchestrator parse stage is owned
    # by another agent and must not be touched.
    try:
        from repowise.core.analysis.security_scan import SecurityScanner

        scanner = SecurityScanner(session, repo_id)
        for pf in result.parsed_files:
            source_text = getattr(pf.file_info, "content", "") or ""
            findings = await scanner.scan_file(
                pf.file_info.path, source_text, pf.symbols
            )
            if findings:
                await scanner.persist(pf.file_info.path, findings)
    except Exception as _sec_err:  # noqa: BLE001 — scanner must never break the pipeline
        logger.warning("security_scan_skipped", error=str(_sec_err))

    # ---- Git metadata --------------------------------------------------------
    if result.git_metadata_list:
        await upsert_git_metadata_bulk(session, repo_id, result.git_metadata_list)

    # ---- Dead code findings --------------------------------------------------
    if result.dead_code_report and result.dead_code_report.findings:
        await save_dead_code_findings(session, repo_id, result.dead_code_report.findings)

    # ---- Decision records ----------------------------------------------------
    if result.decision_report and result.decision_report.decisions:
        await bulk_upsert_decisions(
            session,
            repo_id,
            [dataclasses.asdict(d) for d in result.decision_report.decisions],
        )
        # Recompute staleness scores using git metadata.
        if result.git_metadata_list:
            try:
                git_meta_map: dict[str, dict] = {}
                for gm in result.git_metadata_list:
                    gm_dict = gm if isinstance(gm, dict) else dataclasses.asdict(gm)
                    fp = gm_dict.get("file_path", "")
                    if fp:
                        git_meta_map[fp] = gm_dict
                if git_meta_map:
                    updated = await recompute_decision_staleness(
                        session, repo_id, git_meta_map
                    )
                    if updated:
                        logger.info("decision_staleness_recomputed", updated=updated)
            except Exception as _stale_err:
                logger.debug("staleness_scoring_skipped", error=str(_stale_err))

    logger.info(
        "pipeline_result_persisted",
        repo_id=repo_id,
        pages=len(result.generated_pages) if result.generated_pages else 0,
        graph_nodes=len(nodes),
        symbols=len(all_symbols),
        git_files=len(result.git_metadata_list),
    )
