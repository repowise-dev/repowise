"""``TsWorkspaceIndex``: the files a workspace publishes as entry points."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .builds import get_checked_in_builds, manifest_entry_paths
from .members import get_or_build_workspace_info
from .paths import _normalize_repo_rel, _probe_path
from .scan import _parsed_package_jsons

if TYPE_CHECKING:
    from ..context import ResolverContext


@dataclass
class TsWorkspaceIndex:
    """Aggregated TypeScript/JavaScript workspace metadata.

    ``exports_entry_paths`` are the files workspace ``exports`` maps publish:
    consumers outside the repo reach them, so they are never unreachable.
    """

    packages: dict[str, dict[str, Any]] = field(default_factory=dict)
    exports_entry_paths: set[str] = field(default_factory=set)
    # Start files any ``package.json`` names, workspace member or not.
    manifest_entry_paths: set[str] = field(default_factory=set)


def _expand_exports_wildcard(
    target: str, exports_pattern: str, pkg_dir: str, path_set: set[str]
) -> set[str]:
    """Every indexed file an ``exports`` target (``"./src/locales/*.ts"``) matches."""
    if "*" not in target:
        stripped = target.lstrip("./")
        resolved = _probe_path(f"{pkg_dir}/{stripped}", path_set)
        return {resolved} if resolved is not None else set()
    # The Node spec allows one ``*`` per pattern.
    stripped = target.lstrip("./")
    prefix, _, suffix = stripped.partition("*")
    base_prefix = _normalize_repo_rel(f"{pkg_dir}/{prefix}")
    matches: set[str] = set()
    for candidate in path_set:
        if not candidate.startswith(base_prefix):
            continue
        if suffix and not candidate.endswith(suffix):
            continue
        # Per the Node spec ``*`` may span ``/``.
        matches.add(candidate)
    return matches


def build_ts_workspace_index(ctx: ResolverContext) -> TsWorkspaceIndex:
    """Build the workspace index for *ctx*; idempotent."""
    packages = get_or_build_workspace_info(ctx)
    entries: set[str] = set()
    path_set = ctx.path_set
    for _name, pkg in packages.items():
        dir_posix: str = pkg["dir"]
        exports_map: dict[str, tuple[str, ...]] = pkg.get("exports") or {}
        for pattern, targets in exports_map.items():
            # The ranked target only: spare candidates must not widen the published set.
            entries.update(
                _expand_exports_wildcard(targets[0], pattern, dir_posix, path_set)
            )
        main = pkg.get("main")
        if isinstance(main, str):
            resolved = _probe_path(f"{dir_posix}/{main.lstrip('./')}", path_set)
            if resolved is not None:
                entries.add(resolved)
    checked_in = get_checked_in_builds(ctx)
    declared: set[str] = set()
    for pkg_dir, data in _parsed_package_jsons(ctx):
        declared |= manifest_entry_paths(pkg_dir, data, path_set, checked_in)
    return TsWorkspaceIndex(
        packages=packages, exports_entry_paths=entries, manifest_entry_paths=declared
    )


def get_or_build_ts_index(ctx: ResolverContext) -> TsWorkspaceIndex:
    """Memoized per resolver context."""
    cached = getattr(ctx, "_ts_workspace_index", None)
    if cached is not None:
        return cached
    index = build_ts_workspace_index(ctx)
    ctx._ts_workspace_index = index  # type: ignore[attr-defined]
    return index
