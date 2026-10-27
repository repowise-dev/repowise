"""Graph node, symbol and edge writers for the full and incremental paths."""

from __future__ import annotations

import json
from typing import Any

import structlog

from repowise.core.entry_candidacy import is_reachability_root

from .prune import _PRUNE_CHUNK

logger = structlog.get_logger("repowise.core.pipeline.persist")


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


def _betweenness_commits(graph_builder: Any) -> dict[str, str | None]:
    """Per kind, the commit its betweenness was last exactly computed at.

    ``None`` means the builder cannot say, which is not "never scored" and must
    not be written as one: a builder rehydrated from SQL serves real values it
    did not compute, and stamping those NULL would report an entire indexed
    repository as unscored.
    """
    scoring = getattr(graph_builder, "betweenness_scoring", None)
    if scoring is None:
        return {"file": None, "symbol": None}
    try:
        return {k: getattr(scoring(k), "scored_commit", None) for k in ("file", "symbol")}
    except Exception:  # pragma: no cover - a builder without the accessor
        return {"file": None, "symbol": None}


def _stamp_betweenness_commit(
    node_dict: dict[str, Any],
    node_type: str,
    bc: dict[str, float],
    sym_bc: dict[str, float],
    bt_commits: dict[str, str | None],
) -> None:
    """Set ``betweenness_commit`` on a graph-node row when its scoring is known."""
    node_id = node_dict["node_id"]
    # Scored → its commit; in no scoring → NULL, the unscored marker;
    # provenance unknown → key omitted, so the stored stamp survives.
    if node_id in bc:
        kind, scored = "file", True
    elif node_id in sym_bc:
        kind, scored = "symbol", True
    else:
        kind, scored = ("file" if node_type == "file" else "symbol"), False
    if bt_commits[kind] is not None:
        node_dict["betweenness_commit"] = bt_commits[kind] if scored else None


def _community_meta(
    node_id: str,
    node_type: str,
    cd: dict[str, Any],
    sc: dict[str, Any],
    ci: dict[Any, Any],
    ep_scores: dict[str, float],
) -> dict[str, Any]:
    """The ``community_meta_json`` payload of one graph node."""
    community_meta: dict[str, Any] = {}
    if node_type == "file":
        cid = cd.get(node_id, 0)
        comm_info = ci.get(cid)
        if comm_info:
            community_meta = {
                "label": comm_info.label,
                "cohesion": comm_info.cohesion,
                "conductance": comm_info.conductance,
            }
    elif node_type == "symbol":
        sym_cid = sc.get(node_id)
        if sym_cid is not None:
            community_meta = {"symbol_community_id": sym_cid}
        if node_id in ep_scores:
            community_meta["entry_point_score"] = ep_scores[node_id]
    return community_meta


async def persist_graph_nodes(
    session: Any,
    repo_id: str,
    graph_builder: Any,
    ep_scores: dict[str, float] | None = None,
    *,
    timings: Any | None = None,
) -> None:
    """Persist file- and symbol-level graph nodes with full centrality metrics.

    Lifted out of :func:`persist_pipeline_result` so the incremental
    update path can refresh ``graph_nodes`` (including symbol-level
    PageRank / betweenness) without constructing a full ``PipelineResult``.
    """
    from repowise.core.analysis.communities import stored_community_id
    from repowise.core.persistence import (
        batch_upsert_graph_metrics,
        batch_upsert_graph_node_membership,
        batch_upsert_graph_nodes,
    )
    from repowise.core.pipeline.phase_timing import timed

    graph = graph_builder.graph()
    with timed(timings, "persist.graph_nodes.metrics"):
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
        with timed(timings, "persist.graph_nodes.entry_points"):
            ep_scores = _derive_entry_point_scores(graph_builder)

    bt_commits = _betweenness_commits(graph_builder)

    timings_rows = timed(timings, "persist.graph_nodes.rows")
    timings_rows.__enter__()
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
            "is_reachability_root": is_reachability_root(data),
            "always_run_reason": data.get("always_run_reason"),
            # Files draw from the file-level metric tables; symbols fall
            # back to the symbol subgraph (calls + heritage) so that the
            # per-symbol UI panel shows real centrality instead of 0.
            "pagerank": pr.get(node_id, sym_pr.get(node_id, 0.0)),
            "betweenness": bc.get(node_id, sym_bc.get(node_id, 0.0)),
            "community_id": stored_community_id(cd, node_id),
        }

        _stamp_betweenness_commit(node_dict, node_type, bc, sym_bc, bt_commits)

        community_meta = _community_meta(node_id, node_type, cd, sc, ci, ep_scores)
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
                    "public_api": data.get("public_api"),
                }
            )
        nodes.append(node_dict)
    timings_rows.__exit__(None, None, None)

    if nodes:
        with timed(timings, "persist.graph_nodes.upsert"):
            await batch_upsert_graph_nodes(session, repo_id, nodes)

    # Materialize the file-level metrics snapshot (graph_metrics) so large
    # repos can serve metric reads from SQL without recomputing the NetworkX
    # centrality kernels. Additive to graph_nodes; never changes node rows.
    try:
        with timed(timings, "persist.graph_nodes.snapshots"):
            await batch_upsert_graph_metrics(
                session, repo_id, graph_builder.file_metrics_snapshot()
            )
    except Exception as exc:  # materialization is non-load-bearing
        logger.warning("graph_metrics_materialize_skipped", error=str(exc))

    # Materialize file-level SCCs (import cycles) + symbol communities as
    # queryable rows (graph_node_membership). Feeds the break-cycle /
    # move-method refactoring surfaces; non-load-bearing like graph_metrics.
    try:
        with timed(timings, "persist.graph_nodes.snapshots"):
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
    from repowise.core.ingestion.parse_cache import parser_fingerprint
    from repowise.core.persistence.crud import reconcile_symbols_for_files

    # An extraction change can re-key symbols in files git did not touch (an
    # overload gaining its own id), so it widens to the whole parsed set on the
    # same check that widens edges. The stamp is written once, by
    # :func:`persist_incremental_edges`, which runs after this.
    if await _stored_edges_parser_fingerprint(session, repo_id) != parser_fingerprint():
        changed_paths = [pf.file_info.path for pf in parsed_files]
    reconcile_paths, symbols = _changed_file_symbols(parsed_files, changed_paths)
    if not reconcile_paths:
        return
    await reconcile_symbols_for_files(session, repo_id, reconcile_paths, symbols)


