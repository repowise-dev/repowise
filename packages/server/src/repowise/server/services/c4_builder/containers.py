"""Detect C4 L2 containers for an indexed repository.

Strategy
--------
Containers are derived from the persisted ``external_systems`` table — every
``declared_in`` path points at a manifest file, and that manifest's parent
directory is a container root. This works for both monorepos (many manifests)
and single-package repos (one root manifest).

Manifests under test or example trees are fixtures and samples, not the
system's containers, so they are dropped and their files fold into the nearest
remaining ancestor container (or into none, when no ancestor exists).

If no manifests were found (rare — exotic stacks), we fall back to the set
of top-level directories that hold indexed production files.

Each container's ``language``, ``file_count``, and ``symbol_count`` are
aggregated from ``graph_nodes`` rows whose ``node_id`` (file path) lives
inside the container directory; ``language`` counts code files only. The name
is the manifest's declared package name when the checkout is readable.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.c4.containers import assign_containers, container_name
from repowise.core.analysis.kg_inputs import _dominant_language as _most_common_language
from repowise.core.ids import ContainerId, render
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.persistence import ExternalSystem, GraphNode

from .models import Container

_CODE_LANGUAGES = REGISTRY.code_languages()


async def detect_containers(
    session: AsyncSession,
    repository_id: str,
    *,
    root_name: str | None = None,
    local_path: str | None = None,
) -> list[Container]:
    """Return the list of containers detected for ``repository_id``.

    A container is named by its manifest's package name, read from
    ``local_path`` when that checkout exists. Otherwise ``root_name`` (the
    repository name) labels the root container, which would serialize as the
    uninformative ``"."``, and any other container is named by its path.

    Stable ordering by ``path`` so the C4 diagram doesn't reshuffle between
    requests.
    """
    manifests = await _declared_manifests(session, repository_id)
    file_nodes = await _file_nodes(session, repository_id)
    owner, roots_to_manifests = assign_containers(manifests, (n.node_id for n in file_nodes))

    grouped: dict[str, list[GraphNode]] = defaultdict(list)
    for node in file_nodes:
        if node.node_id in owner:
            grouped[owner[node.node_id]].append(node)

    fallback_root_name = (root_name or ".").strip() or "."
    containers: list[Container] = []
    for root in sorted(grouped):
        nodes = grouped[root]
        lang = _dominant_language(nodes)
        name = container_name(
            Path(local_path) if local_path else None, roots_to_manifests.get(root, ())
        )
        containers.append(
            Container(
                id=container_id(root),
                name=name or root or fallback_root_name,
                path=root,
                language=lang,
                file_count=len(nodes),
                symbol_count=sum(n.symbol_count or 0 for n in nodes),
            )
        )
    return containers


def container_id(path: str) -> str:
    """Stable id for a container path. Edge sources/targets use this form."""
    return render(ContainerId(path or "."))


async def _declared_manifests(session: AsyncSession, repository_id: str) -> set[str]:
    """Every manifest path an external dependency was declared in."""
    result = await session.execute(
        select(ExternalSystem.declared_in).where(
            ExternalSystem.repository_id == repository_id
        )
    )
    return {declared_in for (declared_in,) in result.all() if declared_in}


async def _file_nodes(session: AsyncSession, repository_id: str) -> list[GraphNode]:
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_type == "file",
            # ``external:*`` nodes are unresolved-import / dependency targets,
            # not real source files — they must not become containers or inflate
            # file counts. Real external deps surface via the ext: mechanism.
            ~GraphNode.node_id.like("external:%"),
        )
    )
    return list(result.scalars())


def _dominant_language(nodes: list[GraphNode]) -> str:
    """Most common code language; markdown and config never name a container."""
    langs = [n.language for n in nodes if n.language in _CODE_LANGUAGES]
    return _most_common_language(langs) or "unknown"
