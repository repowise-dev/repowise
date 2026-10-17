"""Workspace member discovery from ``package.json`` and ``pnpm-workspace.yaml``.

pnpm does not read ``package.json``'s ``workspaces`` field, so when
``pnpm-workspace.yaml`` exists it alone governs membership: merging the two
would register members pnpm never installs as fake intra-repo edges.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..pnpm_workspace import read_pnpm_workspace_patterns
from .exports import _build_exports_map

if TYPE_CHECKING:
    from ..context import ResolverContext


def read_workspaces_field(pkg_data: dict) -> list[str]:
    ws = pkg_data.get("workspaces")
    if isinstance(ws, list):
        return [str(p) for p in ws if isinstance(p, str)]
    if isinstance(ws, dict):
        packages = ws.get("packages")
        if isinstance(packages, list):
            return [str(p) for p in packages if isinstance(p, str)]
    return []


def _entry_hint(pkg_data: dict) -> str | None:
    """``module`` when it is a string, else ``main`` when it is, else None."""
    module = pkg_data.get("module")
    if isinstance(module, str):
        return module
    main = pkg_data.get("main")
    return main if isinstance(main, str) else None


def build_workspace_map(repo_path: Path | None) -> dict[str, str]:
    """Return ``{package_name: dir_posix}`` for every workspace package."""
    return {name: info["dir"] for name, info in build_workspace_info(repo_path).items()}


@dataclass(frozen=True)
class _WorkspaceDeclaration:
    """Which globs govern a repo's workspace, and whether the root is a member."""

    includes: tuple[str, ...]
    excludes: tuple[str, ...]
    include_root: bool

    @property
    def is_empty(self) -> bool:
        return not self.includes and not self.include_root


def _read_workspace_declaration(repo_path: Path) -> _WorkspaceDeclaration:
    """Resolve the governing manifest into member globs."""
    pnpm = read_pnpm_workspace_patterns(repo_path)
    if pnpm is not None:
        includes, excludes = pnpm
        # pnpm always includes the root package, even with no ``packages`` key.
        return _WorkspaceDeclaration(tuple(includes), tuple(excludes), include_root=True)

    root_pkg = repo_path / "package.json"
    if not root_pkg.is_file():
        return _WorkspaceDeclaration((), (), include_root=False)
    try:
        data = json.loads(root_pkg.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return _WorkspaceDeclaration((), (), include_root=False)
    if not isinstance(data, dict):
        return _WorkspaceDeclaration((), (), include_root=False)
    return _WorkspaceDeclaration(
        tuple(read_workspaces_field(data)), (), include_root=False
    )


def _expand_member_dirs(repo_path: Path, declared: _WorkspaceDeclaration) -> list[Path]:
    """Expand member globs to directories, minus the negated ones.

    Negations use the same ``Path.glob`` as the includes, which anchors at the
    root like pnpm's matcher; git-ignore semantics would drop members pnpm
    keeps. The root member is never negated. A pattern ``Path.glob`` refuses
    (absolute or empty) drops only itself.
    """
    excluded: set[Path] = set()
    for pattern in declared.excludes:
        with contextlib.suppress(NotImplementedError, ValueError):
            excluded.update(p for p in repo_path.glob(pattern) if p.is_dir())

    dirs: list[Path] = [repo_path] if declared.include_root else []
    for pattern in declared.includes:
        with contextlib.suppress(NotImplementedError, ValueError):
            globbed = [repo_path] if pattern == "." else repo_path.glob(pattern)
            dirs.extend(p for p in globbed if p.is_dir() and p not in excluded)
    return dirs


def build_workspace_info(repo_path: Path | None) -> dict[str, dict[str, Any]]:
    """Return ``{pkg_name: {"dir": <posix>, "exports": {...}, "main": str|None}}``."""
    if repo_path is None or not repo_path.is_dir():
        return {}
    declared = _read_workspace_declaration(repo_path)
    if declared.is_empty:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for ws_dir in _expand_member_dirs(repo_path, declared):
        try:
            rel = ws_dir.relative_to(repo_path).as_posix()
        except ValueError:
            continue
        ws_pkg = ws_dir / "package.json"
        if not ws_pkg.is_file():
            continue
        try:
            ws_data = json.loads(ws_pkg.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if not isinstance(ws_data, dict):
            continue
        name = ws_data.get("name")
        if not isinstance(name, str) or not name:
            continue
        result[name] = {
            "dir": rel,
            "exports": _build_exports_map(ws_data),
            "main": _entry_hint(ws_data),
        }
    return result


def get_or_build_workspace_info(ctx: ResolverContext) -> dict[str, dict[str, Any]]:
    cached = getattr(ctx, "_ts_workspace_info", None)
    if cached is not None:
        return cached
    info = build_workspace_info(ctx.repo_path)
    ctx._ts_workspace_info = info  # type: ignore[attr-defined]
    return info
