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


async def stored_symbol_complexity(session: Any, repo_id: str) -> dict[str, int]:
    """``{symbol_id: complexity_estimate}`` as the last health run stored it, zeros left out."""
    from sqlalchemy import select

    from repowise.core.persistence.models import WikiSymbol

    rows = await session.execute(
        select(WikiSymbol.symbol_id, WikiSymbol.complexity_estimate).where(
            WikiSymbol.repository_id == repo_id
        )
    )
    return {sid: int(value) for sid, value in rows if value}


def apply_stored_complexity(
    parsed_files: list[Any] | None, stored: dict[str, int], skip_paths: set[str] | frozenset = frozenset()
) -> None:
    """Set each symbol's ``complexity_estimate`` from *stored*, outside *skip_paths*.

    A parse leaves every symbol at the parser default and only health analysis
    measures complexity. An update analyses the changed files alone, so they go
    in *skip_paths* and keep the values this run measured.
    """
    for pf in parsed_files or []:
        if pf.file_info.path in skip_paths:
            continue
        for sym in pf.symbols:
            sid = getattr(sym, "id", None) or f"{pf.file_info.path}::{sym.name}"
            if value := stored.get(sid):
                sym.complexity_estimate = value


async def restore_stored_complexity(
    session: Any, repo_id: str, parsed_files: list[Any] | None, changed_paths: list[str]
) -> None:
    """Give the unchanged files' symbols the complexity their last health run measured.

    Left at the parser default, the update's parse ranks spotlights unlike
    init (the deselection sweep retires pages init keeps), and a symbol write
    widened to every file by a parser change stores the default over every
    measured value.
    """
    if parsed_files:
        stored = await stored_symbol_complexity(session, repo_id)
        apply_stored_complexity(parsed_files, stored, set(changed_paths or ()))


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
    owned = [
        node for node, path in _edge_owners(graph.nodes(data=True)).items() if path in reconcile
    ]
    owned_set = set(owned)
    # Out-edges of the owned nodes, plus target-owned edges into them from
    # elsewhere: the same rows as a scan of every edge, at the cost of the
    # files' own adjacency.
    edges = [_edge_row(u, v, data) for u, v, data in graph.out_edges(owned, data=True)]
    edges += [
        _edge_row(u, v, data)
        for u, v, data in graph.in_edges(owned, data=True)
        if u not in owned_set and data.get("hint_source") in TARGET_OWNED_HINTS
    ]
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
    """Make ``graph_edges`` match the rebuilt graph on an incremental update.

    Sibling of :func:`persist_incremental_symbols`. The full-init path was the
    only one that ever wrote ``graph_edges``; ``repowise update`` rebuilt the
    graph but never repersisted edges, so adjacency froze at the last full
    index. Phase E flow-path answers and any graph expansion read adjacency
    straight from this table, so they decayed on every incremental update.

    Files outside *changed_paths* are reconciled too when their edges moved.
    A file git did not touch still gains or loses edges when what it points at
    changes: a call resolves to a symbol a new file defines, or stops resolving
    to one that moved. Those files are found without reading the whole table
    (:func:`_drifted_files`), so the store work follows the change rather than
    the repository. An extraction change (``parser_fingerprint``) alters every
    file's edges at once, so it reconciles every parsed file, once.

    The files outside the change set keep the old scoping when the graph is
    known to be short: a traversed file failed to parse (its symbols are
    missing, so every caller's edge into it would read as gone), or the
    framework or dynamic-hint pass failed (``synthetic_edges_complete``).

    The one difference between the two sets is ``co_changes``. The graph holds
    every pair only when the history walk completed; otherwise it has the
    changed files' pairs alone, so the other files' stored pairs are left to
    :func:`sync_co_change_edges`, which runs only on a complete walk.
    """
    # An empty parse still counts: a repo with no source files has nothing to
    # reconcile, and it must be stamped, or a missing stamp re-parses it forever.
    if graph_builder is None or parsed_files is None:
        return
    from repowise.core.ingestion.parse_cache import parser_fingerprint
    from repowise.core.persistence.crud import reconcile_edges_for_files

    fingerprint = parser_fingerprint()
    parser_changed = await _stored_edges_parser_fingerprint(session, repo_id) != fingerprint
    if parser_changed:
        logger.info("graph_edges_parser_backfill", repo_id=repo_id, files=len(parsed_files))

    reconcile_paths, edges = _changed_file_edges(graph_builder, parsed_files, changed_paths)
    if reconcile_paths:
        await reconcile_edges_for_files(session, repo_id, reconcile_paths, edges)
    other_paths = await _reconcile_unchanged_files(
        session,
        repo_id,
        graph_builder,
        parsed_files,
        set(changed_paths or ()),
        widen=parser_changed,
    )
    if reconcile_paths or other_paths:
        # A symbol renamed or removed otherwise keeps its node beside the new one.
        await _prune_vanished_symbol_nodes(
            session, repo_id, graph_builder, [*reconcile_paths, *other_paths]
        )
    await _sync_complete_co_changes(session, repo_id, graph_builder)
    # Only after the reconcile actually ran to completion. Raising here (or in any
    # caller before the commit) leaves the old stamp, so the next update retries
    # rather than stranding the files it never reached.
    # A short graph skipped the files outside the change set, so they still
    # hold the old parser's edges.
    if parser_changed and _graph_is_complete(graph_builder, parsed_files):
        await stamp_edges_parser_fingerprint(session, repo_id, fingerprint)


