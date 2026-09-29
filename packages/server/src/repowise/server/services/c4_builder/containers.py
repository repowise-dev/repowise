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
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.kg_inputs import _dominant_language as _most_common_language
from repowise.core.ids import ContainerId, render
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.workspace_members import manifest_package_name
from repowise.core.persistence import ExternalSystem, GraphNode
from repowise.core.support_paths import is_test_or_example_path

from .models import Container

# Package-manifest basenames. A directory holding one of these is a container
# root even when the manifest declares no external dependencies (so a package
# like ``packages/core`` is its own container rather than dissolving into the
# repo-root catch-all).
_MANIFEST_BASENAMES = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "composer.json",
        "gemfile",
    }
)
_MANIFEST_SUFFIXES = (".csproj",)

_CODE_LANGUAGES = REGISTRY.code_languages()


def _is_manifest(node_id: str) -> bool:
    base = node_id.rsplit("/", 1)[-1].lower()
    return base in _MANIFEST_BASENAMES or base.endswith(_MANIFEST_SUFFIXES)


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

    # Also treat any directory holding a package manifest as a container root,
    # even when that manifest declared no external deps (catches e.g. a core
    # package that would otherwise dissolve into the repo-root catch-all).
    manifests |= {n.node_id for n in file_nodes if _is_manifest(n.node_id)}
    roots_to_manifests = _manifest_roots(manifests)
    container_roots = set(roots_to_manifests)

    if not container_roots:
        container_roots = _top_level_dirs(
            n.node_id for n in file_nodes if not is_test_or_example_path(n.node_id)
        )

    # Sort longest path first so nested manifests win over their parents.
    sorted_roots = sorted(container_roots, key=len, reverse=True)

    grouped: dict[str, list[GraphNode]] = defaultdict(list)
    for node in file_nodes:
        root = _match_container(node.node_id, sorted_roots)
        if root is not None:
            grouped[root].append(node)

    fallback_root_name = (root_name or ".").strip() or "."
    containers: list[Container] = []
    for root in sorted(grouped):
        nodes = grouped[root]
        lang = _dominant_language(nodes)
        name = _manifest_name(local_path, roots_to_manifests.get(root, ()))
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


def _manifest_roots(manifests: Iterable[str]) -> dict[str, list[str]]:
    """``{container root: its manifests}``, fixture and sample manifests dropped."""
    roots: dict[str, list[str]] = defaultdict(list)
    for path in sorted(manifests):
        if not is_test_or_example_path(path):
            roots[path.rsplit("/", 1)[0] if "/" in path else ""].append(path)
    return roots


def _manifest_name(local_path: str | None, manifests: Iterable[str]) -> str | None:
    """The first package name the root's manifests declare, read from the checkout."""
    # Ceiling: needs the checkout on disk; persisting names at index time lifts it.
    if not local_path or not Path(local_path).is_dir():
        return None
    for manifest in manifests:
        name = manifest_package_name(Path(local_path) / manifest)
        if name:
            return name
    return None


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
    """Most common code language; markdown and config never name a container."""
    langs = [n.language for n in nodes if n.language in _CODE_LANGUAGES]
    return _most_common_language(langs) or "unknown"
