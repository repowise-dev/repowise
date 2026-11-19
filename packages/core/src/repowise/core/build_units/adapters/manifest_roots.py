"""Every package root on disk, for ecosystems no specific adapter reads (Ruby, Dart ...).

The roots come from :func:`package_manifests`, which reads the language
registry's manifest names. A root whose every manifest *decided* accepts
(called with the manifest's repo-relative path) is skipped: a specific
adapter already ruled on each. These units carry no deps.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.package_roots import scan_package_manifests
from repowise.core.ingestion.resolvers.context import ResolverContext

_CACHE_KEY = "_build_unit_manifests"


def _root_manifests(repo: Path) -> frozenset[str]:
    """The package manifests directly in *repo*, which the scan leaves out."""
    try:
        with os.scandir(repo) as listing:
            return frozenset(
                entry.name
                for entry in listing
                if entry.is_file() and REGISTRY.is_package_manifest(entry.name)
            )
    except OSError:
        return frozenset()


def package_manifests(ctx: ResolverContext) -> dict[str, frozenset[str]]:
    """Each package root (``""`` for the repo root) to its manifest filenames, scanned once per *ctx*.

    Empty without a repo path.
    """
    cached = getattr(ctx, _CACHE_KEY, None)
    if cached is not None:
        return cached
    roots: dict[str, frozenset[str]] = {}
    if ctx.repo_path is not None:
        roots = scan_package_manifests(ctx.repo_path, prune_nested_git=ctx.prune_nested_git)
        if at_root := _root_manifests(ctx.repo_path):
            roots[""] = at_root
    setattr(ctx, _CACHE_KEY, roots)
    return roots


def units(
    ctx: ResolverContext, decided: Callable[[str], bool]
) -> list[tuple[BuildUnit, tuple[str, ...]]]:
    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for root, names in package_manifests(ctx).items():
        if all(decided(f"{root}/{name}" if root else name) for name in names):
            continue
        manifest = min(names)
        unit = BuildUnit(
            id=f"manifest:{root or '.'}",
            ecosystem="manifest",
            name=root.rsplit("/", 1)[-1] or ".",
            root=root,
            manifest=f"{root}/{manifest}" if root else manifest,
            source="manifest-root",
        )
        out.append((unit, ()))
    return out