async def _sync_complete_co_changes(session: Any, repo_id: str, graph_builder: Any) -> None:
    # Only when the rebuild re-added every pair; a failed or skipped walk leaves
    # the graph without them, and syncing would wipe the stored ones.
    if getattr(graph_builder, "co_change_edges_complete", False):
        from .co_change_edges import sync_co_change_edges

        await sync_co_change_edges(session, repo_id, graph_builder)


async def _reconcile_unchanged_files(
    session: Any,
    repo_id: str,
    graph_builder: Any,
    parsed_files: list[Any],
    changed: set[str],
    *,
    widen: bool,
) -> list[str]:
    """Reconcile the parsed files outside *changed* whose edges moved.

    Every one of them when *widen*. Returns the paths reconciled. Nothing when
    the graph is short (:func:`_graph_is_complete`). Their ``co_changes`` rows
    are left to the co-change sync.
    """
    from repowise.core.persistence.crud import reconcile_edges_for_files

    from .co_change_edges import _CO_CHANGES

    if not _graph_is_complete(graph_builder, parsed_files):
        return []
    others = {pf.file_info.path for pf in parsed_files} - changed
    # Narrowed only against a snapshot of the stored graph. Without one the
    # rebuilt graph alone cannot show a call that stopped resolving, so every
    # file is reconciled; the snapshot this run then saves makes the next one
    # cheap again.
    if not widen and getattr(graph_builder, "previous_seed_edges", None) is not None:
        others &= await _drifted_files(session, repo_id, graph_builder, changed)
    if not others:
        return []
    paths, edges = _changed_file_edges(graph_builder, parsed_files, sorted(others))
    if paths:
        await reconcile_edges_for_files(
            session,
            repo_id,
            paths,
            [e for e in edges if e["edge_type"] != _CO_CHANGES],
            keep_edge_types=frozenset({_CO_CHANGES}),
        )
    return paths


def _edge_owners(nodes: Any) -> dict[str, str | None]:
    """``{node_id: owning file}`` over ``(node_id, attrs)`` pairs.

    A file node owns itself, a symbol node its ``file_path``.
    """
    return {
        node_id: node_id if attrs.get("node_type", "file") == "file" else attrs.get("file_path")
        for node_id, attrs in nodes
    }


# The in-memory edge fields that reach a ``graph_edges`` row (see _edge_row).
_EDGE_ROW_FIELDS = (
    "imported_names",
    "confidence",
    "hint_source",
    "resolution_origin",
    "call_lines",
    "type_only",
    "deferred",
)


def _edge_signature(data: dict) -> tuple:
    return tuple(
        tuple(value) if isinstance(value, list) else value
        for value in (data.get(field) for field in _EDGE_ROW_FIELDS)
    )