async def persist_symbol_analysis(
    session: Any,
    repo_id: str,
    parsed_files: list[Any] | None,
) -> None:
    """Reconcile symbol rows after analysis mutates their derived fields.

    Health analysis computes ``complexity_estimate`` on the in-memory symbols
    after the resumable INDEX checkpoint has already persisted the parser
    defaults.  Re-upsert just those symbol records before ANALYSIS is marked
    complete; graph construction, Git analysis, and the rest of ingestion stay
    untouched.  The upsert preserves each persisted row's database identity.
    """
    if not parsed_files:
        return
    from repowise.core.persistence.crud import batch_upsert_symbols

    symbols: list[Any] = []
    for pf in parsed_files:
        for sym in pf.symbols:
            if not getattr(sym, "file_path", None):
                sym.file_path = pf.file_info.path
            symbols.append(sym)
    if symbols:
        await batch_upsert_symbols(session, repo_id, symbols)


def _changed_file_edges(
    graph_builder: Any,
    parsed_files: list[Any] | None,
    changed_paths: list[str],
) -> tuple[list[str], list[dict]]:
    """``(reconcile_paths, edges)`` for edges emanating from changed+parsed files.

    An edge is attributed to the file that owns its *source* node: a file node
    is the path itself; a symbol node carries ``file_path``. An edge whose fact
    sits at its target (``TARGET_OWNED_HINTS``) belongs to the target's file
    as well, so editing the decorated file rewrites it. Restricting to
    files that both changed and parsed this run mirrors ``_changed_file_symbols``
    — a changed file that failed to parse keeps its existing edges rather than
    having them wrongly wiped on a transient failure.
    """
    from repowise.core.ingestion.models import TARGET_OWNED_HINTS

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
        if owner.get(u) in reconcile or (
            data.get("hint_source") in TARGET_OWNED_HINTS and owner.get(v) in reconcile
        ):
            edges.append(_edge_row(u, v, data))
    return sorted(reconcile), edges


def _edge_row(source: str, target: str, data: dict) -> dict:
    """One ``graph_edges`` payload from an in-memory edge; both writers use it."""
    return {
        "source_node_id": source,
        "target_node_id": target,
        "imported_names_json": json.dumps(data.get("imported_names", [])),
        "edge_type": data.get("edge_type", "imports"),
        "confidence": data.get("confidence", 1.0),
        "hint_source": data.get("hint_source"),
        "resolution_origin": data.get("resolution_origin"),
        "call_lines_json": json.dumps(data.get("call_lines", [])),
        "type_only": bool(data.get("type_only", False)),
        "deferred": bool(data.get("deferred", False)),
    }


