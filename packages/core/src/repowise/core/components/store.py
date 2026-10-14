"""Index reads behind components: the file list, derivation inputs, rollup rows."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.components.catalog import Catalog, read_catalog
from repowise.core.components.resolve import (
    ResolvedComponents,
    derive_from_directories,
    derive_from_modules,
    derive_from_services,
    resolve_components,
)
from repowise.core.components.rollup import rollup_component
from repowise.core.persistence.crud import code_file_rows
from repowise.core.persistence.crud.analysis.shown import ungated
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitMetadata,
    GraphNode,
    HealthFileMetric,
    Page,
)


@dataclass
class RepoComponents:
    catalog: Catalog
    resolved: ResolvedComponents
    test_files: set[str] = field(default_factory=set)


def services_from_system_graph(
    graph: dict[str, Any] | None, alias: str | None
) -> list[tuple[str, str]]:
    """``(name, service_path)`` for *alias*'s service nodes in a system-graph dict."""
    if not graph or not alias:
        return []
    return [
        (str(n.get("name") or n["service_path"]), str(n["service_path"]))
        for n in graph.get("nodes", [])
        if isinstance(n, dict) and n.get("repo") == alias and n.get("service_path")
    ]


def workspace_services(repo_root: Path) -> list[tuple[str, str]]:
    """Services of *repo_root* from the enclosing workspace's system graph, if any."""
    from repowise.core.workspace.config import WorkspaceConfig, find_workspace_root
    from repowise.core.workspace.system_graph import load_system_graph

    ws_root = find_workspace_root(repo_root)
    if ws_root is None:
        return []
    try:
        config = WorkspaceConfig.load(ws_root)
    except Exception:
        return []
    root = repo_root.resolve()
    alias = next((r.alias for r in config.repos if (ws_root / r.path).resolve() == root), None)
    graph = load_system_graph(ws_root)
    return services_from_system_graph(graph.to_dict() if graph else None, alias)


async def load_repo_components(
    session: Any,
    repository_id: str,
    repo_root: Path | str,
    *,
    services: Iterable[tuple[str, str]] = (),
    is_excluded: Callable[[str], bool] | None = None,
) -> RepoComponents:
    """Declared and derived components for one indexed repository."""
    catalog = read_catalog(repo_root)
    rows = (
        await session.execute(
            select(GraphNode.node_id, GraphNode.is_test).where(
                GraphNode.repository_id == repository_id, GraphNode.node_type == "file"
            )
        )
    ).all()
    rows = [r for r in rows if not (is_excluded and is_excluded(r[0]))]
    files = [r[0] for r in rows]

    derived = derive_from_services(services)
    if not derived:
        modules = (
            await session.execute(
                select(Page.target_path, Page.title).where(
                    Page.repository_id == repository_id,
                    Page.page_type == "module_page",
                    Page.freshness_status != "tombstone",
                )
            )
        ).all()
        derived = derive_from_modules((m[0], m[1]) for m in modules)
    if not derived:
        derived = derive_from_directories(files)
    return RepoComponents(
        catalog=catalog,
        resolved=resolve_components(catalog.components, derived, files),
        test_files={r[0] for r in rows if r[1]},
    )


_GIT_COLUMNS = (
    GitMetadata.file_path,
    GitMetadata.is_hotspot,
    GitMetadata.temporal_hotspot_score,
    GitMetadata.bus_factor,
    GitMetadata.primary_owner_name,
    GitMetadata.primary_owner_email,
    GitMetadata.top_authors_json,
)


async def component_rollup(
    session: Any, repository_id: str, files: list[str], test_files: set[str]
) -> dict[str, Any]:
    """The rollup figures for one component's *files*."""
    wanted = set(files)
    git = (await session.execute(select(*_GIT_COLUMNS).where(code_file_rows(repository_id)))).all()
    git_rows = {r.file_path: dict(r._mapping) for r in git if r.file_path in wanted}
    health = (
        await session.execute(
            select(HealthFileMetric).where(HealthFileMetric.repository_id == repository_id)
        )
    ).scalars()
    health_rows = {r.file_path: r for r in health if r.file_path in wanted}
    dead = (
        await session.execute(
            select(DeadCodeFinding.file_path, DeadCodeFinding.lines).where(
                DeadCodeFinding.repository_id == repository_id,
                DeadCodeFinding.kind.not_in(excluded_types()),
                ungated("dead_code", DeadCodeFinding.file_path, DeadCodeFinding.kind),
            )
        )
    ).all()
    return rollup_component(
        files,
        test_files=test_files,
        git_rows=git_rows,
        health_rows=health_rows,
        dead_rows=[(r[0], r[1]) for r in dead if r[0] in wanted],
    )
