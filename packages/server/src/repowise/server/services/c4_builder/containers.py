"""Detect C4 L2 containers for an indexed repository.

Strategy
--------
Containers are derived from the persisted ``external_systems`` table — every
``declared_in`` path points at a manifest file, and that manifest's parent
directory is a container root. This works for both monorepos (many manifests)
and single-package repos (one root manifest).

If no manifests were found (rare — exotic stacks), we fall back to the set
of top-level directories that hold indexed files.

Each container's ``language``, ``file_count``, and ``symbol_count`` are
aggregated from ``graph_nodes`` rows whose ``node_id`` (file path) lives
inside the container directory.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable

from repowise.core.persistence import ExternalSystem, GraphNode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Container


async def detect_containers(
    session: AsyncSession, repository_id: str
) -> list[Container]:
    """Return the list of containers detected for ``repository_id``.

    Stable ordering by ``path`` so the C4 diagram doesn't reshuffle between
    requests.
    """
    container_roots = await _container_roots_from_manifests(session, repository_id)
    file_nodes = await _file_nodes(session, repository_id)

    if not container_roots:
        container_roots = _top_level_dirs(node.node_id for node in file_nodes)

    # Sort longest path first so nested manifests win over their parents.
    sorted_roots = sorted(container_roots, key=len, reverse=True)

    grouped: dict[str, list[GraphNode]] = defaultdict(list)
    for node in file_nodes:
        root = _match_container(node.node_id, sorted_roots)
        if root is not None:
            grouped[root].append(node)

    containers: list[Container] = []
    for root in sorted(grouped):
        nodes = grouped[root]
        lang = _dominant_language(nodes)
        name = root.split("/")[-1] if root else "."
        containers.append(
            Container(
                id=container_id(root),
                name=name or ".",
                path=root,
                language=lang,
                file_count=len(nodes),
                symbol_count=sum(n.symbol_count or 0 for n in nodes),
            )
        )
    return containers


def container_id(path: str) -> str:
    """Stable id for a container path. Edge sources/targets use this form."""
    return f"pkg:{path}" if path else "pkg:."


async def _container_roots_from_manifests(
    session: AsyncSession, repository_id: str
) -> set[str]:
    """Return the unique parent directories of every declared manifest."""
    result = await session.execute(
        select(ExternalSystem.declared_in).where(
            ExternalSystem.repository_id == repository_id
        )
    )
    roots: set[str] = set()
    for (declared_in,) in result.all():
        if not declared_in:
            continue
        parent = declared_in.rsplit("/", 1)[0] if "/" in declared_in else ""
        roots.add(parent)
    return roots


async def _file_nodes(session: AsyncSession, repository_id: str) -> list[GraphNode]:
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_type == "file",
        )
    )
    return list(result.scalars())


def _top_level_dirs(paths: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for path in paths:
        if "/" in path:
            out.add(path.split("/", 1)[0])
        else:
            out.add("")
    return out


def _match_container(file_path: str, sorted_roots: list[str]) -> str | None:
    """Return the deepest container root containing ``file_path``."""
    for root in sorted_roots:
        if not root:
            return root  # root manifest catches everything that isn't claimed
        if file_path == root or file_path.startswith(root + "/"):
            return root
    return None


def _dominant_language(nodes: list[GraphNode]) -> str:
    counter: Counter[str] = Counter(
        n.language for n in nodes if n.language and n.language != "unknown"
    )
    return counter.most_common(1)[0][0] if counter else "unknown"