async def prune_absent_graph_rows(
    session: Any,
    repo_id: str,
    node_ids: set[str],
    edge_rows: list[dict],
) -> tuple[int, int]:
    """Delete this repo's ``graph_edges`` / ``graph_nodes`` the fresh graph lacks.

    :func:`persist_graph_nodes` and :func:`batch_upsert_graph_edges` only
    upsert, so a full re-index over an existing store kept every row an earlier
    run wrote: an import removed from source stayed visible to MCP callers /
    dependents, flow paths and change health, and a superseded
    ``external:...`` target sat beside its resolved edge. Full runs only: the
    authority is "absent from this run's complete graph", the same rule as
    :func:`_prune_stale_file_rows`. Incremental updates use the file-scoped
    reconcilers instead and never call this.

    Runs in the caller's session, so the prune commits or rolls back together
    with the upsert it follows. Edges go before nodes (no FK cascade between
    the tables). An empty graph is treated as a broken run and prunes nothing,
    matching the rest of the full-run pruning.

    Returns ``(edges_deleted, nodes_deleted)``.
    """
    from sqlalchemy import delete, select

    from repowise.core.persistence.models import GraphEdge, GraphNode

    if not node_ids:
        return 0, 0

    fresh_edges = {
        (r["source_node_id"], r["target_node_id"], r.get("edge_type", "imports")) for r in edge_rows
    }

    # Diff in Python so each IN (...) is bounded by the stale set, not the whole
    # repo (SQLite host-parameter limit) — same approach as _prune_stale_file_rows.
    edge_rows_db = (
        await session.execute(
            select(
                GraphEdge.id,
                GraphEdge.source_node_id,
                GraphEdge.target_node_id,
                GraphEdge.edge_type,
            ).where(GraphEdge.repository_id == repo_id)
        )
    ).all()
    stale_edge_ids = [
        edge_id
        for edge_id, source, target, edge_type in edge_rows_db
        if (source, target, edge_type) not in fresh_edges
    ]
    for i in range(0, len(stale_edge_ids), _PRUNE_CHUNK):
        await session.execute(
            delete(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.id.in_(stale_edge_ids[i : i + _PRUNE_CHUNK]),
            )
        )

    node_rows_db = (
        await session.execute(
            select(GraphNode.id, GraphNode.node_id).where(GraphNode.repository_id == repo_id)
        )
    ).all()
    stale_node_ids = [row_id for row_id, node_id in node_rows_db if node_id not in node_ids]
    for i in range(0, len(stale_node_ids), _PRUNE_CHUNK):
        await session.execute(
            delete(GraphNode).where(
                GraphNode.repository_id == repo_id,
                GraphNode.id.in_(stale_node_ids[i : i + _PRUNE_CHUNK]),
            )
        )

    if stale_edge_ids or stale_node_ids:
        logger.info(
            "graph_rows_full_prune",
            repo_id=repo_id,
            edges=len(stale_edge_ids),
            nodes=len(stale_node_ids),
        )
    return len(stale_edge_ids), len(stale_node_ids)


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
    repo whose graph now has cohesion edges but whose table has no cohesion
    ``hint_source`` anywhere is exactly a pre-cohesion store. Rewriting it
    stamps those rows, so this answers False from then on and the full
    reconcile happens once.
    """
    from sqlalchemy import select

    from repowise.core.ingestion.cohesion import COHESION_HINTS, is_cohesion_edge
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
            .where(
                GraphEdge.repository_id == repo_id,
                # Cohesion hints only: other passes (pytest conftest) stamp a
                # hint too, and one of theirs is not a cohesion stamp.
                GraphEdge.hint_source.in_(COHESION_HINTS),
            )
            .limit(1)
        )
    ).first()
    return row is None


async def has_graph_edges(session: Any, repo_id: str) -> bool:
    """Whether this repo already has any ``graph_edges`` row."""
    from sqlalchemy import select

    from repowise.core.persistence.models import GraphEdge

    row = (
        await session.execute(
            select(GraphEdge.repository_id).where(GraphEdge.repository_id == repo_id).limit(1)
        )
    ).first()
    return row is not None


async def _stored_edges_parser_fingerprint(session: Any, repo_id: str) -> str | None:
    """The ``parser_fingerprint()`` recorded when this repo's edges were written.

    ``None`` on a store written before the column existed, which is what makes
    every already-shipped store take the widen once.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository

    return (
        await session.execute(
            select(Repository.graph_edges_parser_fingerprint).where(Repository.id == repo_id)
        )
    ).scalar_one_or_none()


