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
        text = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("build_units_pubspec_unreadable", manifest=str(manifest), error=str(exc))
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        log.warning("build_units_pubspec_unreadable", manifest=str(manifest), error=str(exc))
        return None
    if not isinstance(data, dict):
        return None
    return data


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


def _collect_dep_roots(data: dict, root: str) -> set[str]:
    """Set of target roots from ``dependencies`` and ``dev_dependencies`` path deps."""
    dep_roots: set[str] = set()
    for key in ("dependencies", "dev_dependencies"):
        table = data.get(key)
        if not isinstance(table, dict):
            continue
        for spec in table.values():
            if isinstance(spec, dict):
                path = spec.get("path")
                if isinstance(path, str):
                    target = _resolve_path_dep(root, path)
                    if target is not None:
                        dep_roots.add(target)
    return dep_roots


def units(ctx: ResolverContext) -> Found:
    if ctx.repo_path is None or not any(path.endswith(".dart") for path in ctx.path_set):
        return Found([])
    repo = ctx.repo_path
    manifests = package_manifests(ctx)

    # Parse each pubspec once, store data per root
    parsed: dict[str, dict] = {}
    for root, names in manifests.items():
        if _MANIFEST in names:
            pubspec = repo / root / _MANIFEST if root else repo / _MANIFEST
            data = _parse_pubspec(pubspec)
            if data is not None:
                parsed[root] = data

    taken: set[str] = set()
    by_root: dict[str, str] = {}
    ruled_out: set[str] = set()

    for root, data in sorted(parsed.items()):
        name = data.get("name")
        has_workspace = "workspace" in data
        if not isinstance(name, str) or not name:
            if has_workspace:
                ruled_out.add(f"{root}/{_MANIFEST}" if root else _MANIFEST)
            continue
        unit_id = f"dart:{name}"
        if unit_id in taken:
            continue
        taken.add(unit_id)
        by_root[root] = unit_id

    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for root, uid in by_root.items():
        data = parsed[root]
        name = data["name"]
        dep_roots = _collect_dep_roots(data, root)
        deps = {by_root[target] for target in dep_roots if target in by_root}
        manifest_path = f"{root}/{_MANIFEST}" if root else _MANIFEST
        unit = BuildUnit(
            id=uid,
            ecosystem="dart",
            name=name,
            root=root,
            manifest=manifest_path,
            source="pubspec",
            deps=tuple(deps),
        )
        out.append((unit, ()))

    return Found(out, frozenset(ruled_out))