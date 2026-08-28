"""Bounded, aggregate-first reads for one external package's relationships."""

from __future__ import annotations

import json
from typing import Literal

from sqlalchemy import and_, case, distinct, func, literal, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from repowise.core.persistence.models import ExternalSystem, GraphEdge, GraphNode
from repowise.server.schemas.external_systems import (
    ExternalSystemGraphTarget,
    ExternalSystemImportingFile,
    ExternalSystemImportingFilesResponse,
    ExternalSystemRelationshipEdge,
    ExternalSystemRelationshipGraphResponse,
    ExternalSystemRelationshipNode,
)

RelationshipScope = Literal["primary", "all"]
DEFAULT_RELATIONSHIP_NODE_LIMIT = 50
MAX_RELATIONSHIP_NODE_LIMIT = 50
DEFAULT_RELATIONSHIP_EDGE_LIMIT = 200
MAX_RELATIONSHIP_EDGE_LIMIT = 200
DEFAULT_FILE_LIMIT = 25
MAX_FILE_LIMIT = 100
EXTERNAL_TARGET_LIMIT = 20
EVIDENCE_TARGET_LIMIT = 200
_AUXILIARY_PREFIXES = (".claude/worktrees/", "local-stash/")


def _primary_path(path_column):
    return not_(or_(*(path_column.like(f"{prefix}%") for prefix in _AUXILIARY_PREFIXES)))


def _source_scope(scope: RelationshipScope):
    return literal(True) if scope == "all" else _primary_path(GraphEdge.source_node_id)


def _declaration_scope(scope: RelationshipScope):
    return literal(True) if scope == "all" else _primary_path(ExternalSystem.declared_in)


def _identity(package_key: str) -> tuple[str, str]:
    ecosystem, separator, name = package_key.partition(":")
    if not separator or not ecosystem or not name:
        raise ValueError("package_key must contain an ecosystem and package name")
    return ecosystem, name


def _target_basis(node_id: str, package_name: str) -> Literal["exact", "subpath", "mapped"]:
    value = node_id.casefold()
    expected = f"external:{package_name}".casefold()
    if value == expected:
        return "exact"
    if value.startswith(f"{expected}/") or value.startswith(f"{expected}:"):
        return "subpath"
    return "mapped"


def _community_label(meta_json: str | None, community_id: int, top_file: str | None) -> str:
    try:
        label = json.loads(meta_json or "{}").get("label")
    except (json.JSONDecodeError, TypeError, AttributeError):
        label = None
    if isinstance(label, str) and label.strip():
        return label.strip()
    if top_file:
        parts = top_file.replace("\\", "/").split("/")
        return "/".join(parts[:2]) if len(parts) > 1 else parts[0]
    return f"Community {community_id}"


async def _selected_package_targets(
    session: AsyncSession,
    repository_id: str,
    ecosystem: str,
    package_name: str,
    scope: RelationshipScope,
) -> tuple[str, list[str], int] | None:
    """Resolve the same scoped, capped target universe for graph and expansion reads."""
    declared = (
        select(
            ExternalSystem.ecosystem.label("ecosystem"),
            ExternalSystem.name.label("name"),
        )
        .where(
            ExternalSystem.repository_id == repository_id,
            ExternalSystem.ecosystem == ecosystem,
            ExternalSystem.name == package_name,
            _declaration_scope(scope),
        )
        .group_by(ExternalSystem.ecosystem, ExternalSystem.name)
        .subquery("selected_package")
    )
    targets = (
        select(
            declared.c.name,
            GraphNode.node_id,
            func.sum(case((GraphNode.node_id.is_not(None), 1), else_=0))
            .over()
            .label("target_total"),
        )
        .select_from(declared)
        .outerjoin(
            ExternalSystem,
            and_(
                ExternalSystem.repository_id == repository_id,
                ExternalSystem.ecosystem == declared.c.ecosystem,
                ExternalSystem.name == declared.c.name,
            ),
        )
        .outerjoin(
            GraphNode,
            and_(
                GraphNode.repository_id == repository_id,
                GraphNode.external_system_id == ExternalSystem.id,
            ),
        )
        .group_by(declared.c.name, GraphNode.node_id)
        .order_by(GraphNode.node_id)
        .limit(EVIDENCE_TARGET_LIMIT + 1)
    )
    rows = (await session.execute(targets)).all()
    if not rows:
        return None
    target_total = int(rows[0].target_total or 0)
    target_nodes = [row.node_id for row in rows if row.node_id is not None][:EVIDENCE_TARGET_LIMIT]
    return rows[0].name, target_nodes, target_total