async def stamp_edges_parser_fingerprint(session: Any, repo_id: str, fingerprint: str) -> None:
    """Record the parser build that just wrote this repo's ``graph_edges``.

    Written by :func:`persist_incremental_edges` after a widen, and by the full
    :func:`persist_ingestion` path after it has pruned the store down to the
    fresh graph (:func:`prune_absent_graph_rows`). Either way the table holds
    exactly the running build's edges, so it is parser-current.
    """
    from sqlalchemy import update

    from repowise.core.persistence.models import Repository

    await session.execute(
        update(Repository)
        .where(Repository.id == repo_id)
        .values(graph_edges_parser_fingerprint=fingerprint)
    )


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

    That scoping is also this function's one structural blind spot: an
    extraction change (a tree-sitter query edit, an extractor fix) alters the
    edges of *every* file, but only the git-changed ones get rewritten, so the
    rest keep the old build's edges until they happen to change. The parse cache
    self-invalidates on such a change (``parser_fingerprint`` hashes the ``.scm``
    sources) and the update rebuilds the whole graph in memory anyway, so the
    correct edges are already computed and only the write is too narrow. Widen
    it whenever the fingerprint that last wrote the table differs from the
    running one, then re-stamp. One comparison covers every future extraction
    change; :func:`_edges_predate_cohesion` is the bespoke predecessor, kept
    until shipped stores have all been stamped.
    """
    # An empty parse still counts: a repo with no source files has nothing to
    # widen, and it must be stamped, or a missing stamp re-parses it forever.
    if graph_builder is None or parsed_files is None:
        return
    from repowise.core.ingestion.parse_cache import parser_fingerprint
    from repowise.core.persistence.crud import reconcile_edges_for_files

    # Widen the reconcile to the whole parsed set, once. Same code path, so the
    # delete-then-insert still drops edges a file no longer has — which is what
    # clears the old build's resolution mistakes, an upsert alone would leave
    # them behind.
    fingerprint = parser_fingerprint()
    parser_changed = await _stored_edges_parser_fingerprint(session, repo_id) != fingerprint
    if parser_changed:
        changed_paths = [pf.file_info.path for pf in parsed_files]
        logger.info("graph_edges_parser_backfill", repo_id=repo_id, files=len(changed_paths))
    elif await _edges_predate_cohesion(session, repo_id, graph_builder):
        changed_paths = [pf.file_info.path for pf in parsed_files]
        logger.info("graph_edges_cohesion_backfill", repo_id=repo_id, files=len(changed_paths))

    reconcile_paths, edges = _changed_file_edges(graph_builder, parsed_files, changed_paths)
    if reconcile_paths:
        await reconcile_edges_for_files(session, repo_id, reconcile_paths, edges)
        # Every reconciled file, not only a widen's: a symbol renamed or removed
        # in a changed file otherwise keeps its node beside the new one.
        await _prune_vanished_symbol_nodes(session, repo_id, graph_builder, reconcile_paths)
    # Only after the widen actually ran to completion. Raising here (or in any
    # caller before the commit) leaves the old stamp, so the next update retries
    # rather than stranding the files it never reached.
    if parser_changed:
        await stamp_edges_parser_fingerprint(session, repo_id, fingerprint)


async def _prune_vanished_symbol_nodes(
    session: Any, repo_id: str, graph_builder: Any, paths: list[str]
) -> None:
    """Delete symbol ``graph_nodes`` of *paths* a re-extraction no longer produces.

    ``persist_graph_nodes`` upserts the current graph and never deletes, so a
    symbol whose id changed (a rename, or an overload gaining its own id under
    a new parser) would otherwise keep its old row beside the new ones. Scoped
    to files this run reconciled; a file that is gone is
    :func:`_prune_stale_file_rows`'s job.
    Membership rows prune themselves on their next write, and ``graph_metrics``
    holds files only.
    """
    from sqlalchemy import delete, or_, select

    from repowise.core.persistence.models import GraphEdge, GraphNode

    graph = graph_builder.graph()
    stale: list[str] = []
    for i in range(0, len(paths), _PRUNE_CHUNK):
        rows = await session.execute(
            select(GraphNode.node_id).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_type != "file",
                GraphNode.file_path.in_(paths[i : i + _PRUNE_CHUNK]),
            )
        )
        stale.extend(node_id for node_id in rows.scalars() if node_id not in graph)
    for i in range(0, len(stale), _PRUNE_CHUNK):
        batch = stale[i : i + _PRUNE_CHUNK]
        await session.execute(
            delete(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
                or_(GraphEdge.source_node_id.in_(batch), GraphEdge.target_node_id.in_(batch)),
            )
        )
        await session.execute(
            delete(GraphNode).where(
                GraphNode.repository_id == repo_id, GraphNode.node_id.in_(batch)
            )
        )
    if stale:
        logger.info("graph_nodes_parser_prune", repo_id=repo_id, nodes=len(stale))
