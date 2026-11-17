"""Callers that reach an implementation through the base method it answers for.

A call written against an interface (or a base class) resolves to the base's
method, so an implementation injected by a container reads as called only by
its own unit test. Ingestion already joins each base method to the
implementations that can answer for it with a ``dispatches_to`` edge; this
reads that edge one hop back and returns the calls into the base. One hop
only, never a walk up the hierarchy: ``dispatches_to`` already spans it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.batches import chunked
from repowise.core.persistence.models import GraphEdge

#: Same floor as the direct call rows; ``dispatches_to`` is emitted at 0.75.
MIN_CONFIDENCE = 0.7

#: The ``via`` marker on every row this module produces.
VIA_INTERFACE = "interface"


async def interface_call_edges(
    session: AsyncSession, repo_id: str, impl_ids: list[str]
) -> list[tuple[GraphEdge, str]]:
    """``(call edge, base method id)`` for calls into a base dispatching to *impl_ids*.

    Calls made from inside *impl_ids* are left out: an implementation going
    through its own interface is not an outside caller.
    """
    impls = set(impl_ids)
    bases: set[str] = set()
    for batch in chunked(sorted(impls)):
        res = await session.execute(
            select(GraphEdge.source_node_id).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.target_node_id.in_(batch),
                GraphEdge.edge_type == "dispatches_to",
                GraphEdge.confidence >= MIN_CONFIDENCE,
            )
        )
        bases.update(row[0] for row in res.all())
    bases -= impls
    out: list[tuple[GraphEdge, str]] = []
    for batch in chunked(sorted(bases)):
        res = await session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.target_node_id.in_(batch),
                GraphEdge.edge_type == "calls",
                GraphEdge.confidence >= MIN_CONFIDENCE,
            )
        )
        out.extend(
            (edge, edge.target_node_id)
            for edge in res.scalars().all()
            if edge.source_node_id not in impls
        )
    return out
