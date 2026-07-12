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


def tombstone_candidates(file_diffs: list[Any]) -> list[tuple[str, list[str]]]:
    """(dead_path, successor_paths) pairs from deleted/renamed file diffs.

    A renamed file's old path gets its new path as the successor; a deleted
    file has no successor. Diffs of other statuses contribute nothing.
    """
    out: list[tuple[str, list[str]]] = []
    for fd in file_diffs or []:
        status = getattr(fd, "status", None)
        if status == "deleted" and fd.path:
            out.append((fd.path, []))
        elif status == "renamed" and fd.old_path:
            out.append((fd.old_path, [fd.path] if fd.path else []))
    return out


async def mark_tombstone_pages(
    session: Any, repo_id: str, candidates: list[tuple[str, list[str]]]
) -> int:
    """Mark file pages for deleted/renamed files as tombstones.

    A ``freshness_status="fresh"`` page for a file that no longer exists is
    an active trap: retrieval serves it, agents cite it, and the index-age
    metadata says nothing is wrong. Tombstoned pages keep their content
    (the prose may still orient a reader) but carry status=tombstone and
    ``successor_paths`` in metadata so serving layers can skip or redirect.

    Returns the number of pages marked.
    """
    if not candidates:
        return 0
    from sqlalchemy import select

    from repowise.core.persistence.models import Page

    page_ids = {f"file_page:{path}": (path, successors) for path, successors in candidates}
    res = await session.execute(
        select(Page).where(Page.repository_id == repo_id, Page.id.in_(page_ids))
    )
    marked = 0
    for page in res.scalars().all():
        _, successors = page_ids[page.id]
        page.freshness_status = "tombstone"
        try:
            meta = json.loads(page.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        meta["successor_paths"] = successors
        page.metadata_json = json.dumps(meta)
        marked += 1
    if marked:
        logger.info("pages_tombstoned", repo_id=repo_id, count=marked)
    elif candidates:
        # Candidates existed but no page matched — the id scheme drifted from
        # ``file_page:{path}`` or the paths don't line up. Silent success here
        # would let stale pages keep serving, so surface it.
        logger.debug(
            "tombstone_no_match",
            repo_id=repo_id,
            candidate_count=len(candidates),
            sample=[p for p, _ in candidates[:3]],
        )
    return marked


async def mark_stale_pages(session: Any, repo_id: str, paths: list[str]) -> int:
    """Decay weakly-affected file pages to ``freshness_status='stale'``.

    ``ChangeDetector.get_affected_pages`` returns ``decay_only`` — pages hit
    by the change cascade but beyond the regeneration budget (budget
    overflow, co-change partners, 2-hop rename fallout). They keep serving
    their existing content, but the stale bit makes the coverage view and
    ``get_stale_pages`` truthful so the next docs run (or a reader) knows
    they lag the code. Only ``fresh`` pages are downgraded — tombstoned or
    already-stale pages keep their stronger status, and pages regenerated in
    this run are never in ``decay_only`` by construction.

    Returns the number of pages marked.
    """
    if not paths:
        return 0
    from sqlalchemy import update

    from repowise.core.persistence.models import Page

    page_ids = [f"file_page:{path}" for path in paths]
    res = await session.execute(
        update(Page)
        .where(
            Page.repository_id == repo_id,
            Page.id.in_(page_ids),
            Page.freshness_status == "fresh",
        )
        .values(freshness_status="stale")
    )
    marked = int(res.rowcount or 0)
    if marked:
        logger.info("pages_decayed_stale", repo_id=repo_id, count=marked)
    return marked


def _derive_entry_point_scores(graph_builder: Any) -> dict[str, float]:
    """Best-effort entry-point scores from the builder's execution-flow report.

    Returns ``{node_id: score}`` for every scored entry-point candidate (not
    just the ones that produced a traced flow). The call is cached on the
    builder, so this is cheap when flows were already computed.
    """
    try:
        report = graph_builder.execution_flows()
    except Exception as exc:  # analysis is non-load-bearing for persistence
        logger.warning("entry_point_scores_derive_failed", error=str(exc))
        return {}
    if report is None:
        return {}
    scores = getattr(report, "entry_point_scores", None)
    if scores:
        return dict(scores)
    # Back-compat: older reports only expose per-flow scores.
    return {
        f.entry_point_id: f.entry_point_score
        for f in getattr(report, "flows", []) or []
        if hasattr(f, "entry_point_id") and hasattr(f, "entry_point_score")
    }


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
        batch_upsert_graph_node_membership,
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
    # ``None`` means "derive scores from the graph" (the incremental update
    # path passes nothing). An explicit ``{}`` means "no scores" and is left
    # untouched. Without this, every ``update`` re-upserted symbol nodes with
    # empty community_meta and wiped the entry_point_scores written at init,
    # leaving get_execution_flows / the dashboard panel permanently empty.
    if ep_scores is None:
        ep_scores = _derive_entry_point_scores(graph_builder)

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

    # Materialize file-level SCCs (import cycles) + symbol communities as
    # queryable rows (graph_node_membership). Feeds the break-cycle /
    # move-method refactoring surfaces; non-load-bearing like graph_metrics.
    try:
        await batch_upsert_graph_node_membership(
            session, repo_id, graph_builder.node_membership_snapshot()
        )
    except Exception as exc:  # materialization is non-load-bearing
        logger.warning("graph_node_membership_materialize_skipped", error=str(exc))


def _changed_file_symbols(
    parsed_files: list[Any] | None, changed_paths: list[str]
) -> tuple[list[str], list[Any]]:
    """``(reconcile_paths, symbols)`` for files that both changed and parsed.

    Restricting to the intersection means a changed file that failed to parse
    this run keeps its existing symbol rows (mirrors the graph, which skips
    unparsed files) rather than having them wrongly pruned on a transient
    failure. Mutates ``sym.file_path`` where the parser left it unset, same as
    the full persist path.
    """
    changed = set(changed_paths or [])
    reconcile_paths: list[str] = []
    symbols: list[Any] = []
    for pf in parsed_files or []:
        path = pf.file_info.path
        if path not in changed:
            continue
        reconcile_paths.append(path)
        for sym in pf.symbols:
            if not getattr(sym, "file_path", None):
                sym.file_path = path
            symbols.append(sym)
    return reconcile_paths, symbols


async def persist_incremental_symbols(
    session: Any,
    repo_id: str,
    parsed_files: list[Any] | None,
    changed_paths: list[str],
) -> None:
    """Refresh ``wiki_symbols`` for changed+parsed files on an incremental update.

    The incremental update path re-parses changed files but never persisted
    their symbols, so wiki_symbols bounds fossilized at the last full index and
    the get_answer hydrator served drifted signatures/bodies. This upserts the
    changed files' fresh symbols and prunes symbols that vanished from a
    still-existing file. Scoped to the changed set for cost — the repo-wide
    ``batch_upsert_symbols`` reloads every symbol row.
    """
    if not parsed_files:
        return
    from repowise.core.persistence.crud import reconcile_symbols_for_files

    reconcile_paths, symbols = _changed_file_symbols(parsed_files, changed_paths)
    if not reconcile_paths:
        return
    await reconcile_symbols_for_files(session, repo_id, reconcile_paths, symbols)


# Chunk size for IN (...) deletes — stays under SQLite's host-parameter limit.
_PRUNE_CHUNK = 500


async def _prune_stale_file_rows(
    session: Any,
    repo_id: str,
    current_graph_file_paths: set[str],
    current_git_file_paths: set[str],
) -> None:
    """Delete file-scoped rows for files absent from the latest full pipeline run.

    The parser and git indexer disagree on the file set — a file can be
    git-tracked yet absent from ``parsed_files`` (parse failure, unparsed
    extension, skipped) — so the tables use different sources of truth.
    *current_graph_file_paths* (from ``parsed_files``) governs graph/analysis
    tables; *current_git_file_paths* (from ``git_metadata_list``) governs
    ``git_metadata`` only. Each set independently no-ops when empty to avoid
    wiping rows on a broken run. FULL persistence only — not incremental paths.
    """
    from sqlalchemy import delete, or_, select

    from repowise.core.persistence.models import (
        DeadCodeFinding,
        GitMetadata,
        GraphEdge,
        GraphMetric,
        GraphNode,
        HealthFileMetric,
        HealthFinding,
        SecurityFinding,
        WikiSymbol,
    )

    async def _delete_stale_by_paths(model: Any, column: Any, current: set[str]) -> None:
        # Diff persisted paths against *current* in Python so the IN (...) is
        # bounded by the stale set, not the whole repo (SQLite param limit).
        if not current:
            return
        existing = set(
            (await session.execute(select(column).where(model.repository_id == repo_id).distinct()))
            .scalars()
            .all()
        )
        stale = [p for p in existing if p not in current]
        for i in range(0, len(stale), _PRUNE_CHUNK):
            await session.execute(
                delete(model).where(
                    model.repository_id == repo_id,
                    column.in_(stale[i : i + _PRUNE_CHUNK]),
                )
            )

    # ---- Graph nodes + edges -------------------------------------------------
    # File nodes key on node_id; symbol nodes on file_path. Delete edges before
    # nodes (no FK cascade between the tables).
    if current_graph_file_paths:
        node_rows = (
            await session.execute(
                select(GraphNode.node_id, GraphNode.node_type, GraphNode.file_path).where(
                    GraphNode.repository_id == repo_id
                )
            )
        ).all()
        stale_node_ids = [
            node_id
            for node_id, node_type, file_path in node_rows
            if (node_type == "file" and node_id not in current_graph_file_paths)
            or (
                node_type != "file"
                and file_path is not None
                and file_path not in current_graph_file_paths
            )
        ]
        for i in range(0, len(stale_node_ids), _PRUNE_CHUNK):
            batch = stale_node_ids[i : i + _PRUNE_CHUNK]
            await session.execute(
                delete(GraphEdge).where(
                    GraphEdge.repository_id == repo_id,
                    or_(
                        GraphEdge.source_node_id.in_(batch),
                        GraphEdge.target_node_id.in_(batch),
                    ),
                )
            )
            await session.execute(
                delete(GraphNode).where(
                    GraphNode.repository_id == repo_id,
                    GraphNode.node_id.in_(batch),
                )
            )

    # GraphMetric is file-level only (node_id == path).
    await _delete_stale_by_paths(GraphMetric, GraphMetric.node_id, current_graph_file_paths)
    await _delete_stale_by_paths(WikiSymbol, WikiSymbol.file_path, current_graph_file_paths)
    await _delete_stale_by_paths(
        SecurityFinding, SecurityFinding.file_path, current_graph_file_paths
    )
    await _delete_stale_by_paths(
        DeadCodeFinding, DeadCodeFinding.file_path, current_graph_file_paths
    )
    await _delete_stale_by_paths(
        HealthFileMetric, HealthFileMetric.file_path, current_graph_file_paths
    )
    await _delete_stale_by_paths(HealthFinding, HealthFinding.file_path, current_graph_file_paths)
    await _delete_stale_by_paths(GitMetadata, GitMetadata.file_path, current_git_file_paths)


# Generated page types keyed on run-scoped structure: module/scc pages on
# clustering ordinals, layer pages on display names. Those keys shift between
# runs, so re-runs mint fresh page ids and the previous rows linger as
# duplicates unless swept against the current run's output.
_SWEPT_GENERATED_PAGE_TYPES = ("module_page", "layer_page", "scc_page")


async def _sweep_stale_generated_pages(
    session: Any,
    repo_id: str,
    generated_pages: list[Any] | None,
    authoritative_page_types: set[str] | None = None,
) -> list[str]:
    """Delete structurally-keyed generated pages this run did not produce.

    Sweeps a page type when the run either produced at least one page of it OR
    declared itself authoritative for it (``authoritative_page_types`` — set by
    the generation layer when it fully decided the type, even if that decision
    was "emit none"; e.g. a curated run whose modules all collapsed into their
    layers via ``wholeLayer``). A type that is neither produced nor authoritative
    is left untouched, so a skipped/failed/degraded level never wipes the last
    good set. When authoritative-but-empty, the current set is empty and every
    prior row of that type is retired. Page versions go with their page (FK
    enforcement requires it, and a retired structural id never comes back to
    claim its history). Returns the swept page ids so the caller can drop them
    from FTS after the session closes (the FTS store must not be touched
    in-session).
    """
    from sqlalchemy import delete, select

    from repowise.core.persistence.models import Page, PageVersion

    produced: dict[str, set[str]] = {}
    for page in generated_pages or []:
        produced.setdefault(page.page_type, set()).add(page.page_id)
    authoritative = authoritative_page_types or set()

    swept: list[str] = []
    for page_type in _SWEPT_GENERATED_PAGE_TYPES:
        current = produced.get(page_type)
        if not current and page_type not in authoritative:
            continue
        current = current or set()
        existing = (
            (
                await session.execute(
                    select(Page.id).where(
                        Page.repository_id == repo_id, Page.page_type == page_type
                    )
                )
            )
            .scalars()
            .all()
        )
        stale = [pid for pid in existing if pid not in current]
        for i in range(0, len(stale), _PRUNE_CHUNK):
            batch = stale[i : i + _PRUNE_CHUNK]
            await session.execute(delete(PageVersion).where(PageVersion.page_id.in_(batch)))
            await session.execute(
                delete(Page).where(Page.repository_id == repo_id, Page.id.in_(batch))
            )
        swept.extend(stale)

    if swept:
        logger.info("stale_generated_pages_swept", repo_id=repo_id, count=len(swept))
    return swept


async def persist_ingestion(result: Any, session: Any, repo_id: str) -> int:
    """Persist ingestion-phase outputs: graph nodes/edges, external systems,
    symbols, and the per-file security scan.

    Every write here is an idempotent UPSERT keyed by ``(repo_id, …)``, so
    this is safe to call incrementally (per phase) and to re-run on resume.

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
    # There is already a clear per-file loop over parsed_files here, so the
    # scan rides alongside symbol persistence. Best-effort — never breaks
    # the rest of the phase.
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

    return len(all_symbols)


async def persist_git(result: Any, session: Any, repo_id: str) -> None:
    """Persist git-phase outputs: per-file metadata and per-commit rows.

    Both writes are idempotent UPSERTs keyed by ``(repo_id, file_path)`` /
    ``(repo_id, sha)`` — safe to call incrementally and on resume.
    """
    from repowise.core.persistence.crud import (
        upsert_git_commits_bulk,
        upsert_git_metadata_bulk,
    )

    if result.git_metadata_list:
        await upsert_git_metadata_bulk(session, repo_id, result.git_metadata_list)

    # Per-commit rows + change-risk ride on the git summary.
    commit_rows = getattr(getattr(result, "git_summary", None), "commit_rows", None)
    if commit_rows:
        await upsert_git_commits_bulk(session, repo_id, commit_rows)


async def persist_analysis(result: Any, session: Any, repo_id: str) -> None:
    """Persist analysis-phase outputs: dead code, health, decisions, governance.

    Dead-code and health writes are repo-wide DELETE-THEN-INSERT (so they
    converge on re-run but don't support partial-within-phase resume);
    decisions/governance are idempotent. Intended to run once the analysis
    phase has fully completed.
    """
    from repowise.core.persistence.crud import (
        bulk_upsert_decisions,
        recompute_decision_staleness,
        save_coverage_files,
        save_dead_code_findings,
        save_health_findings,
        save_health_metrics,
        save_health_snapshot,
        save_refactoring_suggestions,
        upsert_git_function_blame_bulk,
    )

    # ---- Dead code findings --------------------------------------------------
    if result.dead_code_report and result.dead_code_report.findings:
        await save_dead_code_findings(session, repo_id, result.dead_code_report.findings)

    # ---- Health findings + per-file metrics ---------------------------------
    if getattr(result, "health_report", None):
        hr = result.health_report
        await save_health_metrics(session, repo_id, hr.metrics or [])
        if hr.findings:
            await save_health_findings(session, repo_id, hr.findings)
        # Resolved coverage rows, when a report was ingested this run.
        coverage_files = getattr(hr, "coverage_files", None)
        if coverage_files:
            head_sha = getattr(result, "head_commit", None) or getattr(result, "commit_sha", None)
            await save_coverage_files(
                session,
                repo_id,
                coverage_files,
                source_format=getattr(hr, "coverage_format", None) or "lcov",
                ingested_commit_sha=head_sha,
            )
        # Structured refactoring suggestions (Extract Class, ...). Repo-wide
        # delete-then-insert like findings; empty list clears prior rows.
        await save_refactoring_suggestions(
            session, repo_id, getattr(hr, "refactoring_suggestions", None) or []
        )
        # Per-function blame rollup (FULL tier only; empty otherwise).
        fn_blame_rows = getattr(hr, "function_blame_rows", None)
        if fn_blame_rows:
            await upsert_git_function_blame_bulk(session, repo_id, fn_blame_rows)
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

    # One-shot drain of proposals from the removed code_comment harvest;
    # without this, DBs indexed before its removal keep a flooded review
    # queue forever (#751). Confirmed/dismissed rows are kept.
    try:
        from repowise.core.persistence.crud import purge_proposed_decisions_by_source

        await purge_proposed_decisions_by_source(session, repo_id, "code_comment")
    except Exception as _purge_err:
        logger.debug("decision_purge_skipped", error=str(_purge_err))

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


async def persist_generation(result: Any, session: Any, repo_id: str) -> None:
    """Persist generation-phase outputs: wiki pages and knowledge-graph layers.

    Pages upsert per ``page_id`` (archiving prior versions); KG layers/tour
    are full-replace. Both safe to call incrementally / on resume.
    """
    from repowise.core.persistence import upsert_page_from_generated

    # ---- Pages (if generated) -----------------------------------------------
    if result.generated_pages:
        for page in result.generated_pages:
            await upsert_page_from_generated(session, page, repo_id)

    # ---- Knowledge graph layers, tour steps & curated meta ------------------
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        await persist_kg(kg, session, repo_id)


async def persist_kg(kg: Any, session: Any, repo_id: str) -> None:
    """Persist knowledge-graph layers, tour steps, and curated meta.

    Full-replace semantics, safe to call incrementally — shared by the full
    pipeline (:func:`persist_generation`) and the incremental update path so
    a refreshed KG lands through the same writers.
    """
    from repowise.core.persistence.crud import (
        file_node_meta_from_kg_nodes,
        upsert_kg_layers,
        upsert_kg_node_meta,
        upsert_kg_project_meta,
        upsert_kg_tour_steps,
    )

    if hasattr(kg, "layers") and kg.layers:
        await upsert_kg_layers(session, repo_id, kg.layers)
    if hasattr(kg, "tour") and kg.tour:
        await upsert_kg_tour_steps(session, repo_id, kg.tour)

    # Project-level curated meta (ranked entry points from the curation pass).
    project = getattr(kg, "project", None)
    if isinstance(project, dict) and project.get("entry_points"):
        await upsert_kg_project_meta(
            session,
            repo_id,
            entry_points=project["entry_points"],
            entry_candidates=project.get("entry_candidates", []),
        )

    # Per-node curated meta (type/summary/tags) for file nodes, stored with
    # the "file:" prefix stripped so the architecture view can match its
    # node ids (plain repo-relative paths) directly.
    file_node_meta = file_node_meta_from_kg_nodes(getattr(kg, "nodes", None) or [])
    if file_node_meta:
        await upsert_kg_node_meta(session, repo_id, file_node_meta)


async def persist_pipeline_result(
    result: Any,
    session: Any,
    repo_id: str,
) -> list[str]:
    """Persist all outputs from a :class:`PipelineResult` into the database.

    Thin composition of the four phase-scoped persisters
    (:func:`persist_ingestion`, :func:`persist_git`, :func:`persist_analysis`,
    :func:`persist_generation`) in dependency order. The same functions are
    reused by the incremental-persistence path so a resumed run can persist
    one phase at a time.

    Parameters
    ----------
    result:
        A ``PipelineResult`` from ``run_pipeline()``.
    session:
        An active SQLAlchemy ``AsyncSession`` (caller manages commit/rollback).
    repo_id:
        The repository ID to associate all records with.

    Returns
    -------
    The page ids of stale generated pages swept by this run. Callers should
    remove them from the FTS index after the session closes.

    Note
    ----
    FTS indexing is intentionally excluded here — callers must do it after
    this session closes to avoid SQLite write-lock conflicts.

    This function mutates ``sym.file_path`` on parsed-file symbols that
    lack one.  Callers should treat *result* as consumed after this call.
    """
    # Prune rows for files absent from this full result before the phase
    # persisters re-upsert. graph/analysis tables key off parsed_files;
    # git_metadata keys off the git indexer's set (a file can be git-tracked
    # but unparsed). Runs only here, never in the reusable phase persisters.
    current_graph_file_paths = {pf.file_info.path for pf in result.parsed_files}
    current_git_file_paths = {
        (gm if isinstance(gm, dict) else dataclasses.asdict(gm)).get("file_path", "")
        for gm in result.git_metadata_list
    }
    current_git_file_paths.discard("")
    await _prune_stale_file_rows(session, repo_id, current_graph_file_paths, current_git_file_paths)

    symbol_count = await persist_ingestion(result, session, repo_id)
    await persist_git(result, session, repo_id)
    await persist_analysis(result, session, repo_id)
    await persist_generation(result, session, repo_id)

    # Sweep structurally-keyed generated pages (module/layer/scc) that this
    # run did not reproduce — their ids drift between runs, so without the
    # sweep every re-index strands the previous set as duplicates. Full runs
    # only, same rule as _prune_stale_file_rows.
    swept_page_ids = await _sweep_stale_generated_pages(
        session,
        repo_id,
        result.generated_pages,
        getattr(result, "authoritative_page_types", None),
    )

    logger.info(
        "pipeline_result_persisted",
        repo_id=repo_id,
        pages=len(result.generated_pages) if result.generated_pages else 0,
        graph_nodes=result.graph_builder.graph().number_of_nodes(),
        symbols=symbol_count,
        git_files=len(result.git_metadata_list),
    )
    return swept_page_ids
