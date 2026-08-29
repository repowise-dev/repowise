"""Bounded read models for declared packages and persisted graph usage."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from sqlalchemy import and_, case, distinct, func, literal, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import ExternalSystem, GraphEdge, GraphNode
from repowise.server.schemas.external_systems import (
    ExternalSystemsSummaryResponse,
    ExternalSystemSummaryEntry,
)

SummaryScope = Literal["primary", "all"]
DEFAULT_SUMMARY_LIMIT = 200
MAX_SUMMARY_LIMIT = 400
SUMMARY_VERSION_LIMIT = 5
PRIMARY_AUXILIARY_PREFIXES = (".claude/worktrees/", "local-stash/")


def _primary_path_predicate(path_column):
    return not_(or_(*(path_column.like(f"{prefix}%") for prefix in PRIMARY_AUXILIARY_PREFIXES)))


def _scope_predicate(scope: SummaryScope):
    if scope == "all":
        return literal(True)
    return _primary_path_predicate(ExternalSystem.declared_in)


def _package_query(repository_id: str, scope: SummaryScope):
    runtime = func.max(case((ExternalSystem.is_dev_dep.is_(False), 1), else_=0))
    dev = func.max(case((ExternalSystem.is_dev_dep.is_(True), 1), else_=0))
    return (
        select(
            ExternalSystem.ecosystem.label("ecosystem"),
            ExternalSystem.name.label("name"),
            func.max(ExternalSystem.display_name).label("display_name"),
            func.min(ExternalSystem.category).label("category"),
            func.min(ExternalSystem.io_kind).label("io_kind"),
            runtime.label("runtime_declared"),
            dev.label("dev_declared"),
            func.count(ExternalSystem.id).label("declaration_count"),
            func.count(distinct(ExternalSystem.declared_in)).label("manifest_count"),
        )
        .where(
            ExternalSystem.repository_id == repository_id,
            _scope_predicate(scope),
        )
        .group_by(ExternalSystem.ecosystem, ExternalSystem.name)
        .subquery("declared_packages")
    )


def _usage_query(repository_id: str, scope: SummaryScope):
    edge_scope = (
        literal(True) if scope == "all" else _primary_path_predicate(GraphEdge.source_node_id)
    )
    return (
        select(
            ExternalSystem.ecosystem.label("ecosystem"),
            ExternalSystem.name.label("name"),
            func.count(distinct(GraphNode.node_id)).label("external_node_count"),
            func.count(GraphEdge.id).label("import_edge_count"),
            func.count(distinct(GraphEdge.source_node_id)).label("importing_file_count"),
        )
        .select_from(GraphNode)
        .join(ExternalSystem, ExternalSystem.id == GraphNode.external_system_id)
        .outerjoin(
            GraphEdge,
            and_(
                GraphEdge.repository_id == repository_id,
                GraphEdge.target_node_id == GraphNode.node_id,
                GraphEdge.edge_type == "imports",
                edge_scope,
            ),
        )
        .where(
            GraphNode.repository_id == repository_id,
            GraphNode.external_system_id.is_not(None),
        )
        .group_by(ExternalSystem.ecosystem, ExternalSystem.name)
        .subquery("package_usage")
    )


async def _versions_for_page(
    session: AsyncSession,
    repository_id: str,
    scope: SummaryScope,
    identities: list[tuple[str, str]],
) -> dict[tuple[str, str], tuple[list[str], int]]:
    """Fetch at most ``SUMMARY_VERSION_LIMIT`` values for each page item."""
    if not identities:
        return {}
    identity_filter = or_(
        *(
            and_(ExternalSystem.ecosystem == ecosystem, ExternalSystem.name == name)
            for ecosystem, name in identities
        )
    )
    distinct_versions = (
        select(
            ExternalSystem.ecosystem.label("ecosystem"),
            ExternalSystem.name.label("name"),
            ExternalSystem.version.label("version"),
        )
        .where(
            ExternalSystem.repository_id == repository_id,
            _scope_predicate(scope),
            ExternalSystem.version.is_not(None),
            identity_filter,
        )
        .group_by(ExternalSystem.ecosystem, ExternalSystem.name, ExternalSystem.version)
        .subquery("distinct_versions")
    )
    ranked = select(
        distinct_versions,
        func.row_number()
        .over(
            partition_by=(distinct_versions.c.ecosystem, distinct_versions.c.name),
            order_by=distinct_versions.c.version,
        )
        .label("version_rank"),
        func.count()
        .over(partition_by=(distinct_versions.c.ecosystem, distinct_versions.c.name))
        .label("versions_total"),
    ).subquery("ranked_versions")
    rows = (
        await session.execute(select(ranked).where(ranked.c.version_rank <= SUMMARY_VERSION_LIMIT))
    ).all()
    values: dict[tuple[str, str], list[str]] = defaultdict(list)
    totals: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row.ecosystem, row.name)
        values[key].append(row.version)
        totals[key] = int(row.versions_total)
    return {key: (versions, totals[key]) for key, versions in values.items()}


async def build_external_systems_summary(
    session: AsyncSession,
    repository_id: str,
    *,
    scope: SummaryScope = "primary",
    limit: int = DEFAULT_SUMMARY_LIMIT,
    offset: int = 0,
) -> ExternalSystemsSummaryResponse:
    """Return a bounded package page in constant SQL statements, independent of N."""
    packages = _package_query(repository_id, scope)
    usage = _usage_query(repository_id, scope)
    node_count = func.coalesce(usage.c.external_node_count, 0)
    edge_count = func.coalesce(usage.c.import_edge_count, 0)
    file_count = func.coalesce(usage.c.importing_file_count, 0)
    joined = packages.outerjoin(
        usage,
        and_(
            usage.c.ecosystem == packages.c.ecosystem,
            usage.c.name == packages.c.name,
        ),
    )
    summaries = select(
        packages,
        node_count.label("external_node_count"),
        edge_count.label("import_edge_count"),
        file_count.label("importing_file_count"),
        func.count().over().label("total_packages"),
        func.sum(packages.c.declaration_count).over().label("total_declarations"),
        func.sum(packages.c.runtime_declared).over().label("runtime_packages"),
        func.sum(
            case(
                (
                    and_(
                        packages.c.dev_declared == 1,
                        packages.c.runtime_declared == 0,
                    ),
                    1,
                ),
                else_=0,
            )
        )
        .over()
        .label("dev_only_packages"),
        func.sum(case((edge_count > 0, 1), else_=0)).over().label("observed_packages"),
        func.sum(case((node_count > 0, 1), else_=0)).over().label("linked_packages"),
        func.sum(case((and_(node_count > 0, edge_count == 0), 1), else_=0))
        .over()
        .label("linked_without_imports"),
    ).select_from(joined)
    ordered_summaries = summaries.order_by(
        packages.c.runtime_declared.desc(),
        file_count.desc(),
        func.lower(packages.c.name),
        packages.c.ecosystem,
    )
    page_rows = (await session.execute(ordered_summaries.limit(limit).offset(offset))).all()
    manifest_count = (
        select(func.count(distinct(ExternalSystem.declared_in)))
        .where(
            ExternalSystem.repository_id == repository_id,
            _scope_predicate(scope),
        )
        .scalar_subquery()
    )
    excluded_declarations = (
        select(func.count(ExternalSystem.id))
        .where(
            ExternalSystem.repository_id == repository_id,
            ~_scope_predicate("primary"),
        )
        .scalar_subquery()
    )
    metadata_rows = (
        await session.execute(
            select(
                ExternalSystem.ecosystem,
                manifest_count.label("manifest_count"),
                excluded_declarations.label("excluded_declarations"),
            )
            .where(
                ExternalSystem.repository_id == repository_id,
                _scope_predicate(scope),
            )
            .group_by(ExternalSystem.ecosystem)
        )
    ).all()
    excluded = 0
    if scope == "primary" and metadata_rows:
        excluded = int(metadata_rows[0].excluded_declarations or 0)
    elif scope == "primary":
        # Grouped totals have no row when every declaration is auxiliary.
        excluded = int(
            (
                await session.execute(
                    select(func.count(ExternalSystem.id)).where(
                        ExternalSystem.repository_id == repository_id,
                        ~_scope_predicate("primary"),
                    )
                )
            ).scalar_one()
        )
    totals_row = page_rows[0] if page_rows else None
    if totals_row is None and offset:
        totals_row = (await session.execute(ordered_summaries.limit(1))).first()
    identities = [(row.ecosystem, row.name) for row in page_rows]
    versions = await _versions_for_page(session, repository_id, scope, identities)
    items: list[ExternalSystemSummaryEntry] = []
    for row in page_rows:
        package_versions, versions_total = versions.get((row.ecosystem, row.name), ([], 0))
        resolved_nodes = int(row.external_node_count or 0)
        items.append(
            ExternalSystemSummaryEntry(
                package_key=f"{row.ecosystem}:{row.name}",
                name=row.name,
                display_name=row.display_name or row.name,
                ecosystem=row.ecosystem,
                category=row.category,
                io_kind=row.io_kind,
                runtime_declared=bool(row.runtime_declared),
                dev_declared=bool(row.dev_declared),
                declaration_count=int(row.declaration_count),
                manifest_count=int(row.manifest_count),
                versions=package_versions,
                versions_total=versions_total,
                versions_truncated=versions_total > len(package_versions),
                multiple_versions=versions_total > 1,
                external_node_count=resolved_nodes,
                import_edge_count=int(row.import_edge_count or 0),
                importing_file_count=int(row.importing_file_count or 0),
                link_state="linked" if resolved_nodes else "unlinked",
            )
        )
    total_packages = int(totals_row.total_packages or 0) if totals_row else 0
    linked_packages = int(totals_row.linked_packages or 0) if totals_row else 0
    first_metadata = metadata_rows[0] if metadata_rows else None
    return ExternalSystemsSummaryResponse(
        items=items,
        returned=len(items),
        total_packages=total_packages,
        limit=limit,
        offset=offset,
        truncated=offset + len(items) < total_packages,
        scope=scope,
        excluded_declarations=excluded,
        total_declarations=int(totals_row.total_declarations or 0) if totals_row else 0,
        runtime_packages=int(totals_row.runtime_packages or 0) if totals_row else 0,
        dev_only_packages=int(totals_row.dev_only_packages or 0) if totals_row else 0,
        observed_packages=int(totals_row.observed_packages or 0) if totals_row else 0,
        linked_packages=linked_packages,
        unlinked_packages=total_packages - linked_packages,
        linked_without_imports=(int(totals_row.linked_without_imports or 0) if totals_row else 0),
        ecosystems=sorted(row.ecosystem for row in metadata_rows),
        manifest_count=(int(first_metadata.manifest_count or 0) if first_metadata else 0),
    )
