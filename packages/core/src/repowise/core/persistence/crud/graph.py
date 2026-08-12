"""CRUD operations for the graph domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    GraphEdge,
    GraphMetric,
    GraphNode,
    GraphNodeMembership,
    _new_uuid,
)
from ._shared import _BATCH_SIZE, UpsertGate, _batch_upsert_keyed

# ---------------------------------------------------------------------------
# Graph CRUD (batch)
# ---------------------------------------------------------------------------

_METRIC_FIELDS = ("pagerank", "betweenness", "community_id", "in_degree", "out_degree")

# Absolute tolerance for the centrality columns' skip test. The observed
# process-to-process spread on an unchanged repo is ~1e-17 (bench probe
# `probe_graph_determinism.py --cmp`), so this sits five orders above the
# noise it exists to absorb and six below the smallest difference any reader
# of these columns can act on — the rankings they feed are read as an order,
# and no two distinct files sit 1e-12 apart in it. A backend that stored these
# at lower precision than the payload computes them would push every row past
# the tolerance and write it, which is today's behaviour, not a wrong one.
_CENTRALITY_ATOL = 1e-12
_CENTRALITY_COLUMNS = frozenset({"pagerank", "betweenness"})


def _update_graph_node(existing: GraphNode, node_data: dict) -> None:
    for key, val in node_data.items():
        if key not in ("id", "repository_id", "created_at") and hasattr(existing, key):
            setattr(existing, key, val)


# Every column ``_update_graph_node`` can write from a node payload. A payload
# field missing here turns the gate off for that node rather than skipping it
# (see UpsertGate), so adding a field to persist_graph_nodes without adding it
# here costs a write, never a lost one. The direction that is NOT self-healing
# is the reverse: an update_fn that writes something the payload does not carry
# (a timestamp, a counter) would be skipped along with the row, so keep these
# three update_fns strictly payload-driven.
_NODE_FIELDS = (
    "node_type",
    "language",
    "symbol_count",
    "has_error",
    "is_test",
    "is_entry_point",
    "pagerank",
    "betweenness",
    "community_id",
    "community_meta_json",
    "kind",
    "name",
    "qualified_name",
    "file_path",
    "start_line",
    "end_line",
    "visibility",
    "signature",
    "parent_symbol_id",
)


def _node_gate_values(node_data: dict) -> dict:
    return {k: v for k, v in node_data.items() if k != "node_id"}


def _update_graph_edge(existing: GraphEdge, edge_data: dict) -> None:
    imported = edge_data.get("imported_names_json")
    if imported is not None:
        existing.imported_names_json = imported
    # Assigned unconditionally, including None: an edge that stops being
    # cohesion (a real import statement appears between two package siblings)
    # must lose the stamp, or cycle detection keeps skipping it forever.
    existing.hint_source = edge_data.get("hint_source")
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

# Chunk size for IN (...) deletes — stays under SQLite's host-parameter limit.
_MEMBERSHIP_PRUNE_CHUNK = 500


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

    *nodes* is a full snapshot of the repo's graph on every call, including the
    incremental update path, where a one-file change leaves the overwhelming
    majority of it identical. The gate skips those rows before they are
    hydrated as ORM objects.
    """
    await _batch_upsert_keyed(
        session,
        GraphNode,
        nodes,
        prefilter=(GraphNode.repository_id == repository_id,),
        item_key_fn=lambda n: n.get("node_id", ""),
        row_key_fn=lambda row: row.node_id,
        update_fn=_update_graph_node,
        gate=UpsertGate(
            key_column=GraphNode.node_id,
            columns=_NODE_FIELDS,
            item_values_fn=_node_gate_values,
            float_columns=_CENTRALITY_COLUMNS,
            float_atol=_CENTRALITY_ATOL,
        ),
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
            hint_source=e.get("hint_source"),
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
                hint_source=e.get("hint_source"),
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
        gate=UpsertGate(
            key_column=GraphMetric.node_id,
            columns=_METRIC_FIELDS,
            item_values_fn=lambda kv: {k: v for k, v in kv[1].items() if k in _METRIC_FIELDS},
            float_columns=_CENTRALITY_COLUMNS,
            float_atol=_CENTRALITY_ATOL,
        ),
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

    The snapshot is a full recomputation, so absence is meaningful: a node the
    caller did not send is a node that is no longer in any cycle or community.
    Rows for absent nodes are therefore deleted rather than left behind. Without
    that, a pure upsert let a file that dropped out of a cycle keep its old
    ``scc_id`` / ``scc_size`` forever, and the Stats "largest cycle" record and
    ``get_scc_members`` both read exactly those rows — so a fixed cycle stayed
    on screen indefinitely.
    """
    current = set(membership)
    # The stale scan already has to walk every row this repo owns, so it reads
    # the comparison columns at the same time and hands them to the gate. A
    # second narrow scan measured slower than the writes it was saving on a
    # snapshot where most rows genuinely moved.
    existing_rows = (
        await session.execute(
            select(
                GraphNodeMembership.node_id,
                *[getattr(GraphNodeMembership, c) for c in _MEMBERSHIP_FIELDS],
            ).where(GraphNodeMembership.repository_id == repository_id)
        )
    ).all()
    stale = [row[0] for row in existing_rows if row[0] not in current]
    for i in range(0, len(stale), _MEMBERSHIP_PRUNE_CHUNK):
        await session.execute(
            delete(GraphNodeMembership).where(
                GraphNodeMembership.repository_id == repository_id,
                GraphNodeMembership.node_id.in_(stale[i : i + _MEMBERSHIP_PRUNE_CHUNK]),
            )
        )

    await _batch_upsert_keyed(
        session,
        GraphNodeMembership,
        list(membership.items()),
        prefilter=(GraphNodeMembership.repository_id == repository_id,),
        item_key_fn=lambda kv: kv[0],
        row_key_fn=lambda row: row.node_id,
        update_fn=lambda existing, kv: _update_graph_node_membership(existing, kv[1]),
        gate=UpsertGate(
            key_column=GraphNodeMembership.node_id,
            columns=_MEMBERSHIP_FIELDS,
            item_values_fn=lambda kv: {
                k: v for k, v in kv[1].items() if k in _MEMBERSHIP_FIELDS
            },
            # Pruned keys are excluded: their rows are gone by the time the
            # upsert runs, and a snapshot never sends a key it just pruned.
            prefetched={
                row[0]: row[1:] for row in existing_rows if row[0] in current
            },
        ),
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
                "hint_source": row.hint_source,
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


async def get_test_file_paths(
    session: AsyncSession,
    repository_id: str,
    paths: Sequence[str] | None = None,
) -> set[str]:
    """Relative paths of every file the ingester classified as test material.

    Reads the flag ingestion already decided per file (#1103 made ``is_test``
    the single canonical answer to "is this a test"). For a file node
    ``node_id`` *is* the repo-relative path, so the result joins straight onto
    ``HealthFileMetric.file_path`` / ``HealthFinding.file_path`` with no
    denormalized column and no migration.

    ``node_type == "file"`` is required, not incidental: symbol nodes carry
    ``is_test`` too and their ``node_id`` is a ``"<path>::<name>"`` composite,
    which would never match a file path but would inflate the read.

    *paths* narrows the read to "which of **these** are tests". A caller that
    only ever asks ``path in result`` for a known path set — ``get_health`` in
    targeted mode, where the caller named the files — pays for a keyed seek on
    ``uq_graph_node`` instead of the repo-wide read. ``None`` keeps the
    repo-wide answer, which is what the dashboard's production/test *split*
    needs (it partitions a finding list whose paths are not known up front).
    Passing an empty sequence means "no paths asked about" and returns an empty
    set without a query — not the same thing as ``None``.

    Narrow in columns, and now optionally in rows. The repo-wide form seeks on
    ``ix_graph_nodes_repo_type`` (0051); before that index it scanned every node
    the repo has (~27 ms on this 36k-node index, ~8 ms after).

    Degrades to "nothing is test material" when the graph is missing or lags
    the health pass. That is the safe direction: a caller sees the unsplit
    world it saw before, never a production file mislabelled as a test.
    Censused on this repo's index — 1,030 test file nodes, none of them absent
    from ``health_file_metrics``.
    """
    if paths is not None and not paths:
        return set()
    q = select(GraphNode.node_id).where(
        GraphNode.repository_id == repository_id,
        GraphNode.node_type == "file",
        GraphNode.is_test.is_(True),
    )
    if paths is not None:
        # Chunked like ``get_graph_nodes_bulk`` above — a ``module:`` target
        # expands to every file in the module, which on a monorepo is more
        # bind parameters than SQLite's limit allows in one statement.
        out: set[str] = set()
        ordered = list(paths)
        for i in range(0, len(ordered), _BATCH_SIZE):
            batch = ordered[i : i + _BATCH_SIZE]
            rows = await session.execute(q.where(GraphNode.node_id.in_(batch)))
            out |= {row[0] for row in rows.all()}
        return out
    result = await session.execute(q)
    return {row[0] for row in result.all()}


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


async def get_node_degree_counts_bulk(
    session: AsyncSession,
    repository_id: str,
    node_ids: list[str],
) -> dict[str, dict[str, int]]:
    """Return ``node_id -> {in_degree, out_degree}`` for many nodes at once.

    Three queries total instead of three per node (existence, then one grouped
    count per direction). Callers that need degrees for a set of files were
    otherwise forced into an N+1.

    A node absent from the graph is absent from the result, mirroring
    ``get_graph_node`` returning ``None``: consumers distinguish "not a graph
    node" (no entry) from "a node with no edges" (``{0, 0}``), and collapsing
    the two would report isolated files as un-analyzed.
    """
    if not node_ids:
        return {}
    # Batched like ``get_graph_nodes_by_ids`` above: SQLITE_MAX_VARIABLE_NUMBER
    # is 999 on SQLite < 3.32, and the caller's input is unbounded by design (a
    # ``module:`` target expands to every file in the module).
    unique_ids = list(dict.fromkeys(node_ids))
    counts: dict[str, dict[str, int]] = {}
    for i in range(0, len(unique_ids), _BATCH_SIZE):
        existing = await session.execute(
            select(GraphNode.node_id).where(
                GraphNode.repository_id == repository_id,
                GraphNode.node_id.in_(unique_ids[i : i + _BATCH_SIZE]),
            )
        )
        for node_id in existing.scalars().all():
            counts[node_id] = {"in_degree": 0, "out_degree": 0}
    if not counts:
        return {}
    present = list(counts)
    for column, key in (
        (GraphEdge.target_node_id, "in_degree"),
        (GraphEdge.source_node_id, "out_degree"),
    ):
        for i in range(0, len(present), _BATCH_SIZE):
            rows = await session.execute(
                select(column, func.count())
                .where(
                    GraphEdge.repository_id == repository_id,
                    column.in_(present[i : i + _BATCH_SIZE]),
                )
                .group_by(column)
            )
            for node_id, count in rows.all():
                counts[node_id][key] = count or 0
    return counts
