"""Fetching the rows one package's relationship reads are folded from.

Nothing here aggregates. Composition, the caps and the flags that confess them
live in ``repowise.core.analysis.external_systems.relationships``, session-free
so a second consumer folds the same records rather than reimplementing the
bounds.

Both reads resolve the package's target nodes first and fetch only the edges
that reach them, so the query stays proportional to one package rather than to
the repository.
"""

from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from repowise.core.analysis.external_systems import (
    DEFAULT_FILE_LIMIT,
    DEFAULT_RELATIONSHIP_EDGE_LIMIT,
    DEFAULT_RELATIONSHIP_NODE_LIMIT,
    EVIDENCE_TARGET_LIMIT,
    IMPORT_EDGE_TYPE,
    Scope,
    build_importing_files,
    build_relationship_graph,
    resolve_targets,
    split_community_key,
    split_package_key,
)
from repowise.core.persistence.models import ExternalSystem, GraphEdge, GraphNode
from repowise.server.schemas.external_systems import (
    ExternalSystemImportingFilesResponse,
    ExternalSystemRelationshipGraphResponse,
)

RelationshipScope = Scope
#: Ceilings on what a caller may ask for. HTTP validation, so they stay with
#: the route; the defaults the folds apply are the folds' own.
MAX_RELATIONSHIP_NODE_LIMIT = 50
MAX_RELATIONSHIP_EDGE_LIMIT = 200
MAX_FILE_LIMIT = 100

__all__ = [
    "DEFAULT_FILE_LIMIT",
    "DEFAULT_RELATIONSHIP_EDGE_LIMIT",
    "DEFAULT_RELATIONSHIP_NODE_LIMIT",
    "EVIDENCE_TARGET_LIMIT",
    "MAX_FILE_LIMIT",
    "MAX_RELATIONSHIP_EDGE_LIMIT",
    "MAX_RELATIONSHIP_NODE_LIMIT",
    "RelationshipScope",
    "build_external_system_importing_files",
    "build_external_system_relationship_graph",
]


async def _package_records(
    session: AsyncSession, repository_id: str, ecosystem: str, name: str
) -> tuple[list, list]:
    """The one package's declarations and the external nodes it resolved to."""
    declarations = (
        await session.execute(
            select(ExternalSystem.ecosystem, ExternalSystem.name, ExternalSystem.declared_in).where(
                ExternalSystem.repository_id == repository_id,
                ExternalSystem.ecosystem == ecosystem,
                ExternalSystem.name == name,
            )
        )
    ).all()
    links = (
        await session.execute(
            select(GraphNode.node_id, ExternalSystem.ecosystem, ExternalSystem.name)
            .join(ExternalSystem, ExternalSystem.id == GraphNode.external_system_id)
            .where(
                GraphNode.repository_id == repository_id,
                ExternalSystem.repository_id == repository_id,
                ExternalSystem.ecosystem == ecosystem,
                ExternalSystem.name == name,
            )
        )
    ).all()
    return declarations, links


async def _import_edges(session: AsyncSession, repository_id: str, target_nodes: list[str]) -> list:
    """File-sourced import edges reaching the package, with their communities."""
    if not target_nodes:
        return []
    source_node = aliased(GraphNode, name="source_node")
    return (
        await session.execute(
            select(
                GraphEdge.source_node_id.label("source_path"),
                GraphEdge.target_node_id,
                source_node.community_id,
                source_node.community_meta_json,
                source_node.language,
            )
            .join(
                source_node,
                and_(
                    source_node.repository_id == repository_id,
                    source_node.node_id == GraphEdge.source_node_id,
                    source_node.node_type == "file",
                ),
            )
            .where(
                GraphEdge.repository_id == repository_id,
                GraphEdge.edge_type == IMPORT_EDGE_TYPE,
                GraphEdge.target_node_id.in_(target_nodes),
            )
        )
    ).all()


async def build_external_system_relationship_graph(
    session: AsyncSession,
    repository_id: str,
    package_key: str,
    *,
    scope: RelationshipScope = "primary",
    node_limit: int = DEFAULT_RELATIONSHIP_NODE_LIMIT,
    edge_limit: int = DEFAULT_RELATIONSHIP_EDGE_LIMIT,
) -> ExternalSystemRelationshipGraphResponse | None:
    """Serve one package's bounded community graph, or ``None`` if undeclared."""
    ecosystem, name = split_package_key(package_key)
    declarations, links = await _package_records(session, repository_id, ecosystem, name)
    resolved = resolve_targets(declarations, links, ecosystem, name, scope)
    if resolved is None:
        return None
    import_edges = await _import_edges(session, repository_id, resolved[1])
    graph = build_relationship_graph(
        declarations,
        links,
        import_edges,
        package_key,
        scope=scope,
        node_limit=node_limit,
        edge_limit=edge_limit,
    )
    if graph is None:
        return None
    return ExternalSystemRelationshipGraphResponse.model_validate(graph.as_dict())


async def build_external_system_importing_files(
    session: AsyncSession,
    repository_id: str,
    package_key: str,
    aggregate_key: str,
    *,
    scope: RelationshipScope = "primary",
    limit: int = DEFAULT_FILE_LIMIT,
    offset: int = 0,
) -> ExternalSystemImportingFilesResponse | None:
    """Serve one bounded file page for a selected aggregate, or ``None``.

    Both keys are checked before any read, so a malformed one is rejected
    rather than answered as a missing package.
    """
    ecosystem, name = split_package_key(package_key)
    split_community_key(aggregate_key)
    declarations, links = await _package_records(session, repository_id, ecosystem, name)
    resolved = resolve_targets(declarations, links, ecosystem, name, scope)
    if resolved is None:
        return None
    import_edges = await _import_edges(session, repository_id, resolved[1])
    page = build_importing_files(
        declarations,
        links,
        import_edges,
        package_key,
        aggregate_key,
        scope=scope,
        limit=limit,
        offset=offset,
    )
    if page is None:
        return None
    return ExternalSystemImportingFilesResponse.model_validate(page.as_dict())
