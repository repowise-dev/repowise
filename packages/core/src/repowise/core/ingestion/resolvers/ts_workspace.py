"""npm/yarn/pnpm workspace package resolution for TypeScript imports.

Reads the root ``package.json``'s ``workspaces`` field (string list or
``{"packages": [...]}`` form), expands glob patterns, and reads each
sibling package's ``name`` field. The resulting ``{pkg_name: dir_posix}``
map lets the TS resolver turn ``import x from "@myorg/foo"`` into the
correct intra-repo file rather than an ``external:`` node.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ResolverContext


def _read_workspaces_field(pkg_data: dict) -> list[str]:
    ws = pkg_data.get("workspaces")
    if isinstance(ws, list):
        return [str(p) for p in ws if isinstance(p, str)]
    if isinstance(ws, dict):
        packages = ws.get("packages")
        if isinstance(packages, list):
            return [str(p) for p in packages if isinstance(p, str)]
    return []


def build_workspace_map(repo_path: Path | None) -> dict[str, str]:
    """Return ``{package_name: dir_posix}`` for every workspace package.

    Empty dict if no root ``package.json`` or no ``workspaces`` field.
    """
    if repo_path is None or not repo_path.is_dir():
        return {}
    root_pkg = repo_path / "package.json"
    if not root_pkg.is_file():
        return {}
    try:
        data = json.loads(root_pkg.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    patterns = _read_workspaces_field(data)
    if not patterns:
        return {}

    result: dict[str, str] = {}
    for pattern in patterns:
        # Glob each pattern relative to the repo root. ``pathlib.Path.glob``
        # already understands ``*`` and ``**``.
        for ws_dir in repo_path.glob(pattern):
            if not ws_dir.is_dir():
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
            try:
                rel = ws_dir.relative_to(repo_path).as_posix()
            except ValueError:
                continue
            result[name] = rel
    return result


def get_or_build_workspace_map(ctx: "ResolverContext") -> dict[str, str]:
    cached = getattr(ctx, "_ts_workspace_map", None)
    if cached is not None:
        return cached
    mapping = build_workspace_map(ctx.repo_path)
    ctx._ts_workspace_map = mapping  # type: ignore[attr-defined]
    return mapping


def resolve_via_workspaces(module_path: str, ctx: "ResolverContext") -> str | None:
    """Resolve a bare specifier (``@scope/pkg`` or ``@scope/pkg/sub/file``)
    against the workspace map. Returns a repo-relative path or None.
    """
    mapping = get_or_build_workspace_map(ctx)
    if not mapping:
        return None

    # Match the longest package-name prefix. ``@scope/pkg/sub/x`` should bind
    # ``@scope/pkg`` and resolve ``sub/x`` under that workspace's dir.
    best: tuple[str, str] | None = None
    for name, dir_posix in mapping.items():
        if module_path == name or module_path.startswith(name + "/"):
            if best is None or len(name) > len(best[0]):
                best = (name, dir_posix)
    if best is None:
        return None
    name, dir_posix = best
    sub = module_path[len(name) :].lstrip("/")
    base = f"{dir_posix}/{sub}" if sub else dir_posix

    # When the import lacks an explicit file path, target the package's
    # entry point. Try ``index.{ts,tsx,js,jsx}``.
    if not sub:
        for ext in (".ts", ".tsx", ".js", ".jsx"):
            cand = f"{base}/index{ext}"
            if cand in ctx.path_set:
                return cand
        # Read main/module from the workspace package.json
        if ctx.repo_path is not None:
            ws_pkg = ctx.repo_path / dir_posix / "package.json"
            if ws_pkg.is_file():
                try:
                    ws_data = json.loads(ws_pkg.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    ws_data = None
                if isinstance(ws_data, dict):
                    for key in ("module", "main"):
                        entry = ws_data.get(key)
                        if isinstance(entry, str):
                            cand = f"{dir_posix}/{entry.lstrip('./')}"
                            if cand in ctx.path_set:
                                return cand
        return None

    # Sub-path: try explicit, then with extensions.
    if base in ctx.path_set:
        return base
    for ext in (".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"):
        cand = base + ext if not ext.startswith("/") else base + ext
        if cand in ctx.path_set:
            return cand
    return None
