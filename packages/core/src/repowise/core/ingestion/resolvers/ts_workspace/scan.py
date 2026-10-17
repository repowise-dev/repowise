"""One pruned filesystem walk, memoized per resolver context, shared by every finder.

A per-finder ``rglob`` walked the whole tree (node_modules, virtualenvs, .git)
once per pattern. The prune list stays narrow: only vendored or derived trees
that never hold our manifests; dot-dirs like ``.github`` may hold real ones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import ResolverContext


_SCAN_PRUNE_DIRS: frozenset[str] = frozenset({
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".tox",
    "__pycache__",
    ".repowise",
    ".repowise.prebench-bak",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    ".parcel-cache",
    ".yarn",
    ".pnpm-store",
})

_VITEST_CONFIG_NAMES: frozenset[str] = frozenset({
    "vitest.config.ts", "vitest.config.js", "vitest.config.mts",
    "vitest.config.mjs", "vitest.config.cjs", "vitest.config.cts",
    "vite.config.ts", "vite.config.js", "vite.config.mts",
    "vite.config.mjs",
})


@dataclass
class _RepoFileScan:
    """File locations gathered by the single pruned walk."""

    mdx_files: list[Path] = field(default_factory=list)
    vitest_configs: list[Path] = field(default_factory=list)
    package_jsons: list[Path] = field(default_factory=list)
    plugin_jsons: list[Path] = field(default_factory=list)


def _scan_repo_files(repo_path: Path, *, prune_nested_git: bool = True) -> _RepoFileScan:
    """One pruned walk collecting every finder's target files."""
    from repowise.core.fs_walk import walk_repo

    scan = _RepoFileScan()
    for dirpath, _dirnames, filenames in walk_repo(
        repo_path, prune_dirs=_SCAN_PRUNE_DIRS, prune_nested_git=prune_nested_git
    ):
        for fname in filenames:
            if fname.endswith(".mdx"):
                scan.mdx_files.append(Path(dirpath) / fname)
            elif fname in _VITEST_CONFIG_NAMES:
                scan.vitest_configs.append(Path(dirpath) / fname)
            elif fname == "package.json":
                scan.package_jsons.append(Path(dirpath) / fname)
            elif fname == "plugin.json":
                scan.plugin_jsons.append(Path(dirpath) / fname)
    return scan


def _get_repo_scan(ctx: ResolverContext) -> _RepoFileScan:
    """Memoized accessor — one walk per resolver context."""
    cached = getattr(ctx, "_ts_repo_file_scan", None)
    if cached is not None:
        return cached
    scan = (
        _scan_repo_files(ctx.repo_path, prune_nested_git=ctx.prune_nested_git)
        if ctx.repo_path is not None
        else _RepoFileScan()
    )
    ctx._ts_repo_file_scan = scan  # type: ignore[attr-defined]
    return scan


def _parsed_package_jsons(ctx: ResolverContext) -> list[tuple[str, dict]]:
    """``(repo-relative dir, data)`` for every readable ``package.json`` object, memoized.

    The base is ``ctx.repo_path`` as given, not resolved: the scan walks that
    same path, and a different base would drop every manifest.
    """
    cached = getattr(ctx, "_ts_parsed_package_jsons", None)
    if cached is not None:
        return cached
    parsed: list[tuple[str, dict]] = []
    for pkg_file in _get_repo_scan(ctx).package_jsons:
        try:
            pkg_dir = pkg_file.parent.relative_to(ctx.repo_path).as_posix()
            data = json.loads(pkg_file.read_text(encoding="utf-8", errors="ignore"))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            parsed.append((pkg_dir, data))
    ctx._ts_parsed_package_jsons = parsed  # type: ignore[attr-defined]
    return parsed
