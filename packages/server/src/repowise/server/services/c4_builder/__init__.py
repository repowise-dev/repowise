"""C4 diagram builder service.

Public API
----------
``build_l1(session, repo)`` returns System Context (system + external systems).
``build_l2(session, repo)`` returns Container view + cross-container edges
    + edges to external systems.
``build_l3(session, repo, container_id)`` returns Component view for one
    container + its in/out edges to other containers and external systems.

All three reuse the same persisted graph (``graph_nodes``, ``graph_edges``)
and the ``external_systems`` table populated during ingestion. No on-disk
re-scan is performed at request time.
"""

from __future__ import annotations

from collections import defaultdict

from repowise.core.persistence import ExternalSystem, Repository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .components import detect_components
from .containers import container_id, detect_containers
from .models import (
    C4L1,
    C4L2,
    C4L3,
    Component,
    Container,
    ExternalSystemView,
    Person,
    Relation,
    System,
)
from .relations import aggregate_relations, external_node_to_system_id

__all__ = [
    "C4L1",
    "C4L2",
    "C4L3",
    "Component",
    "Container",
    "ExternalSystemView",
    "Person",
    "Relation",
    "System",
    "build_architecture_view",
    "build_l1",
    "build_l2",
    "build_l3",
    "container_id",
    "load_repo",
]


async def load_repo(session: AsyncSession, repo_id: str) -> Repository | None:
    result = await session.execute(select(Repository).where(Repository.id == repo_id))
    return result.scalar_one_or_none()


async def _external_views(
    session: AsyncSession, repo_id: str
) -> tuple[list[ExternalSystemView], dict[str, str]]:
    """Return the deduplicated list of ExternalSystemView (one per name) and
    a map from each name → ``ext:<name>`` id.

    Multi-manifest deps collapse: we pick the highest-category entry
    (framework > service > tool > library) so the UI shows the most
    interesting label.
    """
    result = await session.execute(
        select(ExternalSystem).where(ExternalSystem.repository_id == repo_id)
    )
    rows = list(result.scalars())
    priority = {"framework": 3, "service": 2, "tool": 1, "library": 0}
    by_name: dict[str, ExternalSystem] = {}
    for row in rows:
        prev = by_name.get(row.name)
        if prev is None or priority.get(row.category, 0) > priority.get(prev.category, 0):
            by_name[row.name] = row

    views: list[ExternalSystemView] = []
    name_to_id: dict[str, str] = {}
    for name in sorted(by_name):
        row = by_name[name]
        view_id = f"ext:{name}"
        views.append(
            ExternalSystemView(
                id=view_id,
                name=name,
                display_name=row.display_name or name,
                category=row.category,
                ecosystem=row.ecosystem,
                version=row.version,
                io_kind=row.io_kind,
            )
        )
        name_to_id[name] = view_id
    return views, name_to_id


def _system_for(repo: Repository | None, repo_id: str) -> System:
    if repo is None:
        return System(id=f"sys:{repo_id}", name=repo_id)
    return System(id=f"sys:{repo.id}", name=repo.name, description="")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


async def build_l1(session: AsyncSession, repo_id: str) -> C4L1:
    repo = await load_repo(session, repo_id)
    system = _system_for(repo, repo_id)
    externals, _ = await _external_views(session, repo_id)

    # Default actor — Phase 1 of issue #203 keeps a single generic User.
    person = Person(id="person:user", name="User", description="Uses the system")

    relations: list[Relation] = [
        Relation(source_id=person.id, target_id=system.id, label="uses")
    ]
    for ext in externals:
        relations.append(
            Relation(source_id=system.id, target_id=ext.id, label=ext.category)
        )
    return C4L1(
        system=system,
        people=[person],
        external_systems=externals,
        relations=relations,
    )


async def build_l2(session: AsyncSession, repo_id: str) -> C4L2:
    containers = await detect_containers(session, repo_id)
    externals, _ = await _external_views(session, repo_id)

    file_to_container = await _file_to_container_map(session, repo_id, containers)
    file_to_external = await external_node_to_system_id(session, repo_id)

    relations = await aggregate_relations(
        session,
        repo_id,
        file_to_box=file_to_container,
        file_to_external=file_to_external,
    )

    # Only surface externals actually depended on by at least one container.
    used_external_ids = {r.target_id for r in relations if r.target_id.startswith("ext:")}
    pruned_externals = [e for e in externals if e.id in used_external_ids]
    return C4L2(containers=containers, external_systems=pruned_externals, relations=relations)


async def build_l3(session: AsyncSession, repo_id: str, container_id_value: str) -> C4L3 | None:
    """Return L3 view for one container, or ``None`` if it doesn't exist."""
    containers = await detect_containers(session, repo_id)
    container = next((c for c in containers if c.id == container_id_value), None)
    if container is None:
        return None

    components, in_container_index = await detect_components(
        session, repo_id, container.path
    )

    # Files outside this container map to their owning container so cross-
    # container edges show as component → other-container.
    file_to_box: dict[str, str] = dict(in_container_index)
    other_containers_index = await _file_to_container_map(
        session,
        repo_id,
        [c for c in containers if c.id != container.id],
    )
    file_to_box.update(other_containers_index)

    file_to_external = await external_node_to_system_id(session, repo_id)
    relations = await aggregate_relations(
        session,
        repo_id,
        file_to_box=file_to_box,
        file_to_external=file_to_external,
    )

    relevant_box_ids = {c.id for c in components} | {container.id}
    relations = [
        r for r in relations
        if r.source_id in relevant_box_ids or r.target_id in relevant_box_ids
    ]

    externals_all, _ = await _external_views(session, repo_id)
    used_external_ids = {r.target_id for r in relations if r.target_id.startswith("ext:")}
    used_external_ids |= {r.source_id for r in relations if r.source_id.startswith("ext:")}
    externals = [e for e in externals_all if e.id in used_external_ids]

    return C4L3(
        container=container,
        components=components,
        external_systems=externals,
        relations=relations,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _file_to_container_map(
    session: AsyncSession,
    repo_id: str,
    containers: list[Container],
) -> dict[str, str]:
    """Map every file path to its owning container id, longest-prefix wins."""
    from repowise.core.persistence import GraphNode

    result = await session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "file",
        )
    )
    paths = [row[0] for row in result.all()]
    sorted_containers = sorted(containers, key=lambda c: len(c.path), reverse=True)
    out: dict[str, str] = {}
    bucket: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        for c in sorted_containers:
            if not c.path:
                bucket[c.id].append(path)
                out[path] = c.id
                break
            if path == c.path or path.startswith(c.path + "/"):
                bucket[c.id].append(path)
                out[path] = c.id
                break
    return out