async def _drifted_files(
    session: Any, repo_id: str, graph_builder: Any, changed: set[str]
) -> set[str]:
    """Files whose stored edges may no longer match the rebuilt graph.

    The owners of every stored row into a changed file (indexed lookups on the
    changed files' node ids, the old ids included), and of every edge that
    differs from the last persisted graph (``previous_seed_edges``), which also
    catches a call that stopped resolving without gaining a target in a
    changed file. The store is read only for the changed files and the
    rest is a dictionary diff, so no step reads the whole edge table.
    """
    from repowise.core.analysis.execution_graph import file_of_symbol
    from repowise.core.ingestion.models import TARGET_OWNED_HINTS

    graph = graph_builder.graph()
    owner = _edge_owners(graph.nodes(data=True))
    drifted: set[str] = set()

    def mark(source: str, target: str, hint: Any, owners: dict) -> None:
        drifted.add(owners.get(source) or file_of_symbol(source))
        if hint in TARGET_OWNED_HINTS:
            drifted.add(owners.get(target) or file_of_symbol(target))

    previous = graph_builder.previous_seed_edges
    targets = {node for node, path in owner.items() if path in changed} | changed
    for source, target, hint in await _stored_edges_into(session, repo_id, targets, changed):
        mark(source, target, hint, owner)

    added, removed = _snapshot_diff(graph, previous)
    for u, v, hint in added:
        mark(u, v, hint, owner)
    prev_owner = _edge_owners(previous["nodes"])
    for u, v, hint in removed:
        mark(u, v, hint, prev_owner)
    drifted.discard(None)
    return drifted


def _snapshot_diff(
    graph: Any, previous: dict[str, list]
) -> tuple[list[tuple[str, str, Any]], list[tuple[str, str, Any]]]:
    """``(added_or_changed, removed)`` edges of *graph* against *previous*, as
    ``(source, target, hint_source)``. Co-change pairs are left to their sync."""
    from .co_change_edges import _CO_CHANGES

    prev_edges = {
        (u, v, attrs.get("edge_type", "imports")): attrs for u, v, attrs in previous["edges"]
    }
    added: list[tuple[str, str, Any]] = []
    for u, v, data in graph.edges(data=True):
        if data.get("edge_type") == _CO_CHANGES:
            continue
        before = prev_edges.pop((u, v, data.get("edge_type", "imports")), None)
        # Whole-dict equality first: it is the common case and runs in C.
        if before is None or (before != data and _edge_signature(before) != _edge_signature(data)):
            added.append((u, v, data.get("hint_source")))
    removed = [
        (u, v, attrs.get("hint_source"))
        for (u, v, edge_type), attrs in prev_edges.items()
        if edge_type != _CO_CHANGES
    ]
    return added, removed


