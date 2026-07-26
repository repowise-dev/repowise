"""Detect C4 L3 components inside a single container.

A component is a meaningful child directory of the container. Two refinements
keep the view legible:

* **Pass-through roots are skipped.** A leading build-convention directory
  (``src``/``lib``/``app``/…) carries no architectural meaning, so a file at
  ``packages/ui/src/health/x.tsx`` is grouped under ``health`` rather than a
  single giant ``src``. This is the "one more level where it adds clarity"
  rule — the displayed level tracks the real feature directory.
* **Files at the container root** (or only inside pass-through dirs) collapse
  into one synthetic bucket labeled ``(root)`` — never the leaky ``_root``
  token, which used to reach the API verbatim.

External (``external:*``) nodes and files owned by sibling containers are
excluded so a root/catch-all container does not absorb another container's
tree.

Returns the components plus a ``file_index`` mapping each owned file path to
its component id — relations.py reuses this index to roll file->file edges up
to component->component edges.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ids import ComponentId, render
from repowise.core.persistence import GraphNode

from .containers import container_id
from .models import Component, Container

# Source-root directories that carry no architectural meaning — skipped so a
# component reflects the real feature directory beneath them. Deliberately only
# genuine source roots (not build-output dirs like ``dist``, which we do want
# to surface as their own component if indexed).
_PASS_THROUGH_DIRS = frozenset({"src", "lib", "app", "source"})

# Display label for the bucket of files that sit at the container root.
# Chosen so it never serializes the old "_root" token.
ROOT_BUCKET_LABEL = "(root)"


def component_id(component_path: str, *, is_root_bucket: bool = False) -> str:
    """Stable id for a component, keyed on its repo-relative directory."""
    return render(ComponentId(component_path or ".", is_root_bucket=is_root_bucket))


def _relative(file_path: str, container_path: str) -> str:
    if container_path and file_path.startswith(container_path + "/"):
        return file_path[len(container_path) + 1 :]
    return file_path


def _component_for(
    file_path: str, container_path: str, root_label: str
) -> tuple[str, str, str]:
    """Return ``(component_id, name, path)`` for a file's owning component.

    Skips leading pass-through directories; files with no meaningful directory
    fall into the ``(root)`` bucket.
    """
    rel = _relative(file_path, container_path)
    dirs = rel.split("/")[:-1]  # directory segments only (drop the filename)

    i = 0
    while i < len(dirs) and dirs[i] in _PASS_THROUGH_DIRS:
        i += 1

    if i >= len(dirs):
        base = container_path or "."
        return component_id(base, is_root_bucket=True), root_label, base

    name = dirs[i]
    rel_dir = "/".join(dirs[: i + 1])
    path = f"{container_path}/{rel_dir}" if container_path else rel_dir
    return component_id(path), name, path


async def detect_components(
    session: AsyncSession,
    repository_id: str,
    container_path: str,
    *,
    sibling_roots: Iterable[str] = (),
    root_label: str = ROOT_BUCKET_LABEL,
) -> tuple[list[Component], dict[str, str]]:
    """Return (components, file_index) for ``container_path``.

    ``sibling_roots`` are the paths of other containers; files under them are
    excluded so a catch-all/root container does not claim another container's
    files. ``root_label`` names the container-root bucket.

    ``file_index`` maps every owned file path to the id of its component.
    """
    file_nodes = await _files_in(
        session, repository_id, container_path, sibling_roots=sibling_roots
    )
    return _bucket_into_components(file_nodes, container_path, root_label)


async def detect_components_for_all(
    session: AsyncSession,
    repository_id: str,
    containers: Iterable[Container],
    *,
    root_label: str = ROOT_BUCKET_LABEL,
) -> dict[str, tuple[list[Component], dict[str, str]]]:
    """Same as :func:`detect_components`, for every container, in one query.

    :func:`detect_components` re-reads the file table per call and throws away
    everything outside its container, which is right for the dashboard (one
    container at a time) and wasteful for anything walking the whole model.
    This reads once and slices in Python; the ownership rules are shared, so
    the two agree by construction.

    Returns ``{container_id: (components, file_index)}``.
    """
    containers = list(containers)
    all_files = await _files_in(session, repository_id, "")
    by_path: dict[str, list[GraphNode]] = defaultdict(list)
    for node in all_files:
        by_path[node.node_id].append(node)

    roots = [c.path for c in containers if c.path]
    out: dict[str, tuple[list[Component], dict[str, str]]] = {}
    for container in containers:
        sibling_prefixes = tuple(
            r + "/" for r in roots if r and r != container.path
        )
        owned = [
            n
            for n in all_files
            if _owned_by(n.node_id, container.path, sibling_prefixes)
        ]
        out[container.id] = _bucket_into_components(owned, container.path, root_label)
    return out


def _owned_by(node_id: str, container_path: str, sibling_prefixes: tuple[str, ...]) -> bool:
    """Whether *node_id* belongs to a container — the rule :func:`_files_in` applies."""
    if container_path and not (
        node_id == container_path or node_id.startswith(container_path + "/")
    ):
        return False
    return not node_id.startswith(sibling_prefixes) if sibling_prefixes else True


def _bucket_into_components(
    file_nodes: list[GraphNode], container_path: str, root_label: str
) -> tuple[list[Component], dict[str, str]]:
    """Group a container's files into components. Pure — no session."""
    bucket: dict[str, list[GraphNode]] = defaultdict(list)
    meta: dict[str, tuple[str, str]] = {}  # comp_id -> (name, path)
    file_owner: dict[str, str] = {}
    cid = container_id(container_path)
    for node in file_nodes:
        comp_id, name, path = _component_for(node.node_id, container_path, root_label)
        bucket[comp_id].append(node)
        meta[comp_id] = (name, path)
        file_owner[node.node_id] = comp_id

    components: list[Component] = []
    for comp_id in sorted(bucket, key=lambda c: meta[c][1]):
        nodes = bucket[comp_id]
        name, path = meta[comp_id]
        components.append(
            Component(
                id=comp_id,
                name=name,
                path=path,
                container_id=cid,
                file_count=len(nodes),
                symbol_count=sum(n.symbol_count or 0 for n in nodes),
            )
        )
    return components, file_owner


async def _files_in(
    session: AsyncSession,
    repository_id: str,
    container_path: str,
    *,
    sibling_roots: Iterable[str] = (),
) -> list[GraphNode]:
    stmt = select(GraphNode).where(
        GraphNode.repository_id == repository_id,
        GraphNode.node_type == "file",
        # Unresolved-import / dependency targets are not source files.
        ~GraphNode.node_id.like("external:%"),
    )
    if container_path:
        prefix = container_path + "/"
        stmt = stmt.where(
            (GraphNode.node_id == container_path) | GraphNode.node_id.like(prefix + "%")
        )
    result = await session.execute(stmt)
    nodes = list(result.scalars())

    # The LIKE prefix can over-select on a shared substring (e.g. "packages/core"
    # must not capture "packages/core-extras/foo.py"). Final exact filter.
    if container_path:
        prefix = container_path + "/"
        nodes = [
            n for n in nodes if n.node_id == container_path or n.node_id.startswith(prefix)
        ]

    # Drop files owned by a sibling container so a root/catch-all container does
    # not absorb another container's tree.
    sibling_prefixes = tuple(r + "/" for r in sibling_roots if r)
    if sibling_prefixes:
        nodes = [n for n in nodes if not n.node_id.startswith(sibling_prefixes)]
    return nodes


__all__ = [
    "ROOT_BUCKET_LABEL",
    "component_id",
    "detect_components",
    "detect_components_for_all",
]
