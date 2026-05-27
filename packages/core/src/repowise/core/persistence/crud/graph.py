"""CRUD operations for the graph domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    GraphEdge,
    GraphMetric,
    GraphNode,
    _new_uuid,
)
from ._shared import _BATCH_SIZE, _batch_upsert

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
        existing.confidence = confidence


def _update_graph_metric(existing: GraphMetric, m: dict) -> None:
    for key in _METRIC_FIELDS:
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
    await _batch_upsert(
        session,
        GraphNode,
        nodes,
        key_fn=lambda n: (
            GraphNode.repository_id == repository_id,
            GraphNode.node_id == n.get("node_id", ""),
        ),
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
    await _batch_upsert(
        session,
        GraphEdge,
        edges,
        key_fn=lambda e: (
            GraphEdge.repository_id == repository_id,
            GraphEdge.source_node_id == e.get("source_node_id", ""),
            GraphEdge.target_node_id == e.get("target_node_id", ""),
            GraphEdge.edge_type == e.get("edge_type", "imports"),
        ),
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
    await _batch_upsert(
        session,
        GraphMetric,
        list(metrics.items()),
        key_fn=lambda kv: (
            GraphMetric.repository_id == repository_id,
            GraphMetric.node_id == kv[0],
        ),
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
