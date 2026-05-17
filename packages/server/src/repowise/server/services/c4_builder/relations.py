"""Aggregate file→file graph edges up to container/component relations.

The graph stores edges between individual files (and symbols). For C4
diagrams we need edges between higher-level boxes — between containers at
L2, between components at L3. This module does that aggregation:

    1. Load all ``graph_edges`` whose source AND target are file-level.
    2. Map each endpoint to its container (L2) or component (L3) using the
       ``file_index`` produced by :mod:`.containers` / :mod:`.components`.
    3. Group by (source_box, target_box, edge_type) and sum counts.
    4. Drop self-loops — an edge from a container to itself is not useful
       to a viewer.

External-system edges are produced from file→``external:*`` edges where
the target's ``external_system_id`` resolved to a row in the
``external_systems`` table.
"""

from __future__ import annotations

from collections import defaultdict

from repowise.core.persistence import ExternalSystem, GraphEdge, GraphNode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Relation


async def aggregate_relations(
    session: AsyncSession,
    repository_id: str,
    file_to_box: dict[str, str],
    *,
    file_to_external: dict[str, str] | None = None,
) -> list[Relation]:
    """Roll file→file edges up to box→box edges.

    Parameters
    ----------
    file_to_box:
        Map of file path → owning container/component id.
    file_to_external:
        Map of ``external:*`` node_id → external-system id (e.g., ``ext:react``).
        When provided, edges whose target is an external node are also
        emitted (as box → external).
    """
    file_to_external = file_to_external or {}
    result = await session.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id, GraphEdge.edge_type)
        .where(GraphEdge.repository_id == repository_id)
    )

    counts: dict[tuple[str, str], int] = defaultdict(int)
    types: dict[tuple[str, str], set[str]] = defaultdict(set)

    for src, tgt, etype in result.all():
        src_box = file_to_box.get(src)
        if src_box is None:
            continue
        if tgt in file_to_box:
            tgt_box = file_to_box[tgt]
        elif tgt in file_to_external:
            tgt_box = file_to_external[tgt]
        else:
            continue
        if src_box == tgt_box:
            continue
        key = (src_box, tgt_box)
        counts[key] += 1
        types[key].add(etype or "imports")

    relations: list[Relation] = []
    for (src_box, tgt_box), count in counts.items():
        etypes = tuple(sorted(types[(src_box, tgt_box)]))
        label = etypes[0] if len(etypes) == 1 else f"{etypes[0]} +{len(etypes) - 1}"
        relations.append(
            Relation(
                source_id=src_box,
                target_id=tgt_box,
                label=label,
                edge_count=count,
                edge_types=etypes,
            )
        )
    relations.sort(key=lambda r: (-r.edge_count, r.source_id, r.target_id))
    return relations


async def external_node_to_system_id(
    session: AsyncSession,
    repository_id: str,
) -> dict[str, str]:
    """Map ``external:*`` graph_node ids to ``ext:<name>`` view ids.

    Only nodes whose ``external_system_id`` resolved to an ExternalSystem
    row are returned — anything else stays unmapped and is dropped by the
    aggregator (it would be a noisy, unlabeled box otherwise).
    """
    result = await session.execute(
        select(GraphNode.node_id, ExternalSystem.name)
        .join(ExternalSystem, GraphNode.external_system_id == ExternalSystem.id)
        .where(GraphNode.repository_id == repository_id)
    )
    return {node_id: f"ext:{name}" for node_id, name in result.all()}