async def _stored_edges_into(
    session: Any, repo_id: str, targets: set[str], changed: set[str]
) -> list[tuple[str, str, str | None]]:
    """``(source, target, hint_source)`` of the stored rows into *targets*.

    Also into every stored node of a changed file, so a symbol the rebuilt
    graph no longer has still names the callers that pointed at it.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import GraphEdge, GraphNode

    paths = sorted(changed)
    ids = set(targets)
    for i in range(0, len(paths), _PRUNE_CHUNK):
        ids.update(
            (
                await session.execute(
                    select(GraphNode.node_id).where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.file_path.in_(paths[i : i + _PRUNE_CHUNK]),
                    )
                )
            ).scalars()
        )
    ordered = sorted(ids)
    out: list[tuple[str, str, str | None]] = []
    for i in range(0, len(ordered), _PRUNE_CHUNK):
        rows = await session.execute(
            select(GraphEdge.source_node_id, GraphEdge.target_node_id, GraphEdge.hint_source).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.target_node_id.in_(ordered[i : i + _PRUNE_CHUNK]),
            )
        )
        out.extend(tuple(row) for row in rows)
    return out


def _graph_is_complete(graph_builder: Any, parsed_files: list[Any]) -> bool:
    """Whether the graph holds every row the store does for the files it covers.

    Short when a synthetic-edge pass failed, or when a traversed file that
    failed to parse had graph rows before that the graph no longer holds: its
    symbols are missing, so every caller's edge into it reads as gone. The
    last persisted graph (``previous_seed_edges``) shows which rows a file
    had. A file that never parsed had none, a bare file node with no edge
    carries nothing a caller can lose, and rows the update restored
    (``restore_unparsed_graph_rows``) are not lost; without that snapshot any
    unparsed file counts as short.
    """
    if not getattr(graph_builder, "synthetic_edges_complete", True):
        return False
    traversed = getattr(graph_builder, "traversed_file_paths", None) or set()
    unparsed = set(traversed) - {pf.file_info.path for pf in parsed_files}
    if not unparsed:
        return True
    previous = getattr(graph_builder, "previous_seed_edges", None)
    if previous is None:
        return False
    return not _lost_unparsed_rows(graph_builder.graph(), previous, unparsed)


def _lost_unparsed_rows(graph: Any, previous: dict[str, list], unparsed: set[str]) -> bool:
    """Whether an unparsed file had a symbol or an edge that *graph* no longer holds."""
    owned = {
        node_id
        for node_id, attrs in previous["nodes"]
        if node_id in unparsed or attrs.get("file_path") in unparsed
    }
    if not owned:
        return False
    if any(node_id not in graph for node_id in owned - unparsed):
        return True
    return any(
        (source in owned or target in owned)
        and not _holds_edge(graph, source, target, attrs.get("edge_type", "imports"))
        for source, target, attrs in previous["edges"]
    )


def _holds_edge(graph: Any, source: str, target: str, edge_type: str) -> bool:
    data = graph.get_edge_data(source, target)
    return data is not None and data.get("edge_type", "imports") == edge_type


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


async def prune_absent_synthetic_nodes(session: Any, repo_id: str, graph_builder: Any) -> int:
    """Delete ``external:`` / ``framework:`` rows the rebuilt graph no longer mints.

    These nodes name no file, so the deleted-file prune can never judge them,
    and :func:`persist_graph_nodes` only upserts: a package the last importer
    stopped using kept its node and metrics row after every update, though a
    fresh index has neither. The rebuilt graph is the authority, as it is for
    :func:`prune_absent_graph_rows` on a full run, and an empty graph is a
    broken run that prunes nothing. A node a stored edge still starts or ends
    at is kept: a file that failed to parse keeps its edges, and no update
    reconciles the edges a ``framework:`` anchor sources.

    Returns the number of nodes deleted.
    """
    from sqlalchemy import delete, or_, select

    from repowise.core.persistence.models import GraphEdge, GraphMetric, GraphNode

    graph = graph_builder.graph()
    if graph.number_of_nodes() == 0:
        return 0
    stored: set[str] = set()
    for model in (GraphNode, GraphMetric):
        stored.update(
            (
                await session.execute(
                    select(model.node_id)
                    .where(
                        model.repository_id == repo_id,
                        or_(
                            model.node_id.like("external:%"),
                            model.node_id.like("framework:%"),
                        ),
                    )
                    .distinct()
                )
            ).scalars()
        )
    absent = sorted(n for n in stored if n not in graph)
    gone: list[str] = []
    for i in range(0, len(absent), _PRUNE_CHUNK):
        batch = absent[i : i + _PRUNE_CHUNK]
        linked: set[str] = set()
        for column in (GraphEdge.target_node_id, GraphEdge.source_node_id):
            linked.update(
                (
                    await session.execute(
                        select(column).where(
                            GraphEdge.repository_id == repo_id, column.in_(batch)
                        )
                    )
                ).scalars()
            )
        gone.extend(n for n in batch if n not in linked)
    for i in range(0, len(gone), _PRUNE_CHUNK):
        batch = gone[i : i + _PRUNE_CHUNK]
        for model in (GraphNode, GraphMetric):
            await session.execute(
                delete(model).where(model.repository_id == repo_id, model.node_id.in_(batch))
            )
    if gone:
        logger.info("graph_synthetic_nodes_pruned", repo_id=repo_id, nodes=len(gone))
    return len(gone)
