"""Read graph edges and roll them up to container/component relations.

The roll-up rules live in :mod:`repowise.core.analysis.c4.relations`; this
module owns the queries and turns each rolled pair into a labelled
:class:`Relation`. External-system edges come from file to ``external:*``
edges whose target resolved to a row in the ``external_systems`` table.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.c4.labels import coupling_strength, relation_label
from repowise.core.analysis.c4.relations import roll_up_edges
from repowise.core.ids import ExternalSystemId, render
from repowise.core.persistence import ExternalSystem, GraphEdge, GraphNode

from .models import Relation


async def load_edges(
    session: AsyncSession, repository_id: str
) -> list[tuple[str, str, str]]:
    """Read every graph edge once, as ``(source, target, type)`` rows.

    Split out so a caller rolling the same edges up several ways — by
    container and by component, say — pays for one read instead of one per
    aggregation.
    """
    result = await session.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id, GraphEdge.edge_type).where(
            GraphEdge.repository_id == repository_id
        )
    )
    return list(result.all())


async def aggregate_relations(
    session: AsyncSession,
    repository_id: str,
    file_to_box: dict[str, str],
    *,
    file_to_external: dict[str, str] | None = None,
    edges: list[tuple[str, str, str]] | None = None,
    include_co_changes: bool = False,
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
    edges:
        Pre-loaded rows from :func:`load_edges`. Omit to read them here.
    include_co_changes:
        Also roll up ``co_changes`` edges, as an overlay on the dependencies.
    """
    if edges is None:
        edges = await load_edges(session, repository_id)
    rolled = roll_up_edges(
        edges,
        file_to_box,
        file_to_external=file_to_external,
        include_co_changes=include_co_changes,
    )

    relations: list[Relation] = []
    for (src_box, tgt_box), (count, types) in rolled.items():
        etypes = tuple(sorted(types))
        relations.append(
            Relation(
                source_id=src_box,
                target_id=tgt_box,
                label=relation_label(etypes),
                edge_count=count,
                edge_types=etypes,
                coupling=coupling_strength(count),
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
    return {node_id: render(ExternalSystemId(name)) for node_id, name in result.all()}
