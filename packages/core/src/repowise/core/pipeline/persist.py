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


async def persist_graph_nodes(
    session: Any,
    repo_id: str,
    graph_builder: Any,
    ep_scores: dict[str, float] | None = None,
) -> None:
    """Persist file- and symbol-level graph nodes with full centrality metrics.

    Lifted out of :func:`persist_pipeline_result` so the incremental
    update path can refresh ``graph_nodes`` (including symbol-level
    PageRank / betweenness) without constructing a full ``PipelineResult``.
    """
    from repowise.core.persistence import (
        batch_upsert_graph_metrics,
        batch_upsert_graph_nodes,
    )

    graph = graph_builder.graph()
    pr = graph_builder.pagerank()
    bc = graph_builder.betweenness_centrality()
    sym_pr = graph_builder.symbol_pagerank()
    sym_bc = graph_builder.symbol_betweenness_centrality()
    cd = graph_builder.community_detection()
    sc = graph_builder.symbol_communities()
    ci = graph_builder.community_info()
    ep_scores = ep_scores or {}

    nodes = []
    for node_id in graph.nodes:
        data = graph.nodes[node_id]
        node_type = data.get("node_type", "file")

        node_dict: dict[str, Any] = {
            "node_id": node_id,
            "node_type": node_type,
            "language": data.get("language", "unknown"),
            "symbol_count": data.get("symbol_count", 0),
            "has_error": data.get("has_error", False),
            "is_test": data.get("is_test", False),
            "is_entry_point": data.get("is_entry_point", False),
            # Files draw from the file-level metric tables; symbols fall
            # back to the symbol subgraph (calls + heritage) so that the
            # per-symbol UI panel shows real centrality instead of 0.
            "pagerank": pr.get(node_id, sym_pr.get(node_id, 0.0)),
            "betweenness": bc.get(node_id, sym_bc.get(node_id, 0.0)),
            "community_id": cd.get(node_id, 0),
        }

        community_meta: dict[str, Any] = {}
        if node_type == "file":
            cid = cd.get(node_id, 0)
            comm_info = ci.get(cid)
            if comm_info:
                community_meta = {
                    "label": comm_info.label,
                    "cohesion": comm_info.cohesion,
                }
        elif node_type == "symbol":
            sym_cid = sc.get(node_id)
            if sym_cid is not None:
                community_meta = {"symbol_community_id": sym_cid}
            if node_id in ep_scores:
                community_meta["entry_point_score"] = ep_scores[node_id]
        node_dict["community_meta_json"] = json.dumps(community_meta)

        if node_type == "symbol":
            node_dict.update(
                {
                    "kind": data.get("kind"),
                    "name": data.get("name"),
                    "qualified_name": data.get("qualified_name"),
                    "file_path": data.get("file_path"),
                    "start_line": data.get("start_line"),
                    "end_line": data.get("end_line"),
                    "visibility": data.get("visibility"),
                    "signature": data.get("signature"),
                    "parent_symbol_id": data.get("parent_name"),
                }
            )
        nodes.append(node_dict)

    if nodes:
        await batch_upsert_graph_nodes(session, repo_id, nodes)

    # Materialize the file-level metrics snapshot (graph_metrics) so large
    # repos can serve metric reads from SQL without recomputing the NetworkX
    # centrality kernels. Additive to graph_nodes; never changes node rows.
    try:
        await batch_upsert_graph_metrics(session, repo_id, graph_builder.file_metrics_snapshot())
    except Exception as exc:  # materialization is non-load-bearing
        logger.warning("graph_metrics_materialize_skipped", error=str(exc))


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
        batch_upsert_symbols,
        bulk_upsert_external_systems,
        link_graph_nodes_to_external_systems,
        upsert_page_from_generated,
    )
    from repowise.core.persistence.crud import (
        bulk_upsert_decisions,
        recompute_decision_staleness,
        save_dead_code_findings,
        save_health_findings,
        save_health_metrics,
        save_health_snapshot,
        upsert_git_commits_bulk,
        upsert_git_metadata_bulk,
    )

    # ---- Pages (if generated) -----------------------------------------------
    if result.generated_pages:
        for page in result.generated_pages:
            await upsert_page_from_generated(session, page, repo_id)

    # ---- Graph nodes ---------------------------------------------------------
    ep_scores: dict[str, float] = {}
    if result.execution_flow_report and getattr(result.execution_flow_report, "flows", None):
        ep_scores = {
            f.entry_point_id: f.entry_point_score
            for f in result.execution_flow_report.flows
            if hasattr(f, "entry_point_id") and hasattr(f, "entry_point_score")
        }
    await persist_graph_nodes(session, repo_id, result.graph_builder, ep_scores)

    # ---- Graph edges ---------------------------------------------------------
    graph = result.graph_builder.graph()
    edges = []
    for u, v, data in graph.edges(data=True):
        edges.append(
            {
                "source_node_id": u,
                "target_node_id": v,
                "imported_names_json": json.dumps(data.get("imported_names", [])),
                "edge_type": data.get("edge_type", "imports"),
                "confidence": data.get("confidence", 1.0),
            }
        )
    if edges:
        await batch_upsert_graph_edges(session, repo_id, edges)

    # ---- External systems (C4 L1) -------------------------------------------
    # Persist before symbols so the FK linkage step below sees the IDs.
    external_systems = getattr(result, "external_systems", None) or []
    if external_systems:
        id_map = await bulk_upsert_external_systems(session, repo_id, external_systems)
        # Collapse multi-manifest duplicates: any id for a given name is fine
        # (renderer only needs name/category/ecosystem which are stable).
        name_to_id: dict[str, int] = {}
        for (name, _declared_in), sys_id in id_map.items():
            name_to_id.setdefault(name, sys_id)
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
    # Choice: persist.py (rather than orchestrator.py) because there is already
    # a clear per-file loop over parsed_files here, and the instructions ask for
    # a minimal, non-invasive addition.  The orchestrator parse stage is owned
    # by another agent and must not be touched.
    try:
        from repowise.core.analysis.security_scan import SecurityScanner

        scanner = SecurityScanner(session, repo_id)
        for pf in result.parsed_files:
            source_text = getattr(pf.file_info, "content", "") or ""
            findings = await scanner.scan_file(pf.file_info.path, source_text, pf.symbols)
            if findings:
                await scanner.persist(pf.file_info.path, findings)
    except Exception as _sec_err:
        logger.warning("security_scan_skipped", error=str(_sec_err))

    # ---- Git metadata --------------------------------------------------------
    if result.git_metadata_list:
        await upsert_git_metadata_bulk(session, repo_id, result.git_metadata_list)

    # ---- Per-commit rows + change-risk (ride on the git summary) -------------
    commit_rows = getattr(getattr(result, "git_summary", None), "commit_rows", None)
    if commit_rows:
        await upsert_git_commits_bulk(session, repo_id, commit_rows)

    # ---- Dead code findings --------------------------------------------------
    if result.dead_code_report and result.dead_code_report.findings:
        await save_dead_code_findings(session, repo_id, result.dead_code_report.findings)

    # ---- Health findings + per-file metrics ---------------------------------
    if getattr(result, "health_report", None):
        hr = result.health_report
        await save_health_metrics(session, repo_id, hr.metrics or [])
        if hr.findings:
            await save_health_findings(session, repo_id, hr.findings)
        # Snapshot the run for trend tracking (rolling delete inside).
        kpis = hr.kpis or {}
        try:
            await save_health_snapshot(
                session,
                repo_id,
                hotspot_health=float(kpis.get("hotspot_health", 10.0)),
                average_health=float(kpis.get("average_health", 10.0)),
                worst_performer_path=kpis.get("worst_performer_path"),
                worst_performer_score=kpis.get("worst_performer_score"),
                per_file_scores={m.file_path: round(float(m.score), 2) for m in hr.metrics or []},
            )
        except Exception as _snap_err:
            logger.warning("health_snapshot_skipped", error=str(_snap_err))

    # ---- Decision records ----------------------------------------------------
    # Two contributors merge into one upsert: the multi-source extractor
    # (decision_report) and the Phase-2 LLM-docs harvest (ridden on each
    # generated page's metadata, already gated at generation time). Folding
    # them into a single bulk_upsert lets harvested candidates corroborate
    # extracted decisions (extra evidence row + confidence bump) or stand alone
    # as low-rank ``proposed`` records awaiting review.
    decision_dicts: list[dict] = []
    if result.decision_report and result.decision_report.decisions:
        decision_dicts.extend(dataclasses.asdict(d) for d in result.decision_report.decisions)
    if result.generated_pages:
        for page in result.generated_pages:
            harvested = page.metadata.get("harvested_decisions")
            if harvested:
                decision_dicts.extend(harvested)

    if decision_dicts:
        # Reuse the run's shared vector store for semantic (paraphrase) dedup
        # and to make decisions searchable; title dedup still runs when None.
        store = getattr(result, "vector_store", None)
        touched_ids = await bulk_upsert_decisions(
            session,
            repo_id,
            decision_dicts,
            vector_store=store,
        )
        # Phase 3B: detect supersession/conflict among the just-upserted
        # decisions and record typed edges (auto-flipping the older only above
        # the high-confidence threshold). Heuristic-only here (no provider on
        # the persist path); the update path adds the gated LLM tiebreaker.
        if touched_ids and store is not None:
            try:
                from repowise.core.analysis.decision_evolution import (
                    detect_supersessions_and_conflicts,
                )

                evo = await detect_supersessions_and_conflicts(
                    session,
                    repo_id,
                    touched_ids=touched_ids,
                    vector_store=store,
                )
                if any(evo.values()):
                    logger.info("decision_supersession_detected", **evo)
            except Exception as _evo_err:
                logger.debug("supersession_detection_skipped", error=str(_evo_err))
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
                    updated = await recompute_decision_staleness(session, repo_id, git_meta_map)
                    if updated:
                        logger.info("decision_staleness_recomputed", updated=updated)
            except Exception as _stale_err:
                logger.debug("staleness_scoring_skipped", error=str(_stale_err))

    # ---- Governance findings (additive pass, after decisions are persisted) ----
    # Runs after bulk_upsert_decisions + detect_supersessions_and_conflicts so
    # the decision graph is complete. Best-effort — never breaks persist.
    try:
        from sqlalchemy import select as _select

        from repowise.core.analysis.health.governance import build_governance_findings
        from repowise.core.persistence.crud import (
            get_decision_health_summary,
            replace_governance_findings,
        )
        from repowise.core.persistence.models import DecisionRecord

        _dr_result = await session.execute(
            _select(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
        )
        _decisions = list(_dr_result.scalars().all())
        _health_summary = await get_decision_health_summary(session, repo_id)
        _gov_findings = build_governance_findings(
            health_summary=_health_summary,
            decisions=_decisions,
        )
        await replace_governance_findings(session, repo_id, _gov_findings)
        if _gov_findings:
            logger.info(
                "governance_findings_persisted",
                repo_id=repo_id,
                count=len(_gov_findings),
            )
    except Exception as _gov_err:
        logger.debug("governance_findings_skipped", error=str(_gov_err))

    # ---- Knowledge graph layers & tour steps -----------------------------------
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        from repowise.core.persistence.crud import upsert_kg_layers, upsert_kg_tour_steps

        if hasattr(kg, "layers") and kg.layers:
            await upsert_kg_layers(session, repo_id, kg.layers)
        if hasattr(kg, "tour") and kg.tour:
            await upsert_kg_tour_steps(session, repo_id, kg.tour)

    logger.info(
        "pipeline_result_persisted",
        repo_id=repo_id,
        pages=len(result.generated_pages) if result.generated_pages else 0,
        graph_nodes=result.graph_builder.graph().number_of_nodes(),
        symbols=len(all_symbols),
        git_files=len(result.git_metadata_list),
    )
