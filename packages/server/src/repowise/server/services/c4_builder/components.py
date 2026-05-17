"""Detect C4 L3 components inside a single container.

A component is a top-level subdirectory of the container. Files that sit
directly at the container root (no subdirectory) are bucketed into a
synthetic ``_root`` component so the UI can render them without a
"missing parent" gap.

Returns the components plus a ``file_index`` mapping each file path inside
the container to its owning component id — relations.py reuses this index
to roll file→file edges up to component→component edges.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from repowise.core.persistence import GraphNode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .containers import container_id
from .models import Component

ROOT_COMPONENT_NAME = "_root"


def component_id(container_path: str, component_name: str) -> str:
    base = container_path or "."
    return f"cmp:{base}/{component_name}"


async def detect_components(
    session: AsyncSession,
    repository_id: str,
    container_path: str,
) -> tuple[list[Component], dict[str, str]]:
    """Return (components, file_index) for ``container_path``.

    ``file_index`` maps every file path inside the container to the id of
    the component that owns it.
    """
    file_nodes = await _files_in(session, repository_id, container_path)

    bucket: dict[str, list[GraphNode]] = defaultdict(list)
    for node in file_nodes:
        comp = _component_name(node.node_id, container_path)
        bucket[comp].append(node)

    components: list[Component] = []
    file_index: dict[str, str] = {}
    cid = container_id(container_path)
    for comp_name in sorted(bucket):
        nodes = bucket[comp_name]
        comp_path = f"{container_path}/{comp_name}" if container_path else comp_name
        comp_id = component_id(container_path, comp_name)
        components.append(
            Component(
                id=comp_id,
                name=comp_name,
                path=comp_path,
                container_id=cid,
                file_count=len(nodes),
                symbol_count=sum(n.symbol_count or 0 for n in nodes),
            )
        )
        for n in nodes:
            file_index[n.node_id] = comp_id
    return components, file_index


def _component_name(file_path: str, container_path: str) -> str:
    """Compute the top-level subdirectory of ``file_path`` relative to the
    container root."""
    if container_path:
        rel = file_path[len(container_path) + 1 :] if file_path.startswith(container_path + "/") else file_path
    else:
        rel = file_path
    if "/" in rel:
        return rel.split("/", 1)[0]
    return ROOT_COMPONENT_NAME


async def _files_in(
    session: AsyncSession, repository_id: str, container_path: str
) -> list[GraphNode]:
    stmt = select(GraphNode).where(
        GraphNode.repository_id == repository_id,
        GraphNode.node_type == "file",
    )
    if container_path:
        prefix = container_path + "/"
        # SQLite's LIKE is case-sensitive when the LHS is binary; for path
        # matching that's the behaviour we want.
        stmt = stmt.where(
            (GraphNode.node_id == container_path) | GraphNode.node_id.like(prefix + "%")
        )
    result = await session.execute(stmt)
    nodes = list(result.scalars())
    if not container_path:
        return nodes
    # We may have over-selected files whose path simply starts with the
    # container name as a prefix substring (e.g., "packages/core" should not
    # capture "packages/core-extras/foo.py"). Final filter:
    prefix = container_path + "/"
    return [n for n in nodes if n.node_id == container_path or n.node_id.startswith(prefix)]


# Re-export so callers can introspect / display the synthetic bucket name
__all__ = ["ROOT_COMPONENT_NAME", "component_id", "detect_components"]


# Convenience used by L2 relations: dominant language across the bucket.
def dominant_language(nodes: list[GraphNode]) -> str:
    counter: Counter[str] = Counter(
        n.language for n in nodes if n.language and n.language != "unknown"
    )
    return counter.most_common(1)[0][0] if counter else "unknown"
