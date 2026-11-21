"""Bare specifier resolution against the workspace map.

Each ladder keeps its historical order. Steps added later (spare export
candidates, built-output sources, nested sub-packages) sit behind every older
probe, so they only fill a specifier that resolved to nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .builds import _built_source, get_checked_in_builds
from .exports import _DECLARATION_SUFFIXES, _build_exports_map, _match_export_key
from .members import _entry_hint, get_or_build_workspace_info
from .paths import _probe_path

if TYPE_CHECKING:
    from ..context import ResolverContext


def _read_sub_package_manifest(repo_path: Path, sub_dir_posix: str) -> dict[str, Any] | None:
    """Parse ``<repo_path>/<sub_dir_posix>/package.json``, or None; no ``name`` required."""
    manifest = repo_path / sub_dir_posix / "package.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _resolve_nested_subpackage(dir_posix: str, sub: str, ctx: ResolverContext) -> str | None:
    """Resolve ``sub`` as a sub-package with its own ``package.json`` (solid's ``web``, ``store``).

    Same order as the bare-package ladder, rooted at ``<dir_posix>/<sub>``.
    """
    if ctx.repo_path is None:
        return None
    sub_dir = f"{dir_posix}/{sub}"
    sub_pkg_data = _read_sub_package_manifest(ctx.repo_path, sub_dir)
    if sub_pkg_data is None:
        return None

    exports_map = _build_exports_map(sub_pkg_data)
    targets = _match_export_key("", exports_map) if exports_map else None
    for target in targets or ():
        if target.endswith(_DECLARATION_SUFFIXES):
            continue
        cand = _probe_path(f"{sub_dir}/{target.removeprefix('./')}", ctx.path_set)
        if cand is not None:
            return cand

    cand = _probe_path(f"{sub_dir}/index", ctx.path_set)
    if cand is not None:
        return cand
    main = _entry_hint(sub_pkg_data)
    if isinstance(main, str):
        cand = _probe_path(f"{sub_dir}/{main.removeprefix('./')}", ctx.path_set)
        if cand is not None:
            return cand
    for source_root in ("src", "lib"):
        cand = _probe_path(f"{sub_dir}/{source_root}/index", ctx.path_set)
        if cand is not None:
            return cand
    return None


def _match_workspace_name(module_path: str, info: dict[str, dict[str, Any]]) -> str | None:
    """The longest workspace package name *module_path* is, or sits under."""
    best_name: str | None = None
    for name in info:
        if (module_path == name or module_path.startswith(name + "/")) and (
            best_name is None or len(name) > len(best_name)
        ):
            best_name = name
    return best_name


def _first_found(steps: tuple[Callable[[], str | None], ...]) -> str | None:
    """The first file a step finds, running the steps in order."""
    for step in steps:
        found = step()
        if found is not None:
            return found
    return None


class _WorkspaceProbe:
    """The probes for one specifier against the workspace package it names."""

    __slots__ = ("ctx", "dir_posix", "pkg", "sub", "targets")

    def __init__(self, pkg: dict[str, Any], sub: str, ctx: ResolverContext) -> None:
        self.ctx = ctx
        self.pkg = pkg
        self.dir_posix: str = pkg["dir"]
        self.sub = sub
        exports_map: dict[str, tuple[str, ...]] = pkg["exports"]
        self.targets = _match_export_key(sub, exports_map) if exports_map else None

    def resolve(self) -> str | None:
        # The ``exports`` map is authoritative.
        if self.targets:
            resolved = self.probe_target(self.targets[0])
            if resolved is not None:
                return resolved
        if not self.sub:
            # Bare package with no ``exports["."]`` hit.
            return _first_found((
                self.index_entry,
                self.main_entry,
                self.spare_export_target,
                self.source_root_index,
                self.built_export_source,
            ))
        # Subpath under a plain layout, then build output, then a nested sub-package.
        return _first_found((
            self.direct_subpath,
            self.source_root_subpath,
            self.spare_export_target,
            self.built_export_source,
            self.nested_subpackage,
        ))

    def probe(self, rel: str) -> str | None:
        return _probe_path(f"{self.dir_posix}/{rel}", self.ctx.path_set)

    def probe_target(self, target: str) -> str | None:
        return self.probe(target.removeprefix("./"))

    def index_entry(self) -> str | None:
        return self.probe("index")

    def main_entry(self) -> str | None:
        main = self.pkg.get("main")
        return self.probe(main.removeprefix("./")) if isinstance(main, str) else None

    def source_root_index(self) -> str | None:
        for source_root in ("src", "lib"):
            cand = self.probe(f"{source_root}/index")
            if cand is not None:
                return cand
        return None

    def direct_subpath(self) -> str | None:
        return self.probe(self.sub)

    def source_root_subpath(self) -> str | None:
        for src_root in ("src", "lib", "dist"):
            cand = self.probe(f"{src_root}/{self.sub}")
            if cand is not None:
                return cand
        return None

    def spare_export_target(self) -> str | None:
        """A candidate past the ranked one; never a declaration file, which has no bodies."""
        for target in (self.targets or ())[1:]:
            if target.endswith(_DECLARATION_SUFFIXES):
                continue
            resolved = self.probe_target(target)
            if resolved is not None:
                return resolved
        return None

    def built_export_source(self) -> str | None:
        checked_in = get_checked_in_builds(self.ctx)
        for target in self.targets or ():
            resolved = _built_source(self.dir_posix, target, self.ctx.path_set, checked_in)
            if resolved is not None:
                return resolved
        return None

    def nested_subpackage(self) -> str | None:
        return _resolve_nested_subpackage(self.dir_posix, self.sub, self.ctx)


def resolve_via_workspaces(module_path: str, ctx: ResolverContext) -> str | None:
    """Resolve ``@scope/pkg`` or ``@scope/pkg/sub/file`` to a repo-relative path, or None."""
    info = get_or_build_workspace_info(ctx)
    if not info:
        return None
    best_name = _match_workspace_name(module_path, info)
    if best_name is None:
        return None
    sub = module_path[len(best_name) :].lstrip("/")
    return _WorkspaceProbe(info[best_name], sub, ctx).resolve()
