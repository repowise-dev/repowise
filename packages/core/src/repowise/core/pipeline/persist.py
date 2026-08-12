"""Shared persistence logic for pipeline results.

Extracted from ``cli/commands/init_cmd.py`` so both the CLI and the server
can persist a ``PipelineResult`` without duplicating the upsert recipe.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import structlog

from repowise.core.generation.models import (
    STRUCTURALLY_KEYED_PAGE_TYPES,
    STUB_FALLBACK_ERROR,
)

logger = structlog.get_logger(__name__)

# A second, stdlib logger for the one message here that has to survive
# ``repowise init``. ``configure_cli_logging`` pins both ``repowise.core`` and
# structlog to ERROR for the progress-bar commands, so anything below ERROR is
# discarded in the exact command that writes the index. The refusal below is
# logged through this at ERROR for that reason; the routine counts stay on
# structlog with their siblings.
_log = logging.getLogger(__name__)

# Max page ids per UPDATE ... IN (...) so a large cascade cannot exceed
# SQLite's bound-variable limit on the local CLI store.
_STALE_ID_CHUNK = 500


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
) -> list[str]:
    """Mark file pages for deleted/renamed files as tombstones.

    A ``freshness_status="fresh"`` page for a file that no longer exists is
    an active trap: retrieval serves it, agents cite it, and the index-age
    metadata says nothing is wrong. Tombstoned pages keep their content
    (the prose may still orient a reader) but carry status=tombstone and
    ``successor_paths`` in metadata so serving layers can skip or redirect.

    Returns the page ids marked, so the caller can drop them from the
    full-text index once its session has closed. Every serving layer already
    discards a tombstone, but retrieval fetches a fixed number of rows before
    any of that runs, so a tombstone still takes one of those slots and pushes
    a real candidate out of the fetch. The row has to go, and only the caller
    knows when it is safe to write to the index — on SQLite it shares a file
    with this session.
    """
    if not candidates:
        return []
    from sqlalchemy import select

    from repowise.core.persistence.models import Page

    page_ids = {f"file_page:{path}": (path, successors) for path, successors in candidates}
    res = await session.execute(
        select(Page).where(Page.repository_id == repo_id, Page.id.in_(page_ids))
    )
    marked: list[str] = []
    for page in res.scalars().all():
        _, successors = page_ids[page.id]
        page.freshness_status = "tombstone"
        try:
            meta = json.loads(page.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        meta["successor_paths"] = successors
        page.metadata_json = json.dumps(meta)
        marked.append(page.id)
    if marked:
        logger.info("pages_tombstoned", repo_id=repo_id, count=len(marked))
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


async def tombstone_absent_file_pages(
    session: Any, repo_id: str, repo_path: Path | str
) -> list[str]:
    """Tombstone every file page whose file is no longer on disk.

    :func:`mark_tombstone_pages` works from a diff, so it only ever sees a
    file that was deleted or renamed *between two commits this run compared*.
    ``repowise init`` compares nothing — it indexes a checkout as it stands —
    so it has never tombstoned anything, and a page written before a file was
    deleted keeps ``freshness_status='fresh'`` through every later index.

    That page is the trap the tombstone exists to close: retrieval serves it,
    agents cite it, and the index-age metadata says nothing is wrong.

    The check is a plain existence test against the checkout rather than a
    comparison with what this run parsed. A file can be present but unparsed —
    an unsupported extension, a parse failure, a changed exclude list — and
    such a file is stale, not deleted. Calling it deleted would put a
    ``successor_paths: []`` on a page whose file a reader can still open.

    Returns the page ids marked, so the caller can drop them from the
    full-text index once its session has closed, on the same terms as
    :func:`mark_tombstone_pages`.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import Page

    root = Path(repo_path)
    res = await session.execute(
        select(Page).where(
            Page.repository_id == repo_id,
            Page.page_type == "file_page",
            Page.freshness_status != "tombstone",
        )
    )
    live = list(res.scalars().all())
    if not live:
        return []

    absent = [p for p in live if not (root / (p.target_path or "")).exists()]
    if not absent:
        return []

    # Every page absent means the paths are not being resolved against the
    # tree they were written from — a wrong root, a bare-repo checkout, a
    # working copy that has not been restored yet. Tombstoning the whole wiki
    # on that reading is worse than leaving it, and unlike a partial mistake
    # it is not recoverable by re-indexing a file. Refuse, loudly, and let the
    # rest of the run proceed.
    if len(absent) == len(live):
        _log.error(
            "tombstone_sweep_refused repo_id=%s file_pages=%d root=%s: every file "
            "page's path is missing from the checkout, which reads as a wrong "
            "root rather than a deleted repository. Nothing was tombstoned.",
            repo_id,
            len(live),
            root,
        )
        return []

    marked: list[str] = []
    for page in absent:
        page.freshness_status = "tombstone"
        try:
            meta = json.loads(page.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        # Deleted, not renamed: rename detection is the diff-driven sweep's
        # job and it has evidence this one does not.
        meta["successor_paths"] = []
        page.metadata_json = json.dumps(meta)
        marked.append(page.id)

    logger.info(
        "file_pages_tombstoned_absent",
        repo_id=repo_id,
        count=len(marked),
        sample=[p.target_path for p in absent[:3]],
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


async def mark_page_ids_stale(session: Any, repo_id: str, page_ids: Iterable[str]) -> int:
    """Decay arbitrary pages to ``freshness_status='stale'`` by full page id.

    The sibling of :func:`mark_stale_pages`, which only understands
    ``file_page:<path>`` ids. Scoped generation's cascade needs to mark
    dependents of every type — module, SCC, layer and the repo-wide overview /
    architecture / onboarding pages — stale when the run regenerated a file but
    not its summaries. Same ``fresh``-only downgrade so a tombstoned or
    already-stale page keeps its stronger status.

    Returns the number of pages marked.
    """
    ids = list(dict.fromkeys(page_ids))  # dedupe, preserve order
    if not ids:
        return 0
    from sqlalchemy import update

    from repowise.core.persistence.models import Page

    # Chunk the IN list: SQLite (the local CLI store) binds one variable per id
    # and caps at SQLITE_MAX_VARIABLE_NUMBER, so a large cascade could exceed it.
    marked = 0
    for i in range(0, len(ids), _STALE_ID_CHUNK):
        batch = ids[i : i + _STALE_ID_CHUNK]
        res = await session.execute(
            update(Page)
            .where(
                Page.repository_id == repo_id,
                Page.id.in_(batch),
                Page.freshness_status == "fresh",
            )
            .values(freshness_status="stale")
        )
        marked += int(res.rowcount or 0)
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


def _changed_file_edges(
    graph_builder: Any,
    parsed_files: list[Any] | None,
    changed_paths: list[str],
) -> tuple[list[str], list[dict]]:
    """``(reconcile_paths, edges)`` for edges emanating from changed+parsed files.

    An edge is attributed to the file that owns its *source* node: a file node
    is the path itself; a symbol node carries ``file_path``. Restricting to
    files that both changed and parsed this run mirrors ``_changed_file_symbols``
    — a changed file that failed to parse keeps its existing edges rather than
    having them wrongly wiped on a transient failure.
    """
    changed = set(changed_paths or [])
    parsed = {pf.file_info.path for pf in parsed_files or []}
    reconcile = changed & parsed
    if not reconcile:
        return [], []

    graph = graph_builder.graph()
    owner: dict[str, str | None] = {}
    for node_id in graph.nodes:
        data = graph.nodes[node_id]
        owner[node_id] = (
            node_id if data.get("node_type", "file") == "file" else data.get("file_path")
        )

    edges: list[dict] = []
    for u, v, data in graph.edges(data=True):
        if owner.get(u) not in reconcile:
            continue
        edges.append(
            {
                "source_node_id": u,
                "target_node_id": v,
                "imported_names_json": json.dumps(data.get("imported_names", [])),
                "edge_type": data.get("edge_type", "imports"),
                "confidence": data.get("confidence", 1.0),
                "hint_source": data.get("hint_source"),
            }
        )
    return sorted(reconcile), edges


async def _edges_predate_cohesion(session: Any, repo_id: str, graph_builder: Any) -> bool:
    """True when ``graph_edges`` was written before edge cohesion was recorded.

    An incremental update rewrites only the changed files' edges, so a store
    indexed by an older build keeps that build's edges — and its resolution
    mistakes — on every file that has not happened to change since. The health
    engine and the server both read a graph rehydrated from those rows, so they
    would go on reporting cycles the current engine no longer finds.

    The signal is the store's own content rather than a version marker: a
    routine update deliberately clamps ``store_format_version`` below the first
    reindex gate, so a version comparison would refire on every run. Here, a
    repo whose graph now has cohesion edges but whose table has no
    ``hint_source`` anywhere is exactly a pre-cohesion store. Rewriting it
    stamps those rows, so this answers False from then on and the full
    reconcile happens once.
    """
    from sqlalchemy import select

    from repowise.core.ingestion.cohesion import is_cohesion_edge
    from repowise.core.persistence.models import GraphEdge

    try:
        graph = graph_builder.graph()
    except Exception:
        return False
    if not any(is_cohesion_edge(d) for _u, _v, d in graph.edges(data=True)):
        return False  # nothing to record, so nothing can be missing

    row = (
        await session.execute(
            select(GraphEdge.id)
            .where(GraphEdge.repository_id == repo_id, GraphEdge.hint_source.is_not(None))
            .limit(1)
        )
    ).first()
    return row is None


async def persist_incremental_edges(
    session: Any,
    repo_id: str,
    graph_builder: Any,
    parsed_files: list[Any] | None,
    changed_paths: list[str],
) -> None:
    """Refresh ``graph_edges`` for changed files on an incremental update.

    Sibling of :func:`persist_incremental_symbols`. The full-init path was the
    only one that ever wrote ``graph_edges``; ``repowise update`` rebuilt the
    graph but never repersisted edges, so adjacency froze at the last full
    index. Phase E flow-path answers and any graph expansion read adjacency
    straight from this table, so they decayed on every incremental update. This
    delete-then-inserts the changed files' outgoing edges (dropping edges those
    files no longer have). Scoped to the changed set for cost.
    """
    if graph_builder is None or not parsed_files:
        return
    from repowise.core.persistence.crud import reconcile_edges_for_files

    if await _edges_predate_cohesion(session, repo_id, graph_builder):
        # Widen the reconcile to the whole parsed set, once. Same code path,
        # so the delete-then-insert still drops edges a file no longer has —
        # which is what clears the pre-fix resolution mistakes, an upsert
        # alone would leave them behind.
        changed_paths = [pf.file_info.path for pf in parsed_files]
        logger.info("graph_edges_cohesion_backfill", repo_id=repo_id, files=len(changed_paths))

    reconcile_paths, edges = _changed_file_edges(graph_builder, parsed_files, changed_paths)
    if not reconcile_paths:
        return
    await reconcile_edges_for_files(session, repo_id, reconcile_paths, edges)


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
    wiping rows on a broken run.

    Full runs only. The authority here is "absent from this run's output",
    which is only safe because a full run rebuilds every table it prunes from
    in the same transaction. Incremental runs use
    :func:`prune_deleted_file_rows`, whose authority is the filesystem and git
    rather than the parse — see its docstring for why the two differ.
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


# A prune that would take more than this share of a table is read as a broken
# run rather than a large commit, and refused. The asymmetry is the whole
# argument: refusing leaves stale rows, which are visible in the UI and cleared
# by a reindex, where proceeding deletes live ones, which are invisible until
# someone notices the symbol is missing.
_PRUNE_MAX_FRACTION = 0.5
# ...but a small repo can legitimately lose half its files in one commit, so the
# fraction only applies once the *deletion* is big enough for the ratio to mean
# something. Measured on the deletion rather than the table so that a 25-row
# store losing 20 rows still prunes: at that size "most of it went" is an
# ordinary commit, not evidence of a broken run.
_PRUNE_FLOOR_MIN_ROWS = 20


def _git_tracked_paths(root: Path) -> frozenset[str]:
    """Every path git tracks at HEAD, POSIX-relative, or an empty set on failure.

    Empty is indistinguishable from "git failed" on purpose: both mean this
    witness has nothing to say, and the caller treats silence as "no opinion"
    rather than as "nothing is tracked".
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            timeout=60,
            check=True,
        ).stdout
    except Exception:
        return frozenset()
    return frozenset(p for p in out.decode("utf-8", "replace").split("\0") if p)


class _FileLiveness:
    """Is this path still a real file, judged without consulting this run's parser.

    Two witnesses, neither of which is the parse: the file is present on disk,
    or git still tracks it. A path has to fail both to count as deleted.

    That split is the point. Deriving deletions from ``parsed_files`` alone
    makes every transient read or parse failure look like a deletion, and on
    Windows a file lock or an antivirus scan produces exactly that: the file is
    there, the run could not read it, and the naive prune deletes its rows.
    The disk check answers for that case; git covers the narrower one where the
    stat itself fails (``Path.exists()`` reports False on a permission error).

    The deliberate cost of taking the union: a file that a config change has
    newly *excluded* still exists and is still tracked, so its rows survive an
    incremental update. That matches the behaviour before this guard existed
    (which pruned nothing at all on this path), and a full reindex still clears
    it, so nothing regresses.

    ``git ls-files`` is spawned lazily, on the first path that is not on disk,
    so an update with no deletions never pays for it.
    """

    def __init__(self, repo_path: Any) -> None:
        self._root = Path(repo_path)
        self._tracked: frozenset[str] | None = None
        # The same path is asked about once per table it has rows in, and a
        # stat under an antivirus scanner is not free.
        self._memo: dict[str, bool] = {}

    def is_live(self, path: str) -> bool:
        answer = self._memo.get(path)
        if answer is None:
            answer = self._is_live(path)
            self._memo[path] = answer
        return answer

    def _is_live(self, path: str) -> bool:
        if (self._root / path).exists():
            return True
        if self._tracked is None:
            self._tracked = _git_tracked_paths(self._root)
        return path in self._tracked


async def prune_deleted_file_rows(
    session: Any,
    repo_id: str,
    repo_path: Any,
    *,
    live_hint: set[str] | None = None,
) -> tuple[int, list[str]]:
    """Delete file-scoped rows for files that are gone, on an incremental update.

    The incremental path never pruned anything: deleting a file tombstoned its
    wiki page and left its graph nodes, edges, metrics, symbols, health rows and
    git metadata behind, so MCP, search and every health aggregate kept counting
    a file that no longer exists until someone ran a full reindex.

    The authority is deliberately *not* the one :func:`_prune_stale_file_rows`
    uses. A full run rebuilds every table it prunes from, so "absent from this
    run's output" is a safe question there. An incremental run rebuilds nothing:
    a row deleted here is not rewritten, so the question has to be "is the file
    gone", answered by :class:`_FileLiveness` from the filesystem and git rather
    than from the parse. They differ exactly in the transient-failure case, and
    that case is the one that loses data.

    *live_hint* is what this run already knows to be live: the paths that
    parsed, plus every file node the rebuilt graph holds. The first half is
    purely an optimisation, since anything in it would pass the liveness test
    anyway and the stat is skipped. The second half is load-bearing: a node
    like ``external:...`` or Spring's ``META-INF/services/<iface>`` names no
    file, so it fails every liveness test there is, and only the fact that the
    graph build just re-minted it says it is not a deletion.

    Returns ``(deleted_path_count, refusals)``, where a refusal is a one-line
    explanation of a floor guard that fired, for the caller's degraded report.
    """
    from sqlalchemy import delete, or_, select

    # Not every ``node_type == "file"`` row names a file. ``external:`` and
    # ``framework:`` nodes are minted for third-party imports and for
    # convention-based loading, they are stored as file nodes, and they answer
    # "no" to every liveness question there is because no such file was ever
    # meant to exist. Without this they read as a mass deletion: 223 of hugo's
    # 2,356 file nodes, 590 of react's 3,353, and 70% of spring-petclinic's,
    # which is past the floor guard and would have been reported as a refusal
    # rather than as the bug it is.
    #
    # Imported rather than re-listed: a prefix added to one copy and not the
    # other is exactly the bug above. Costs nothing on this path, where the
    # partial dead-code analysis has already loaded the module (13.7 ms when
    # it has not, measured cold after the CLI modules are up).
    from repowise.core.analysis.dead_code.analyzer import _is_synthetic_node
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

    hint = live_hint or set()
    liveness = _FileLiveness(repo_path)
    refusals: list[str] = []
    deleted: set[str] = set()

    def _dead(persisted: set[str], label: str) -> list[str]:
        """Paths in *persisted* that no longer exist, or [] if the floor fired."""
        persisted = {p for p in persisted if not _is_synthetic_node(p)}
        dead = [p for p in persisted if p not in hint and not liveness.is_live(p)]
        if (
            len(dead) > _PRUNE_FLOOR_MIN_ROWS
            and persisted
            and len(dead) > _PRUNE_MAX_FRACTION * len(persisted)
        ):
            refusals.append(
                f"Deleted-file prune refused for {label}: {len(dead)} of "
                f"{len(persisted)} paths looked deleted, which reads as a broken "
                "run rather than a commit. Run a full reindex to clear them."
            )
            return []
        deleted.update(dead)
        return dead

    async def _prune_table(model: Any, column: Any, label: str) -> None:
        persisted = set(
            (await session.execute(select(column).where(model.repository_id == repo_id).distinct()))
            .scalars()
            .all()
        )
        persisted.discard(None)
        dead = _dead(persisted, label)
        for i in range(0, len(dead), _PRUNE_CHUNK):
            await session.execute(
                delete(model).where(
                    model.repository_id == repo_id,
                    column.in_(dead[i : i + _PRUNE_CHUNK]),
                )
            )

    # ---- Graph nodes + edges -------------------------------------------------
    # File nodes key on node_id, symbol nodes on file_path, and edges have no FK
    # cascade from either, so edges go first.
    node_rows = (
        await session.execute(
            select(GraphNode.node_id, GraphNode.node_type, GraphNode.file_path).where(
                GraphNode.repository_id == repo_id
            )
        )
    ).all()
    node_paths = {
        (node_id if node_type == "file" else file_path)
        for node_id, node_type, file_path in node_rows
        if node_type == "file" or file_path
    }
    dead_paths = set(_dead(node_paths, "graph_nodes"))
    if dead_paths:
        stale_node_ids = [
            node_id
            for node_id, node_type, file_path in node_rows
            if (node_id if node_type == "file" else file_path) in dead_paths
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

    await _prune_table(GraphMetric, GraphMetric.node_id, "graph_metrics")
    await _prune_table(WikiSymbol, WikiSymbol.file_path, "wiki_symbols")
    await _prune_table(SecurityFinding, SecurityFinding.file_path, "security_findings")
    await _prune_table(DeadCodeFinding, DeadCodeFinding.file_path, "dead_code_findings")
    await _prune_table(HealthFileMetric, HealthFileMetric.file_path, "health_file_metrics")
    await _prune_table(HealthFinding, HealthFinding.file_path, "health_findings")
    # git_metadata is keyed off the git indexer on a full run, but an
    # incremental run only indexes the changed files, so the same liveness
    # question is the only authority available here too.
    await _prune_table(GitMetadata, GitMetadata.file_path, "git_metadata")

    return len(deleted), refusals


# Generated page types keyed on run-scoped structure: module/scc pages on
# clustering ordinals, layer pages on display names. Those keys shift between
# runs, so re-runs mint fresh page ids and the previous rows linger as
# duplicates unless swept against the current run's output.
#
# Same list generation stamps ``structural_key`` from, imported rather than
# repeated: a type present in one and missing from the other is exactly the
# bug the sweep exists to prevent.
_SWEPT_GENERATED_PAGE_TYPES = STRUCTURALLY_KEYED_PAGE_TYPES


async def sweep_retired_pages(session: Any, repo_id: str) -> list[str]:
    """Delete every row of a page that no longer exists, by type or by id.

    Distinct from the two sweeps below, and simpler than either: those ask what
    *this* run produced, because a page of a live type may legitimately be
    absent from a scoped run. A retired page has no legitimate rows at all —
    nothing emits one and no failure mode can make anything emit one — so the
    question never arises and this is safe on every path, full or scoped.

    The retirement tables are the source of truth rather than a list repeated
    here. A page is retired exactly when a reader following its id gets sent
    somewhere else, and that is what those tables say; keeping a second list
    would let a page be redirected without being swept, which is the state this
    function was written to clear.

    That state is the reason this exists. The architecture diagram merged into
    the overview and layer pages became grouping rows in the tree, both with
    redirects registered — but ``architecture_diagram`` is not structurally
    keyed, so no sweep ever covered it, and ``layer_page`` was only swept by a
    full run. An index that has only been updated incrementally since therefore
    still serves both. On this repository the retired diagram was the
    second-ranked retrieval hit for "how does the ingestion pipeline work",
    competing with the overview it had been merged into.

    Retirement by *id* is the second half of the same argument, and the reason
    this is no longer named for types. Three orientation slots retired out of
    the onboarding collection while five stayed, so their rows cannot be
    reached by page type: ``onboarding`` still has legitimate rows and always
    will. Sweeping the exact ids is the only way those leave a store, and the
    safety argument is unchanged — a retired id names a page nothing emits.

    Returns the swept page ids so the caller can drop them from the vector
    store and from FTS. A row deleted here but left in either of those is worse
    than one never swept: search still answers from the FTS copy, with the full
    title and snippet of a page the reader can no longer open.
    """
    from sqlalchemy import delete, or_, select

    from repowise.core.generation.page_redirects import (
        RETIRED_IDS,
        SUPERSEDED_TO_REPO_WIDE,
        SUPERSEDED_TYPES,
    )
    from repowise.core.persistence.models import Page, PageVersion

    retired_types = sorted(set(SUPERSEDED_TYPES) | set(SUPERSEDED_TO_REPO_WIDE))
    retired_ids = sorted(RETIRED_IDS)
    if not retired_types and not retired_ids:
        return []

    # Either rule can match, and a page matched by both is one row either way.
    match_clauses = []
    if retired_types:
        match_clauses.append(Page.page_type.in_(retired_types))
    if retired_ids:
        match_clauses.append(Page.id.in_(retired_ids))

    stale = (
        (
            await session.execute(
                select(Page.id).where(
                    Page.repository_id == repo_id, or_(*match_clauses)
                )
            )
        )
        .scalars()
        .all()
    )
    for i in range(0, len(stale), _PRUNE_CHUNK):
        batch = stale[i : i + _PRUNE_CHUNK]
        await session.execute(delete(PageVersion).where(PageVersion.page_id.in_(batch)))
        await session.execute(
            delete(Page).where(Page.repository_id == repo_id, Page.id.in_(batch))
        )
    if stale:
        logger.info(
            "retired_pages_swept",
            repo_id=repo_id,
            count=len(stale),
            types=retired_types,
            ids=retired_ids,
        )
    return list(stale)


async def _sweep_stale_generated_pages(
    session: Any,
    repo_id: str,
    generated_pages: list[Any] | None,
    authoritative_page_types: set[str] | None = None,
    preserved_page_ids: set[str] | None = None,
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

    ``preserved_page_ids`` are ids a ``--resume`` run skipped *because they
    already exist* (see ``_GenerationRun._emit``). They are absent from
    ``generated_pages`` by design, which is exactly what "stale" means to the
    rest of this function, so without this argument a resume that regenerated
    the missing half of a page type deleted the half it had just protected
    (issue #1089). A preserved id is as current as a produced one: the id did
    not drift, which is the only thing this sweep exists to catch.
    """
    from sqlalchemy import delete, select

    from repowise.core.persistence.models import Page, PageVersion

    produced: dict[str, set[str]] = {}
    for page in generated_pages or []:
        produced.setdefault(page.page_type, set()).add(page.page_id)
    authoritative = authoritative_page_types or set()
    preserved = preserved_page_ids or set()

    swept: list[str] = []
    for page_type in _SWEPT_GENERATED_PAGE_TYPES:
        current = produced.get(page_type)
        if not current and page_type not in authoritative:
            continue
        # Preserved ids join the current set rather than being subtracted from
        # the stale one: an authoritative type sweeps even when the run emitted
        # none of it, and that branch must still keep what a resume stood on.
        # (Defensive today — no caller sets both — but the two are independent
        # inputs and the failure mode if they ever meet is a deleted wiki.)
        # Filtered by type because ``preserved`` spans every page type while
        # this loop compares against one. Ids happen to be ``type:target`` so
        # a cross-type match is impossible, but the sweep is the wrong place to
        # depend on an id format nothing here enforces.
        prefix = f"{page_type}:"
        current = (current or set()) | {p for p in preserved if p.startswith(prefix)}
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


async def sweep_absent_cycle_pages(session: Any, repo_id: str, graph_builder: Any) -> list[str]:
    """Delete ``scc_page`` rows whose cycle no longer exists in the graph.

    The other sweeps ask "did this run *produce* this page?", which a scoped run
    cannot answer: ``repowise update`` runs with ``file_pages_only`` and never
    reaches level 3, so it emits no ``scc_page`` at all and every prior row
    looks stale to that question. ``_sweep_stale_generated_pages`` therefore
    gates on the run declaring itself authoritative, and only a keyless
    (``deterministic``) run does.

    Between them those two rules leave a cycle page immortal on the paths that
    matter: an update never regenerates it, and a keyed full re-index of a repo
    whose cycles all disappeared never claims authority to retire it. A user
    upgrading into a build that fixes a cycle-detection bug keeps being served
    the cycles it fixed.

    This asks a different question — "does the graph still contain this cycle?"
    — which the rebuilt graph answers directly and identically on every path,
    with no dependence on what generation chose to emit or what budget it had.
    A cycle's page id is a hash of its sorted members
    (:func:`~repowise.core.generation.models.scc_page_slug`), so the current
    cycle set names exactly the ids that may survive.

    Returns the deleted page ids so the caller can drop them from FTS and the
    vector store after the session closes.
    """
    from sqlalchemy import delete, select

    from repowise.core.generation.models import compute_page_id, scc_page_slug
    from repowise.core.persistence.models import Page, PageVersion

    if graph_builder is None:
        return []
    try:
        sccs = graph_builder.strongly_connected_components()
    except Exception:  # a released graph has no cycles to speak for
        return []

    valid = {
        compute_page_id("scc_page", scc_page_slug(sorted(scc))) for scc in sccs if len(scc) > 1
    }
    existing = (
        (
            await session.execute(
                select(Page.id).where(
                    Page.repository_id == repo_id, Page.page_type == "scc_page"
                )
            )
        )
        .scalars()
        .all()
    )
    stale = [pid for pid in existing if pid not in valid]
    for i in range(0, len(stale), _PRUNE_CHUNK):
        batch = stale[i : i + _PRUNE_CHUNK]
        await session.execute(delete(PageVersion).where(PageVersion.page_id.in_(batch)))
        await session.execute(
            delete(Page).where(Page.repository_id == repo_id, Page.id.in_(batch))
        )
    if stale:
        logger.info("absent_cycle_pages_swept", repo_id=repo_id, count=len(stale))
    return stale


async def sweep_superseded_generated_pages(
    session: Any,
    repo_id: str,
    generated_pages: list[Any] | None,
) -> list[str]:
    """Retire structurally-keyed rows whose coverage a scoped run just took over.

    :func:`_sweep_stale_generated_pages` cannot be used on the update path. It
    deletes every row of a page type the run did not reproduce, which is right
    for a full index and catastrophic for a scoped one: regenerating a single
    page would wipe the other fifty.

    That left a real gap. A structurally-keyed page's id is its
    ``target_path``, and a page that groups files can legitimately change which
    directory names it when its membership changes. ``repowise update`` goes
    through ``scoped_generation`` and never swept, so the regenerated page was
    written under its new id and the old row stayed behind as a duplicate until
    somebody ran a full index. Harmless while module and cycle pages were never
    regenerated incrementally, and not harmless once concept pages are.

    The rule here is narrower and does not need to know what the caller asked
    for: a prior row is superseded when **every file it covered is now covered
    by pages this run produced**, of the same type. That is exactly the
    condition under which the old row documents nothing that is not documented
    elsewhere. A page that failed to generate keeps files no produced page
    claims, so it survives — which matters, because deleting a page because its
    generation errored would be a worse bug than the one this fixes.

    Returns the swept page ids so the caller can drop them from FTS after the
    session closes.
    """
    from sqlalchemy import delete, select

    from repowise.core.persistence.models import Page, PageVersion

    produced_ids: dict[str, set[str]] = {}
    covered: dict[str, set[str]] = {}
    for page in generated_pages or []:
        page_type = page.page_type
        if page_type not in _SWEPT_GENERATED_PAGE_TYPES:
            continue
        # A stub standing in for a failed provider call is the "failed to
        # generate" case this function's contract already carves out: it claims
        # no files, so a prior row covering them is not superseded. Counting its
        # members would retire the very page whose regeneration just failed, and
        # take its version history with it.
        if STUB_FALLBACK_ERROR in (getattr(page, "metadata", None) or {}):
            continue
        produced_ids.setdefault(page_type, set()).add(page.page_id)
        metadata = getattr(page, "metadata", None) or {}
        members = metadata.get("file_paths") or metadata.get("files") or []
        covered.setdefault(page_type, set()).update(m for m in members if isinstance(m, str))

    swept: list[str] = []
    for page_type, current in produced_ids.items():
        union = covered.get(page_type) or set()
        if not union:
            # Nothing to compare against: a run that recorded no membership
            # cannot prove it superseded anything, so it retires nothing.
            continue
        rows = (
            await session.execute(
                select(Page.id, Page.metadata_json).where(
                    Page.repository_id == repo_id, Page.page_type == page_type
                )
            )
        ).all()
        stale: list[str] = []
        for page_id, raw in rows:
            if page_id in current:
                continue
            try:
                metadata = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (TypeError, ValueError):
                continue
            if not isinstance(metadata, dict):
                continue
            members = metadata.get("file_paths") or metadata.get("files") or []
            members = {m for m in members if isinstance(m, str)}
            # No recorded membership means no proof of supersession. Those rows
            # are the pre-membership ones (backlog B20) and they are left for a
            # full index to retire.
            if members and members <= union:
                stale.append(page_id)
        for i in range(0, len(stale), _PRUNE_CHUNK):
            batch = stale[i : i + _PRUNE_CHUNK]
            await session.execute(delete(PageVersion).where(PageVersion.page_id.in_(batch)))
            await session.execute(
                delete(Page).where(Page.repository_id == repo_id, Page.id.in_(batch))
            )
        swept.extend(stale)

    if swept:
        logger.info("superseded_generated_pages_swept", repo_id=repo_id, count=len(swept))
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
                "hint_source": data.get("hint_source"),
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
    # Best-effort — never breaks the rest of the phase.
    try:
        await persist_security_findings(result, session, repo_id)
    except Exception as _sec_err:
        logger.warning("security_scan_skipped", error=str(_sec_err))

    return len(all_symbols)


async def persist_security_findings(result: Any, session: Any, repo_id: str) -> None:
    """Scan every parsed file's source and replace its security findings.

    Source bytes come from ``result.source_map`` (ingestion read them once);
    ``FileInfo`` itself carries no content, only ``content_hash``, so reading
    it off ``file_info`` yields empty text and a scan that can never fire.
    Resume views without a ``source_map`` degrade to the symbol-name scan.
    """
    from repowise.core.analysis.security_scan import SecurityScanner

    scanner = SecurityScanner(session, repo_id)
    source_map = getattr(result, "source_map", None) or {}
    findings_by_file: dict[str, list[dict]] = {}
    scanned_paths: list[str] = []
    for pf in result.parsed_files:
        path = pf.file_info.path
        raw = source_map.get(path, b"")
        if isinstance(raw, (bytes, bytearray)):
            source_text = raw.decode("utf-8", errors="replace")
        else:
            source_text = raw or ""
        scanned_paths.append(path)
        findings = await scanner.scan_file(path, source_text, pf.symbols)
        if findings:
            findings_by_file[path] = findings
    await scanner.replace_findings(findings_by_file, scanned_paths)


async def persist_git(result: Any, session: Any, repo_id: str) -> None:
    """Persist git-phase outputs: per-file metadata and per-commit rows.

    Both writes are idempotent UPSERTs keyed by ``(repo_id, file_path)`` /
    ``(repo_id, sha)`` — safe to call incrementally and on resume.
    """
    from repowise.core.persistence.crud import (
        prune_fix_events_before,
        update_repo_git_totals,
        upsert_fix_events_bulk,
        upsert_git_commits_bulk,
        upsert_git_metadata_bulk,
    )

    if result.git_metadata_list:
        await upsert_git_metadata_bulk(session, repo_id, result.git_metadata_list)

    summary = getattr(result, "git_summary", None)

    # Per-commit rows + change-risk ride on the git summary.
    commit_rows = getattr(summary, "commit_rows", None)
    if commit_rows:
        await upsert_git_commits_bulk(session, repo_id, commit_rows)

    # Per fix-commit x file rows (with their SZZ candidates). The prune keeps a
    # re-index of an already-indexed repo from leaving behind events that have
    # since aged out of the defect window.
    fix_event_rows = getattr(summary, "fix_event_rows", None)
    if fix_event_rows:
        await upsert_fix_events_bulk(session, repo_id, fix_event_rows)
    oldest_ts = getattr(summary, "fix_oldest_ts", 0)
    if oldest_ts and getattr(summary, "fix_events_built", False):
        from datetime import UTC, datetime

        await prune_fix_events_before(session, repo_id, datetime.fromtimestamp(oldest_ts, tz=UTC))

    # Symbol attribution + bug-magnet rollups, over the rows just written.
    # Failure-isolated: a rollup that cannot be computed leaves the previous
    # values in place rather than blanking a surface mid-index.
    try:
        from repowise.core.pipeline.fix_rollups import apply_fix_rollups

        await apply_fix_rollups(session, repo_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("fix_rollups_failed", repo_id=repo_id, error=str(exc))

    # Whole-history totals (true age / commit / contributor counts) also ride on
    # the summary — stamp them on the Repository row so the stats page reads them
    # instead of deriving them from the bounded ``git_commits`` sample (#730).
    totals = getattr(summary, "repo_totals", None)
    if totals is not None:
        await update_repo_git_totals(
            session,
            repo_id,
            total_commit_count=totals.total_commit_count,
            first_commit_at=totals.first_commit_at,
            total_contributor_count=totals.total_contributor_count,
            first_commit_author=totals.first_commit_author,
            first_commit_subject=totals.first_commit_subject,
            total_lines_added=totals.total_lines_added,
            total_lines_deleted=totals.total_lines_deleted,
            churn_anchor_sha=getattr(totals, "churn_anchor_sha", None),
        )


async def persist_analysis(result: Any, session: Any, repo_id: str) -> None:
    """Persist analysis-phase outputs: dead code, health, decisions, governance.

    Dead-code and health writes are repo-wide DELETE-THEN-INSERT (so they
    converge on re-run but don't support partial-within-phase resume);
    decisions/governance are idempotent. Intended to run once the analysis
    phase has fully completed.
    """
    from repowise.core.analysis.health.trends import snapshot_file_maps
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
        # Hoisted out of the coverage branch below: the same sha now stamps the
        # metric rows, so a reader can tell how far the health pass lags the
        # index instead of assuming the two moved together.
        head_sha = getattr(result, "head_commit", None) or getattr(result, "commit_sha", None)
        await save_health_metrics(session, repo_id, hr.metrics or [], analyzed_commit=head_sha)
        if hr.findings:
            await save_health_findings(session, repo_id, hr.findings)
        # Resolved coverage rows, when a report was ingested this run.
        coverage_files = getattr(hr, "coverage_files", None)
        if coverage_files:
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
            scores_map, deductions_map = snapshot_file_maps(
                hr.metrics or [], hr.findings or []
            )
            await save_health_snapshot(
                session,
                repo_id,
                hotspot_health=float(kpis.get("hotspot_health", 10.0)),
                average_health=float(kpis.get("average_health", 10.0)),
                worst_performer_path=kpis.get("worst_performer_path"),
                worst_performer_score=kpis.get("worst_performer_score"),
                per_file_scores=scores_map,
                per_file_deductions=deductions_map,
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

    # Restore records the semantic supersession detector retired before it was
    # turned off. ``superseded`` is a protected status, so nothing else will
    # ever walk these back, and a store that only stops retiring is still a
    # store with a quarter of its corpus hidden. Runs whether or not this run
    # produced decisions.
    #
    # BEFORE the purge below, not after: restoring puts a row back at
    # ``proposed``, which is exactly what the purge deletes. Un-retire first and
    # a run ends in one consistent state — good records visible, retired-source
    # records gone. The other order leaves a restored changelog row alive for
    # one run and silently deletes it on the next.
    try:
        from repowise.core.persistence.crud import unretire_auto_superseded

        await unretire_auto_superseded(session)
    except Exception as _unretire_err:
        logger.debug("decision_unretire_skipped", error=str(_unretire_err))

    # One-shot drain of proposals left by retired extraction sources; without
    # this, DBs indexed before a removal keep a flooded review queue forever
    # (#751 for code_comment). Confirmed/dismissed rows are kept.
    try:
        from repowise.core.analysis.decision_provenance import RETIRED_SOURCES
        from repowise.core.persistence.crud import purge_proposed_decisions_by_source

        for _retired in RETIRED_SOURCES:
            await purge_proposed_decisions_by_source(session, repo_id, _retired)
    except Exception as _purge_err:
        logger.debug("decision_purge_skipped", error=str(_purge_err))

    # Re-stamp evidence rows left on a previous SOURCE_RANK ladder. Local stores
    # are created by ``init_db`` and never see Alembic, so the migration alone
    # would only reach hosted; this is the same repair on the path every store
    # takes. No-op scan once reconciled, and it runs whether or not this run
    # produced decisions, because a store with nothing new still holds the rows.
    try:
        from repowise.core.persistence.crud import reconcile_source_ranks

        await reconcile_source_ranks(session)
    except Exception as _rank_err:
        logger.debug("decision_rank_reconcile_skipped", error=str(_rank_err))

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
    from repowise.core.persistence import upsert_pages_from_generated

    # ---- Pages (if generated) -----------------------------------------------
    # Batched: one SELECT + one flush instead of a SELECT+flush per page. The
    # per-page durability sink already streamed these during generation; this
    # end-of-run pass flushes the post-generation metadata enrichment. See
    # upsert_pages_from_generated for the equivalence contract.
    if result.generated_pages:
        await upsert_pages_from_generated(session, result.generated_pages, repo_id)

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
        getattr(result, "preserved_page_ids", None),
    )
    # Rows of a page type that no longer exists at all. Independent of what
    # this run produced, so it runs on every path rather than only here.
    swept_page_ids += await sweep_retired_pages(session, repo_id)

    # Placement depends on the whole page set, so it is computed here rather
    # than during generation, after the sweep has retired anything stale.
    from .page_tree_sync import rebuild_page_tree

    await rebuild_page_tree(session, repo_id)

    logger.info(
        "pipeline_result_persisted",
        repo_id=repo_id,
        pages=len(result.generated_pages) if result.generated_pages else 0,
        graph_nodes=result.graph_builder.graph().number_of_nodes(),
        symbols=symbol_count,
        git_files=len(result.git_metadata_list),
    )
    return swept_page_ids