async def build_external_system_relationship_graph(
    session: AsyncSession,
    repository_id: str,
    package_key: str,
    *,
    scope: RelationshipScope = "primary",
    node_limit: int = DEFAULT_RELATIONSHIP_NODE_LIMIT,
    edge_limit: int = DEFAULT_RELATIONSHIP_EDGE_LIMIT,
) -> ExternalSystemRelationshipGraphResponse | None:
    """Return one package plus bounded first-party community aggregates in two queries."""
    ecosystem, package_name = _identity(package_key)
    selected_targets = await _selected_package_targets(
        session, repository_id, ecosystem, package_name, scope
    )
    if selected_targets is None:
        return None
    package_name, target_nodes, target_total = selected_targets
    matched_targets = [
        ExternalSystemGraphTarget(
            node_id=node_id,
            match_basis=_target_basis(node_id, package_name),
        )
        for node_id in target_nodes[:EXTERNAL_TARGET_LIMIT]
    ]

    bases = {_target_basis(node_id, package_name) for node_id in target_nodes}
    match_basis = "unresolved" if not bases else next(iter(bases)) if len(bases) == 1 else "mixed"
    evidence_truncated = target_total > len(target_nodes)
    package_node_id = f"package:{package_key}"
    if not target_nodes:
        return ExternalSystemRelationshipGraphResponse(
            package_key=package_key,
            package_name=package_name,
            package_node_id=package_node_id,
            match_basis=match_basis,
            matched_external_nodes=matched_targets,
            matched_external_nodes_total=target_total,
            matched_external_nodes_truncated=target_total > len(matched_targets),
            evidence_target_limit=EVIDENCE_TARGET_LIMIT,
            evidence_truncated=evidence_truncated,
            nodes=[],
            edges=[],
            aggregate_total=0,
            aggregate_returned=0,
            edge_total=0,
            edge_returned=0,
            importing_file_total=0,
            import_edge_total=0,
            node_limit=node_limit,
            edge_limit=edge_limit,
            truncated=evidence_truncated,
            scope=scope,
        )

    source_node = aliased(GraphNode, name="source_node")
    grouped = (
        select(
            source_node.community_id.label("community_id"),
            func.count(GraphEdge.id).label("import_edge_count"),
            func.count(distinct(GraphEdge.source_node_id)).label("importing_file_count"),
            func.min(GraphEdge.source_node_id).label("top_file"),
            func.max(source_node.community_meta_json).label("community_meta_json"),
        )
        .select_from(GraphEdge)
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
            GraphEdge.edge_type == "imports",
            GraphEdge.target_node_id.in_(target_nodes),
            _source_scope(scope),
        )
        .group_by(source_node.community_id)
        .subquery("package_relationship_groups")
    )
    aggregates = select(
        grouped,
        func.count().over().label("aggregate_total"),
        func.sum(grouped.c.import_edge_count).over().label("import_edge_total"),
        func.sum(grouped.c.importing_file_count).over().label("importing_file_total"),
    ).order_by(
        grouped.c.importing_file_count.desc(),
        grouped.c.import_edge_count.desc(),
        grouped.c.community_id,
    )
    aggregate_rows = (await session.execute(aggregates.limit(node_limit + 1))).all()
    returned_rows = aggregate_rows[: min(node_limit, edge_limit)]
    nodes = [
        ExternalSystemRelationshipNode(
            aggregate_key=f"community:{row.community_id}",
            label=_community_label(row.community_meta_json, row.community_id, row.top_file),
            community_id=int(row.community_id),
            importing_file_count=int(row.importing_file_count),
            import_edge_count=int(row.import_edge_count),
            top_file=row.top_file,
        )
        for row in returned_rows
    ]
    edges = [
        ExternalSystemRelationshipEdge(
            source=node.aggregate_key,
            target=package_node_id,
            import_edge_count=node.import_edge_count,
        )
        for node in nodes
    ]
    aggregate_total = int(aggregate_rows[0].aggregate_total or 0) if aggregate_rows else 0
    import_edge_total = int(aggregate_rows[0].import_edge_total or 0) if aggregate_rows else 0
    importing_file_total = int(aggregate_rows[0].importing_file_total or 0) if aggregate_rows else 0
    return ExternalSystemRelationshipGraphResponse(
        package_key=package_key,
        package_name=package_name,
        package_node_id=package_node_id,
        match_basis=match_basis,
        matched_external_nodes=matched_targets,
        matched_external_nodes_total=target_total,
        matched_external_nodes_truncated=target_total > len(matched_targets),
        evidence_target_limit=EVIDENCE_TARGET_LIMIT,
        evidence_truncated=evidence_truncated,
        nodes=nodes,
        edges=edges,
        aggregate_total=aggregate_total,
        aggregate_returned=len(nodes),
        edge_total=aggregate_total,
        edge_returned=len(edges),
        importing_file_total=importing_file_total,
        import_edge_total=import_edge_total,
        node_limit=node_limit,
        edge_limit=edge_limit,
        truncated=aggregate_total > len(nodes) or evidence_truncated,
        scope=scope,
    )


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
    """Return one bounded file page for a selected relationship aggregate."""
    ecosystem, package_name = _identity(package_key)
    prefix, separator, community_value = aggregate_key.partition(":")
    if prefix != "community" or not separator:
        raise ValueError("aggregate_key must identify a graph community")
    try:
        community_id = int(community_value)
    except ValueError as exc:
        raise ValueError("aggregate_key must identify a graph community") from exc

    selected_targets = await _selected_package_targets(
        session, repository_id, ecosystem, package_name, scope
    )
    if selected_targets is None:
        return None
    _, target_nodes, _ = selected_targets
    if not target_nodes:
        return ExternalSystemImportingFilesResponse(
            package_key=package_key,
            aggregate_key=aggregate_key,
            items=[],
            total=0,
            returned=0,
            limit=limit,
            offset=offset,
            truncated=False,
            scope=scope,
        )

    source_node = aliased(GraphNode, name="source_node")
    grouped = (
        select(
            GraphEdge.source_node_id.label("path"),
            source_node.language.label("language"),
            func.count(GraphEdge.id).label("import_edge_count"),
            func.count(distinct(GraphEdge.target_node_id)).label("matched_external_node_count"),
        )
        .select_from(GraphEdge)
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
            GraphEdge.edge_type == "imports",
            GraphEdge.target_node_id.in_(target_nodes),
            source_node.community_id == community_id,
            _source_scope(scope),
        )
        .group_by(GraphEdge.source_node_id, source_node.language)
        .subquery("aggregate_importing_files")
    )
    rows = (
        await session.execute(
            select(grouped, func.count().over().label("total"))
            .order_by(grouped.c.import_edge_count.desc(), grouped.c.path)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    total = int(rows[0].total or 0) if rows else 0
    if not rows and offset:
        total = int((await session.execute(select(func.count()).select_from(grouped))).scalar_one())
    items = [
        ExternalSystemImportingFile(
            path=row.path,
            language=row.language,
            import_edge_count=int(row.import_edge_count),
            matched_external_node_count=int(row.matched_external_node_count),
        )
        for row in rows
    ]
    return ExternalSystemImportingFilesResponse(
        package_key=package_key,
        aggregate_key=aggregate_key,
        items=items,
        total=total,
        returned=len(items),
        limit=limit,
        offset=offset,
        truncated=offset + len(items) < total,
        scope=scope,
    )
