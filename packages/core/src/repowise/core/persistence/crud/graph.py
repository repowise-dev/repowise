"""CRUD operations for the graph domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

import json

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    GraphEdge,
    GraphMetric,
    GraphNode,
    GraphNodeMembership,
    _new_uuid,
)
from ._shared import _BATCH_SIZE, _batch_upsert_keyed

# ---------------------------------------------------------------------------
# Graph CRUD (batch)
# ---------------------------------------------------------------------------

_METRIC_FIELDS = ("pagerank", "betweenness", "community_id", "in_degree", "out_degree")


def _update_graph_node(existing: GraphNode, node_data: dict) -> None:
    for key, val in node_data.items():
        if key not in ("id", "repository_id", "created_at") and hasattr(existing, key):
            setattr(existing, key, val)


def _update_graph_edge(existing: GraphEdge, edge_data: dict) -> None:
    imported = edge_data.get("imported_names_json")
    if imported is not None:
        existing.imported_names_json = imported
    confidence = edge_data.get("confidence")
    if confidence is not None:
        # Keep the max on collision, mirroring the in-memory resolver
        # (_resolvers.py:504-505). A pair can carry several resolved calls of
        # differing confidence; a last-write upsert could stamp a real call
        # below _FLOW_CALLS_CONF_FLOOR (0.5) and drop it from flow-path answers.
        existing.confidence = max(existing.confidence or 0.0, confidence)


def _update_graph_metric(existing: GraphMetric, m: dict) -> None:
    for key in _METRIC_FIELDS:
        if key in m:
            setattr(existing, key, m[key])


_MEMBERSHIP_FIELDS = ("node_type", "scc_id", "scc_size", "symbol_community_id")


def _update_graph_node_membership(existing: GraphNodeMembership, m: dict) -> None:
    for key in _MEMBERSHIP_FIELDS:
        if key in m:
            setattr(existing, key, m[key])


async def batch_upsert_graph_nodes(
    session: AsyncSession,
    repository_id: str,
    nodes: list[dict],
) -> None:
    """Upsert graph nodes for a repository in batches of up to 500.

    Each element of *nodes* is a dict with keys matching GraphNode fields
    (excluding id and repository_id which are set here).

    Uses SELECT-then-INSERT/UPDATE for dialect portability.
    """
    await _batch_upsert_keyed(
        session,
        GraphNode,
        nodes,
        prefilter=(GraphNode.repository_id == repository_id,),
        item_key_fn=lambda n: n.get("node_id", ""),
        row_key_fn=lambda row: row.node_id,
        update_fn=_update_graph_node,
        insert_fn=lambda n: GraphNode(
            id=_new_uuid(),
            repository_id=repository_id,
            **{k: v for k, v in n.items() if k not in ("id", "repository_id")},
        ),
    )


async def batch_upsert_graph_edges(
    session: AsyncSession,
    repository_id: str,
    edges: list[dict],
) -> None:
    """Upsert graph edges for a repository.

    Each element of *edges* should have ``source_node_id``, ``target_node_id``,
    ``edge_type``, and optionally ``imported_names_json`` and ``confidence``.

    The unique constraint is (repository_id, source, target, edge_type),
    allowing multiple edge types between the same pair of nodes.
    """
    await _batch_upsert_keyed(
        session,
        GraphEdge,
        edges,
        prefilter=(GraphEdge.repository_id == repository_id,),
        item_key_fn=lambda e: (
            e.get("source_node_id", ""),
            e.get("target_node_id", ""),
            e.get("edge_type", "imports"),
        ),
        row_key_fn=lambda row: (row.source_node_id, row.target_node_id, row.edge_type),
        update_fn=_update_graph_edge,
        insert_fn=lambda e: GraphEdge(
            id=_new_uuid(),
            repository_id=repository_id,
            source_node_id=e.get("source_node_id", ""),
            target_node_id=e.get("target_node_id", ""),
            imported_names_json=e.get("imported_names_json", "[]"),
            edge_type=e.get("edge_type", "imports"),
            confidence=e.get("confidence", 1.0),
        ),
    )


# Chunk size for the scoped edge delete/existence work — stays under SQLite's
# host-parameter limit when a wide catch-up update touches many files.
_EDGE_RECONCILE_CHUNK = 400


async def reconcile_edges_for_files(
    session: AsyncSession,
    repository_id: str,
    source_file_paths: list[str],
    edges: list[dict],  # fresh edges whose source belongs to those files
) -> int:
    """Make ``graph_edges`` outgoing from *source_file_paths* match a fresh parse.

    Sibling of :func:`reconcile_symbols_for_files` for the edge table. The
    incremental update path rebuilds the in-memory graph, but only the full-init
    path ever persisted edges, so ``graph_edges`` froze at the last full index:
    new imports/calls stayed invisible and edges a changed file dropped lingered
    as false BFS paths (the Phase E flow-path traversal reads adjacency straight
    from this table). This deletes every edge whose source node belongs to one
    of *source_file_paths* — both the file node and its symbol nodes, including
    symbols the change deleted whose node rows the incremental path never prunes
    — then inserts the fresh set. Edges *into* a changed file from an unchanged
    one are owned by that other file and left untouched, the same file-scoping
    the symbol reconciler uses; a full reindex reconciles those.

    Returns the number of deleted rows.
    """
    scoped = [p for p in dict.fromkeys(source_file_paths) if p]
    if not scoped:
        return 0

    # Every graph node owned by a changed file. Pulled from graph_nodes (not
    # just the fresh edge list) so outgoing edges of a symbol the change deleted
    # — whose node row the incremental path leaves behind — are cleared too. A
    # file node keys on node_id == path; a symbol node carries file_path.
    source_ids: set[str] = set()
    for i in range(0, len(scoped), _EDGE_RECONCILE_CHUNK):
        chunk = scoped[i : i + _EDGE_RECONCILE_CHUNK]
        rows = (
            (
                await session.execute(
                    select(GraphNode.node_id).where(
                        GraphNode.repository_id == repository_id,
                        or_(
                            and_(
                                GraphNode.node_type == "file",
                                GraphNode.node_id.in_(chunk),
                            ),
                            GraphNode.file_path.in_(chunk),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        source_ids.update(rows)
    # Union in the fresh edges' own sources so a brand-new node's edges are still
    # rewritten cleanly even if the node-row read above raced its insert.
    source_ids.update(e.get("source_node_id", "") for e in edges)
    source_ids.discard("")
    if not source_ids:
        return 0

    id_list = list(source_ids)
    deleted = 0
    for i in range(0, len(id_list), _EDGE_RECONCILE_CHUNK):
        batch = id_list[i : i + _EDGE_RECONCILE_CHUNK]
        res = await session.execute(
            delete(GraphEdge).where(
                GraphEdge.repository_id == repository_id,
                GraphEdge.source_node_id.in_(batch),
            )
        )
        deleted += res.rowcount or 0

    # Every fresh edge's source was just cleared, so these are all plain inserts
    # — no need for the repo-wide upsert (which reloads every edge row).
    for e in edges:
        session.add(
            GraphEdge(
                id=_new_uuid(),
                repository_id=repository_id,
                source_node_id=e.get("source_node_id", ""),
                target_node_id=e.get("target_node_id", ""),
                imported_names_json=e.get("imported_names_json", "[]"),
                edge_type=e.get("edge_type", "imports"),
                confidence=e.get("confidence", 1.0),
            )
        )
    await session.flush()
    return deleted


async def batch_upsert_graph_metrics(
    session: AsyncSession,
    repository_id: str,
    metrics: dict[str, dict],
) -> None:
    """Materialize the file-level metrics snapshot into ``graph_metrics``.

    *metrics* maps ``node_id`` → a dict with ``pagerank``, ``betweenness``,
    ``community_id``, ``in_degree``, ``out_degree``. Additive to
    ``graph_nodes`` — this is the snapshot read back by
    ``GraphBuilder.load_metrics_from_sql`` on large repos. SELECT-then-write
    for dialect portability (SQLite + Postgres).
    """
    await _batch_upsert_keyed(
        session,
        GraphMetric,
        list(metrics.items()),
        prefilter=(GraphMetric.repository_id == repository_id,),
        item_key_fn=lambda kv: kv[0],
        row_key_fn=lambda row: row.node_id,
        update_fn=lambda existing, kv: _update_graph_metric(existing, kv[1]),
        insert_fn=lambda kv: GraphMetric(
            id=_new_uuid(),
            repository_id=repository_id,
            node_id=kv[0],
            pagerank=float(kv[1].get("pagerank", 0.0)),
            betweenness=float(kv[1].get("betweenness", 0.0)),
            community_id=int(kv[1].get("community_id", 0)),
            in_degree=int(kv[1].get("in_degree", 0)),
            out_degree=int(kv[1].get("out_degree", 0)),
        ),
    )


async def batch_upsert_graph_node_membership(
    session: AsyncSession,
    repository_id: str,
    membership: dict[str, dict],
) -> None:
    """Materialize the SCC + symbol-community snapshot into ``graph_node_membership``.

    *membership* maps ``node_id`` → a dict with ``node_type`` and any of
    ``scc_id`` / ``scc_size`` (file nodes in a size>=2 cycle) /
    ``symbol_community_id`` (symbol nodes). Additive to ``graph_nodes``;
    SELECT-then-write for dialect portability (SQLite + Postgres).
    """
    await _batch_upsert_keyed(
        session,
        GraphNodeMembership,
        list(membership.items()),
        prefilter=(GraphNodeMembership.repository_id == repository_id,),
        item_key_fn=lambda kv: kv[0],
        row_key_fn=lambda row: row.node_id,
        update_fn=lambda existing, kv: _update_graph_node_membership(existing, kv[1]),
        insert_fn=lambda kv: GraphNodeMembership(
            id=_new_uuid(),
            repository_id=repository_id,
            node_id=kv[0],
            node_type=str(kv[1].get("node_type", "file")),
            scc_id=(None if kv[1].get("scc_id") is None else int(kv[1]["scc_id"])),
            scc_size=int(kv[1].get("scc_size", 0)),
            symbol_community_id=(
                None
                if kv[1].get("symbol_community_id") is None
                else int(kv[1]["symbol_community_id"])
            ),
        ),
    )


async def get_scc_members(
    session: AsyncSession,
    repository_id: str,
) -> dict[int, list[str]]:
    """Read the persisted file-level cycles as ``scc_id → [node_id, ...]``.

    Only non-trivial SCCs (``scc_size >= 2``) are materialized, so every
    returned group is a real import cycle.
    """
    result = await session.execute(
        select(GraphNodeMembership).where(
            GraphNodeMembership.repository_id == repository_id,
            GraphNodeMembership.scc_id.isnot(None),
        )
    )
    out: dict[int, list[str]] = {}
    for row in result.scalars().all():
        out.setdefault(int(row.scc_id), []).append(row.node_id)
    for members in out.values():
        members.sort()
    return out


async def get_graph_metrics(
    session: AsyncSession,
    repository_id: str,
) -> dict[str, dict]:
    """Read the materialized ``graph_metrics`` snapshot as ``node_id → metrics``."""
    result = await session.execute(
        select(GraphMetric).where(GraphMetric.repository_id == repository_id)
    )
    return {
        row.node_id: {
            "pagerank": row.pagerank,
            "betweenness": row.betweenness,
            "community_id": row.community_id,
            "in_degree": row.in_degree,
            "out_degree": row.out_degree,
        }
        for row in result.scalars().all()
    }


async def get_all_graph_nodes(
    session: AsyncSession,
    repository_id: str,
) -> list[dict]:
    """Read every persisted graph node as a list of plain dicts.

    Used to rehydrate an in-memory :class:`GraphBuilder` from SQL without
    re-parsing or re-resolving the graph (see
    ``repowise.core.pipeline.upgrade.rehydrate_graph_builder``). Each dict
    carries ``node_id`` plus the file/symbol attributes that the NetworkX node
    needs for traversal and rendering.
    """
    result = await session.execute(
        select(GraphNode).where(GraphNode.repository_id == repository_id)
    )
    return [
        {
            "node_id": row.node_id,
            "node_type": row.node_type,
            "language": row.language,
            "symbol_count": row.symbol_count,
            "has_error": row.has_error,
            "is_test": row.is_test,
            "is_entry_point": row.is_entry_point,
            "kind": row.kind,
            "name": row.name,
            "qualified_name": row.qualified_name,
            "file_path": row.file_path,
            "start_line": row.start_line,
            "end_line": row.end_line,
            "visibility": row.visibility,
            "signature": row.signature,
            "parent_symbol_id": row.parent_symbol_id,
        }
        for row in result.scalars().all()
    ]


async def get_all_graph_edges(
    session: AsyncSession,
    repository_id: str,
) -> list[dict]:
    """Read every persisted graph edge as a list of plain dicts.

    Companion to :func:`get_all_graph_nodes` for graph rehydration. The
    ``imported_names_json`` column is decoded back into a list so the
    rehydrated edge matches the in-memory shape produced during ingestion.
    """
    result = await session.execute(
        select(GraphEdge).where(GraphEdge.repository_id == repository_id)
    )
    edges: list[dict] = []
    for row in result.scalars().all():
        try:
            imported_names = json.loads(row.imported_names_json or "[]")
        except (ValueError, TypeError):
            imported_names = []
        edges.append(
            {
                "source_node_id": row.source_node_id,
                "target_node_id": row.target_node_id,
                "edge_type": row.edge_type,
                "confidence": row.confidence,
                "imported_names": imported_names,
            }
        )
    return edges


# ---------------------------------------------------------------------------
# Graph read-side queries (Phase 5 — MCP graph tools)
# ---------------------------------------------------------------------------


async def get_graph_node(
    session: AsyncSession,
    repository_id: str,
    node_id: str,
) -> GraphNode | None:
    """Look up a single GraphNode by its ``node_id`` (file path or symbol ID)."""
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_id == node_id,
        )
    )
    return result.scalar_one_or_none()


async def get_graph_edges_for_node(
    session: AsyncSession,
    repository_id: str,
    node_id: str,
    *,
    direction: str = "both",
    edge_types: list[str] | None = None,
    limit: int = 50,
) -> list[GraphEdge]:
    """Return edges adjacent to *node_id*.

    Parameters
    ----------
    direction:
        ``"callers"`` → inbound edges (target == node_id),
        ``"callees"`` → outbound edges (source == node_id),
        ``"both"`` → union of both.
    edge_types:
        Optional filter, e.g. ``["calls"]`` or ``["extends", "implements"]``.
    limit:
        Max edges per direction.
    """
    results: list[GraphEdge] = []

    if direction in ("callers", "both"):
        q = select(GraphEdge).where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.target_node_id == node_id,
        )
        if edge_types:
            q = q.where(GraphEdge.edge_type.in_(edge_types))
        q = q.limit(limit)
        res = await session.execute(q)
        results.extend(res.scalars().all())

    if direction in ("callees", "both"):
        q = select(GraphEdge).where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.source_node_id == node_id,
        )
        if edge_types:
            q = q.where(GraphEdge.edge_type.in_(edge_types))
        q = q.limit(limit)
        res = await session.execute(q)
        results.extend(res.scalars().all())

    return results


async def get_graph_nodes_by_ids(
    session: AsyncSession,
    repository_id: str,
    node_ids: list[str],
) -> dict[str, GraphNode]:
    """Batch-lookup GraphNodes by node_id. Returns ``{node_id: GraphNode}``."""
    if not node_ids:
        return {}
    # Process in batches to stay under SQLite parameter limits
    out: dict[str, GraphNode] = {}
    for i in range(0, len(node_ids), _BATCH_SIZE):
        batch = node_ids[i : i + _BATCH_SIZE]
        result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository_id,
                GraphNode.node_id.in_(batch),
            )
        )
        for node in result.scalars().all():
            out[node.node_id] = node
    return out


async def get_community_members(
    session: AsyncSession,
    repository_id: str,
    community_id: int,
    *,
    node_type: str = "file",
    limit: int = 50,
) -> list[GraphNode]:
    """Return all nodes in a community, ordered by PageRank descending."""
    result = await session.execute(
        select(GraphNode)
        .where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_type == node_type,
            GraphNode.community_id == community_id,
        )
        .order_by(GraphNode.pagerank.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_all_file_metrics(
    session: AsyncSession,
    repository_id: str,
) -> list[GraphNode]:
    """Return all file-type GraphNodes (for percentile computation)."""
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_type == "file",
        )
    )
    return list(result.scalars().all())


async def get_cross_community_edges(
    session: AsyncSession,
    repository_id: str,
    community_id: int,
) -> list[dict]:
    """Count edges crossing from *community_id* to other communities.

    Returns a list of ``{"target_community_id": int, "edge_count": int}``.
    Uses a join through ``graph_nodes`` to resolve target community.
    """
    # Alias for the target node lookup
    target_node = GraphNode.__table__.alias("tn")
    source_node = GraphNode.__table__.alias("sn")

    q = (
        select(
            target_node.c.community_id.label("target_community_id"),
            func.count().label("edge_count"),
        )
        .select_from(GraphEdge.__table__)
        .join(
            source_node,
            (GraphEdge.__table__.c.source_node_id == source_node.c.node_id)
            & (GraphEdge.__table__.c.repository_id == source_node.c.repository_id),
        )
        .join(
            target_node,
            (GraphEdge.__table__.c.target_node_id == target_node.c.node_id)
            & (GraphEdge.__table__.c.repository_id == target_node.c.repository_id),
        )
        .where(
            GraphEdge.__table__.c.repository_id == repository_id,
            source_node.c.community_id == community_id,
            target_node.c.community_id != community_id,
            # Only count file-level edges for meaningful community crossing
            source_node.c.node_type == "file",
            target_node.c.node_type == "file",
        )
        .group_by(target_node.c.community_id)
        .order_by(func.count().desc())
    )
    result = await session.execute(q)
    return [
        {"target_community_id": row.target_community_id, "edge_count": row.edge_count}
        for row in result.all()
    ]


async def get_top_entry_points(
    session: AsyncSession,
    repository_id: str,
    *,
    min_score: float = 0.3,
    limit: int = 20,
) -> list[GraphNode]:
    """Return symbol nodes with stored entry_point_score >= *min_score*.

    Scores are stored inside ``community_meta_json``. Since the count of
    symbol nodes is typically < 5000, an in-memory filter is acceptable.
    """
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_type == "symbol",
        )
    )
    all_symbols = result.scalars().all()

    scored: list[tuple[float, GraphNode]] = []
    for node in all_symbols:
        try:
            meta = json.loads(node.community_meta_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        score = meta.get("entry_point_score")
        if score is not None and score >= min_score:
            scored.append((score, node))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [node for _, node in scored[:limit]]


async def get_node_degree_counts(
    session: AsyncSession,
    repository_id: str,
    node_id: str,
) -> dict[str, int]:
    """Return in-degree and out-degree for a node from edge counts."""
    in_result = await session.execute(
        select(func.count())
        .select_from(GraphEdge)
        .where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.target_node_id == node_id,
        )
    )
    out_result = await session.execute(
        select(func.count())
        .select_from(GraphEdge)
        .where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.source_node_id == node_id,
        )
    )
    return {
        "in_degree": in_result.scalar() or 0,
        "out_degree": out_result.scalar() or 0,
    }
