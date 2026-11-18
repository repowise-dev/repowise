"""Every package root on disk, for ecosystems no specific adapter reads (Python, Maven, Ruby ...).

The roots come from :func:`scan_package_manifests`, which reads the language
registry's manifest names; the repo root, which that scan leaves out, is
added when it holds a manifest. A root whose every manifest *decided*
accepts is skipped: a specific adapter already ruled on it. These units carry
no deps.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.package_roots import (
    _manifests_in,
    package_manifest_names,
    scan_package_manifests,
)
from repowise.core.ingestion.resolvers.context import ResolverContext


def units(
    ctx: ResolverContext, decided: Callable[[str], bool]
) -> list[tuple[BuildUnit, tuple[str, ...]]]:
    if ctx.repo_path is None:
        return []
    repo = ctx.repo_path
    roots = scan_package_manifests(repo, prune_nested_git=ctx.prune_nested_git)
    try:
        with os.scandir(repo) as listing:
            top = [entry.name for entry in listing if entry.is_file()]
    except OSError:
        top = []
    at_root = _manifests_in(top, package_manifest_names())
    if at_root:
        roots[""] = at_root

    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for root, names in roots.items():
        if all(decided(name) for name in names):
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
