"""Assign files to C4 containers.

A directory holding a package manifest is a container root. Manifests under
test or example trees are fixtures and samples, not the system's containers, so
they are dropped and their files fold into the nearest remaining ancestor
container (or into none, when no ancestor exists). With no manifest at all, the
top-level directories holding production files stand in.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from repowise.core.ingestion.workspace_members import manifest_package_name
from repowise.core.support_paths import is_test_or_example_path

# Package-manifest basenames. A directory holding one of these is a container
# root even when the manifest declares no external dependencies (so a package
# like ``packages/core`` is its own container instead of dissolving into the
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


def is_manifest(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    return base in _MANIFEST_BASENAMES or base.endswith(_MANIFEST_SUFFIXES)


def manifest_roots(manifests: Iterable[str]) -> dict[str, list[str]]:
    """``{container root: its manifests}``, fixture and sample manifests dropped."""
    roots: dict[str, list[str]] = defaultdict(list)
    for path in sorted(manifests):
        if not is_test_or_example_path(path):
            roots[path.rsplit("/", 1)[0] if "/" in path else ""].append(path)
    return roots


def assign_containers(
    manifests: Iterable[str], file_paths: Iterable[str]
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Map each file to its container root, and each root to its manifests.

    *manifests* may be a superset of the manifest files: any path in
    *file_paths* that is a manifest counts too. Files no root claims are left
    out of the map. The root ``""`` is the repository root.
    """
    paths = list(file_paths)
    roots = manifest_roots({*manifests, *(p for p in paths if is_manifest(p))})
    candidates = set(roots) or _top_level_dirs(p for p in paths if not is_test_or_example_path(p))
    # Longest first so nested manifests win over their parents.
    ordered = sorted(candidates, key=len, reverse=True)
    owner: dict[str, str] = {}
    for path in paths:
        root = _match_container(path, ordered)
        if root is not None:
            owner[path] = root
    return owner, roots


def container_name(repo_root: Path | None, manifests: Iterable[str]) -> str | None:
    """The first package name the root's manifests declare, read from the checkout."""
    # Ceiling: needs the checkout on disk; persisting names at index time lifts it.
    if repo_root is None or not repo_root.is_dir():
        return None
    for manifest in manifests:
        name = manifest_package_name(repo_root / manifest)
        if name:
            return name
    return None


def _top_level_dirs(paths: Iterable[str]) -> set[str]:
    return {path.split("/", 1)[0] if "/" in path else "" for path in paths}


def _match_container(file_path: str, sorted_roots: list[str]) -> str | None:
    """Return the deepest container root containing ``file_path``."""
    for root in sorted_roots:
        if not root:
            return root  # root manifest catches everything that isn't claimed
        if file_path == root or file_path.startswith(root + "/"):
            return root
    return None
