"""Dart packages from every ``pubspec.yaml`` naming a package.

A Dart package is declared by a top-level ``name``. A ``pubspec.yaml`` that does
not parse, or has no ``name`` and no ``workspace`` key, is left to the manifest
fallback (with a warning if unreadable). A pubspec with no ``name`` but a
``workspace`` key is a pub workspace root and is ruled out (no unit, no
fallback). A name taken twice keeps the first root in sorted order; the later
falls back to the manifest adapter.

Deps are the in-repo packages a ``pubspec.yaml`` reaches through ``dependencies``
and ``dev_dependencies`` with a ``path:`` dependency. The path is resolved
against the package root, and the directory it points at is matched against
the root of other units (not by the dependency's key name). A path pointing
outside the repo, an absolute path, or at a directory without a Dart unit links
nothing.

Ceiling: hosted, git and sdk dependencies are external. ``dependency_overrides``
is not read. Members of a pub workspace that depend on each other by version
only (no ``path:``) are not linked; reading ``pubspec.lock`` /
``package_config.json`` is the upgrade path.
"""

from __future__ import annotations

import posixpath
from pathlib import Path

import structlog
import yaml

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.adapters.manifest_roots import package_manifests
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers.context import ResolverContext

log = structlog.get_logger(__name__)

_MANIFEST = "pubspec.yaml"


def _parse_pubspec(manifest: Path) -> dict | None:
    """Parse *manifest* as YAML, return the dict or None (logged)."""
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    # ValueError: a timestamp-shaped scalar that is no real date (2023-02-30).
    except (OSError, ValueError, yaml.YAMLError) as exc:
        log.warning("build_units_pubspec_unreadable", manifest=str(manifest), error=str(exc))
        return None
    return data if isinstance(data, dict) else None


def _resolve_path_dep(root: str, dep_path: str) -> str | None:
    """Resolve a ``path:`` dependency against the package root, or None if outside repo.

    Normalises backslashes and ``"."`` to ``""``. Absolute paths (``/...`` or
    Windows drive) are treated as outside the repo.
    """
    if dep_path.startswith("/") or (len(dep_path) >= 2 and dep_path[1] == ":"):
        return None
    normalized = posixpath.normpath(posixpath.join(root, dep_path.replace("\\", "/")))
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized if normalized != "." else ""


def _dep_path(spec: object) -> str | None:
    """The ``path:`` string of a dependency spec, if it has one."""
    path = spec.get("path") if isinstance(spec, dict) else None
    return path if isinstance(path, str) else None


def _collect_dep_roots(data: dict, root: str) -> set[str]:
    """Set of target roots from ``dependencies`` and ``dev_dependencies`` path deps."""
    tables = [data.get(key) for key in ("dependencies", "dev_dependencies")]
    specs = [spec for table in tables if isinstance(table, dict) for spec in table.values()]
    targets = (
        _resolve_path_dep(root, path) for path in map(_dep_path, specs) if path is not None
    )
    return {target for target in targets if target is not None}


def _manifest_path(root: str) -> str:
    return f"{root}/{_MANIFEST}" if root else _MANIFEST


def _parse_all(ctx: ResolverContext, repo: Path) -> dict[str, dict]:
    """Parse each pubspec once, keyed by package root."""
    parsed: dict[str, dict] = {}
    for root, names in package_manifests(ctx).items():
        if _MANIFEST in names:
            data = _parse_pubspec(repo / root / _MANIFEST if root else repo / _MANIFEST)
            if data is not None:
                parsed[root] = data
    return parsed


def _select_names(parsed: dict[str, dict]) -> tuple[dict[str, str], set[str]]:
    """Map each root to its ``dart:<name>`` id (first root wins a taken name).

    Also returns the manifests of pub workspace roots, which are ruled out.
    """
    by_root: dict[str, str] = {}
    ruled_out: set[str] = set()
    for root, data in sorted(parsed.items()):
        name = data.get("name")
        if not isinstance(name, str) or not name:
            if "workspace" in data:
                ruled_out.add(_manifest_path(root))
            continue
        unit_id = f"dart:{name}"
        if unit_id not in by_root.values():
            by_root[root] = unit_id
    return by_root, ruled_out


def _build_unit(root: str, uid: str, data: dict, by_root: dict[str, str]) -> BuildUnit:
    dep_roots = _collect_dep_roots(data, root)
    return BuildUnit(
        id=uid,
        ecosystem="dart",
        name=data["name"],
        root=root,
        manifest=_manifest_path(root),
        source="pubspec",
        deps=tuple({by_root[target] for target in dep_roots if target in by_root}),
    )


def units(ctx: ResolverContext) -> Found:
    if ctx.repo_path is None or not any(path.endswith(".dart") for path in ctx.path_set):
        return Found([])
    parsed = _parse_all(ctx, ctx.repo_path)
    by_root, ruled_out = _select_names(parsed)
    out = [(_build_unit(root, uid, parsed[root], by_root), ()) for root, uid in by_root.items()]
    return Found(out, frozenset(ruled_out))
