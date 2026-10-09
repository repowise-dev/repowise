"""Fetching the rows the external-dependency summary is folded from.

Nothing here groups, ranks, pages or truncates. Composition is
``repowise.core.analysis.external_systems.summary`` and it is session-free, so
an indexer or a deployment without these tables folds the same records through
the same code and cannot disagree with this surface about what a page omits.

The queries narrow — they select only the columns the fold reads, and join
import edges to resolved external nodes so the fold is handed evidence rather
than the repository's whole import graph.
"""

from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.external_systems import (
    DEFAULT_SUMMARY_LIMIT,
    IMPORT_EDGE_TYPE,
    Scope,
    build_package_summary,
)
from repowise.core.persistence.models import ExternalSystem, GraphEdge, GraphNode
from repowise.server.schemas.external_systems import ExternalSystemsSummaryResponse

SummaryScope = Scope
#: Bound on what a caller may ask for. A page-size ceiling is HTTP validation,
#: so it stays with the route; the default the fold applies is the fold's own.
MAX_SUMMARY_LIMIT = 400

__all__ = [
    "DEFAULT_SUMMARY_LIMIT",
    "MAX_SUMMARY_LIMIT",
    "SummaryScope",
    "build_external_systems_summary",
]


def _declaration_query(repository_id: str):
    return select(
        ExternalSystem.ecosystem,
        ExternalSystem.name,
        ExternalSystem.display_name,
        ExternalSystem.category,
        ExternalSystem.io_kind,
        ExternalSystem.version,
        ExternalSystem.declared_in,
        ExternalSystem.is_dev_dep,
    ).where(ExternalSystem.repository_id == repository_id)


def _link_query(repository_id: str):
    """Every external graph node, named by the package it was resolved to."""
    return (
        select(GraphNode.node_id, ExternalSystem.ecosystem, ExternalSystem.name)
        .join(ExternalSystem, ExternalSystem.id == GraphNode.external_system_id)
        .where(
            GraphNode.repository_id == repository_id,
            GraphNode.external_system_id.is_not(None),
        )
    )


def _import_edge_query(repository_id: str):
    """Import edges that reach a resolved external node, and nothing else."""
    return (
        select(
            GraphEdge.source_node_id.label("source_path"),
            GraphEdge.target_node_id,
        )
        .join(
            GraphNode,
            and_(
                GraphNode.repository_id == repository_id,
                GraphNode.node_id == GraphEdge.target_node_id,
                GraphNode.external_system_id.is_not(None),
            ),
        )
        .where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.edge_type == IMPORT_EDGE_TYPE,
        )
    )


async def build_external_systems_summary(
    session: AsyncSession,
    repository_id: str,
    *,
    scope: SummaryScope = "primary",
    limit: int = DEFAULT_SUMMARY_LIMIT,
    offset: int = 0,
) -> ExternalSystemsSummaryResponse:
    """Serve one bounded package page from three narrowing reads."""
    declarations = (await session.execute(_declaration_query(repository_id))).all()
    links = (await session.execute(_link_query(repository_id))).all()
    import_edges = (await session.execute(_import_edge_query(repository_id))).all()
    summary = build_package_summary(
        declarations,
        links,
        import_edges,
        scope=scope,
        limit=limit,
        offset=offset,
    )
    return ExternalSystemsSummaryResponse.model_validate(summary.as_dict())
