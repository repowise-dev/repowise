"""Index reads behind components: the file list, derivation inputs, rollup rows."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.components.catalog import Catalog, Component, read_catalog
from repowise.core.components.ownership import Activity, assess_component, build_activity
from repowise.core.components.resolve import (
    ResolvedComponents,
    derive_from_directories,
    derive_from_modules,
    derive_from_services,
    repo_wide_when_flat,
    resolve_components,
)
from repowise.core.components.rollup import rollup_component
from repowise.core.persistence.crud import code_file_rows, indexed_file
from repowise.core.persistence.crud.repository import ANALYSIS_RAN_KEY
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitCommit,
    GitCommitFile,
    GitMetadata,
    GraphNode,
    HealthFileMetric,
    Page,
    Repository,
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
                GraphNode.repository_id == repository_id, indexed_file()
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
        derived = derive_from_modules((m[0], m[1]) for m in modules) or derive_from_directories(
            files
        )
        derived = repo_wide_when_flat(derived, files, Path(repo_root).resolve().name)
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


#: Bound parameters per ``IN`` list, well under SQLite's variable limit.
_CHUNK = 500


async def _rows_for(session: Any, query: Any, path_col: Any, files: list[str]) -> list[Any]:
    """*query* narrowed to *files* in SQL, one chunked ``IN`` at a time."""
    out: list[Any] = []
    for i in range(0, len(files), _CHUNK):
        out.extend((await session.execute(query.where(path_col.in_(files[i : i + _CHUNK])))).all())
    return out


def _stamped(settings_json: str | None, stage: str) -> bool:
    """Whether *settings_json* carries the success stamp of *stage*."""
    try:
        stamps = json.loads(settings_json or "{}").get(ANALYSIS_RAN_KEY)
    except (TypeError, ValueError, AttributeError):
        return False  # an unreadable settings blob proves nothing either way
    return isinstance(stamps, dict) and bool(stamps.get(stage))


async def dead_code_ran(session: Any, repository_id: str) -> bool:
    """Whether dead-code analysis ever persisted a result for the repository.

    The stage stamp proves a run that found nothing; finding rows cover indexes
    written before stamps existed (the rule ``analysis_ran_at`` serves by).
    """
    raw = await session.scalar(
        select(Repository.settings_json).where(Repository.id == repository_id)
    )
    if _stamped(raw, "dead_code"):
        return True
    found = await session.scalar(
        select(DeadCodeFinding.id).where(DeadCodeFinding.repository_id == repository_id).limit(1)
    )
    return found is not None


async def component_rollups(
    session: Any,
    repository_id: str,
    files_by_component: dict[str, list[str]],
    test_files: set[str],
) -> dict[str, dict[str, Any]]:
    """Rollup figures per component name, from one read of each fact table.

    The union of every component's files is read once per table (chunked
    ``IN``), and the dead-code run status once, whatever the batch size; the
    rows are then partitioned by file, so each component folds only its own.
    """
    files = sorted({f for paths in files_by_component.values() for f in paths})
    git = await _rows_for(
        session,
        select(*_GIT_COLUMNS).where(code_file_rows(repository_id)),
        GitMetadata.file_path,
        files,
    )
    health = await _rows_for(
        session,
        select(HealthFileMetric).where(HealthFileMetric.repository_id == repository_id),
        HealthFileMetric.file_path,
        files,
    )
    dead = await _rows_for(
        session,
        select(DeadCodeFinding.file_path, DeadCodeFinding.lines).where(
            DeadCodeFinding.repository_id == repository_id,
            DeadCodeFinding.kind.not_in(excluded_types()),
        ),
        DeadCodeFinding.file_path,
        files,
    )
    ran = await dead_code_ran(session, repository_id) if files else False
    git_rows = {r.file_path: dict(r._mapping) for r in git}
    health_rows = {r[0].file_path: r[0] for r in health}
    dead_by_file: dict[str, list[tuple[str, int | None]]] = {}
    for path, lines in dead:
        dead_by_file.setdefault(path, []).append((path, lines))
    return {
        name: rollup_component(
            paths,
            test_files=test_files,
            git_rows=git_rows,
            health_rows=health_rows,
            dead_rows=[row for p in paths for row in dead_by_file.get(p, ())],
            dead_code_ran=ran,
        )
        for name, paths in files_by_component.items()
    }


async def load_activity(session: Any, repository_id: str, files: list[str]) -> Activity:
    """Recent commit activity on *files*, and every author the index knows."""
    from datetime import timedelta

    from repowise.core.analysis.owners import ACTIVITY_WINDOW_DAYS, as_utc

    authors = (
        await session.execute(
            select(GitCommit.author_name, GitCommit.author_email, func.count()).where(
                GitCommit.repository_id == repository_id
            ).group_by(GitCommit.author_name, GitCommit.author_email)
        )
    ).all()
    # The bound keeps the stored value's form (naive on SQLite), as other window reads do.
    newest = await session.scalar(
        select(func.max(GitCommit.committed_at)).where(GitCommit.repository_id == repository_id)
    )
    recent: list[Any] = []
    if newest is not None and files:
        query = (
            select(
                GitCommitFile.file_path,
                GitCommit.sha,
                GitCommit.author_name,
                GitCommit.author_email,
            )
            .join(
                GitCommit,
                (GitCommit.repository_id == GitCommitFile.repository_id)
                & (GitCommit.sha == GitCommitFile.sha),
            )
            .where(
                GitCommitFile.repository_id == repository_id,
                GitCommit.committed_at >= newest - timedelta(days=ACTIVITY_WINDOW_DAYS),
            )
        )
        recent = await _rows_for(session, query, GitCommitFile.file_path, files)
    # One pair per commit, so the resolver's counts match every other people figure.
    pairs = [(name, email) for name, email, n in authors for _ in range(n)]
    return build_activity(pairs, [tuple(r) for r in recent], as_utc(newest))


async def component_ownership(
    session: Any,
    repository_id: str,
    repo_root: Path | str,
    components: list[tuple[Component, list[str]]],
    bus_factors: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The ``ownership`` block per component name (see :mod:`.ownership`), and
    the team directory's read problems."""
    from repowise.core.ownership.directory import team_directories

    declared = team_directories.get().load(Path(repo_root))
    files = sorted({f for _c, paths in components for f in paths})
    activity = await load_activity(session, repository_id, files)
    out = {
        comp.name: assess_component(
            comp, paths, declared, activity, (bus_factors or {}).get(comp.name)
        )
        for comp, paths in components
    }
    return out, list(declared.issues)
