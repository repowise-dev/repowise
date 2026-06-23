"""CRUD operations for the behavior-oriented domain graph.

Full-replace upserts mirroring the knowledge-graph CRUD (delete-then-insert per
repo), so a re-index writes a clean graph and the call is safe to repeat. Row
shapes are the dicts produced by
``repowise.core.generation.domain_graph.flatten_nodes`` /
``flatten_edges``.
"""

from __future__ import annotations

import json

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DomainGraphEdge, DomainGraphNode


async def upsert_domain_graph_nodes(
    session: AsyncSession, repo_id: str, nodes: list[dict]
) -> None:
    """Replace all domain-graph nodes for a repo (delete + bulk insert)."""
    await session.execute(
        delete(DomainGraphNode).where(DomainGraphNode.repository_id == repo_id)
    )
    for i, node in enumerate(nodes):
        session.add(
            DomainGraphNode(
                repository_id=repo_id,
                node_id=node["node_id"],
                kind=node["kind"],
                name=node.get("name", ""),
                summary=node.get("summary", ""),
                parent_id=node.get("parent_id"),
                step_order=node.get("step_order"),
                implements_json=json.dumps(node.get("implements", [])),
                display_order=node.get("display_order", i),
                page_title=node.get("page_title", ""),
                page_content=node.get("page_content", ""),
            )
        )
    await session.flush()


async def upsert_domain_graph_edges(
    session: AsyncSession, repo_id: str, edges: list[dict]
) -> None:
    """Replace all domain-graph edges for a repo (delete + bulk insert)."""
    await session.execute(
        delete(DomainGraphEdge).where(DomainGraphEdge.repository_id == repo_id)
    )
    for edge in edges:
        session.add(
            DomainGraphEdge(
                repository_id=repo_id,
                source_node_id=edge["source_node_id"],
                target_node_id=edge["target_node_id"],
                edge_type=edge["edge_type"],
                weight=float(edge.get("weight", 0.0)),
            )
        )
    await session.flush()


async def get_domain_graph_nodes(
    session: AsyncSession, repo_id: str
) -> list[DomainGraphNode]:
    """Fetch all domain-graph nodes for a repo, ordered for stable display."""
    result = await session.execute(
        select(DomainGraphNode)
        .where(DomainGraphNode.repository_id == repo_id)
        .order_by(DomainGraphNode.kind, DomainGraphNode.display_order)
    )
    return list(result.scalars())


async def get_domain_graph_edges(
    session: AsyncSession, repo_id: str
) -> list[DomainGraphEdge]:
    """Fetch all domain-graph edges for a repo."""
    result = await session.execute(
        select(DomainGraphEdge).where(DomainGraphEdge.repository_id == repo_id)
    )
    return list(result.scalars())
