"""Collect a repo's build units from the resolver indexes: the one entry point."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import structlog

from repowise.core.build_units.adapters import (
    Found,
    cargo,
    cmake,
    dart,
    dotnet,
    elixir,
    go,
    gradle,
    manifest_roots,
    maven,
    npm,
    pascal,
    php,
    python,
    swift,
)
from repowise.core.build_units.model import BuildUnit, BuildUnitIndex, build_file_languages
from repowise.core.ingestion.resolvers.context import ResolverContext

log = structlog.get_logger(__name__)

# Ecosystem adapters, each reading its resolver index through the accessor
# that caches it on the context. ``manifest_roots`` runs after them all and
# fills in the package roots they left: a root is skipped only when every
# manifest in it is one an adapter actually read and ruled on, so a manifest
# that failed to parse still gets its root a unit.
_ADAPTERS: tuple[tuple[str, Callable[[ResolverContext], Found]], ...] = (
    ("dotnet", dotnet.units),
    ("cargo", cargo.units),
    ("gradle", gradle.units),
    ("npm", npm.units),
    ("go", go.units),
    ("maven", maven.units),
    ("php", php.units),
    ("python", python.units),
    ("dart", dart.units),
    ("swift", swift.units),
    ("pascal", pascal.units),
    ("elixir", elixir.units),
    ("cmake", cmake.units),
)


def _add(
    found: Found,
    units: dict[str, BuildUnit],
    listed: dict[str, list[str]],
    path_set: set[str],
) -> None:
    """Add *found*'s units to *units*, keeping an id already present, and their indexed files to *listed*."""
    for unit, files in sorted(found.claims, key=lambda claim: (claim[0].root, claim[0].id)):
        if unit.id in units:
            log.warning("build_units_duplicate_id", unit_id=unit.id, root=unit.root)
            continue
        units[unit.id] = unit
        for path in files:
            if path in path_set:
                listed.setdefault(path, []).append(unit.id)


def _keep_fallback(
    fallback: list[tuple[BuildUnit, tuple[str, ...]]], units: dict[str, BuildUnit]
) -> None:
    """Add each *fallback* unit to *units* unless an adapter's package unit holds its root.

    A unit declared in a build file holds no package root, so it never hides
    a fallback; a fallback whose id a unit already names is kept regardless.
    """
    claimed = {u.root for u in units.values() if not build_file_languages(u)}
    referenced = {dep for unit in units.values() for dep in unit.deps}
    for unit, _files in fallback:
        if unit.root not in claimed or unit.id in referenced:
            units[unit.id] = unit


def collect_build_units(ctx: ResolverContext) -> BuildUnitIndex:
    """Every build unit of the repo *ctx* resolves, and the units owning each indexed file.

    An ecosystem the repo does not use costs a cheap check. One whose index
    fails to build is skipped with one warning and named in
    ``failed_ecosystems``, and its roots are left to the manifest fallback.
    A unit id produced twice keeps the first by root. An unreferenced
    fallback loses to a specific adapter's unit at the same root, unless that
    unit is declared in a build file (``build_file_languages``): a
    ``CMakeLists.txt`` is no package manifest, so the adapter never decided
    the root's package. A fallback
    whose id a collected unit already names is kept, so a path dependency on
    a dropped duplicate still links ``manifest:<root>`` when another adapter
    holds that directory. Only files in ``ctx.path_set`` get an owner.
    """
    units: dict[str, BuildUnit] = {}
    listed: dict[str, list[str]] = {}
    decided: set[str] = set()
    failed: set[str] = set()
    for name, adapter in _ADAPTERS:
        try:
            found = adapter(ctx)
        except Exception as exc:  # one broken index must not cost every other unit
            log.warning("build_units_adapter_failed", ecosystem=name, error=str(exc))
            failed.add(name)
            continue
        decided.update(unit.manifest for unit, _files in found.claims)
        decided.update(found.ruled_out)
        _add(found, units, listed, ctx.path_set)

    try:
        fallback = manifest_roots.units(ctx, decided.__contains__)
    except Exception as exc:
        log.warning("build_units_adapter_failed", ecosystem="manifest", error=str(exc))
        failed.add("manifest")
        fallback = []
    _keep_fallback(fallback, units)

    resolved = [
        replace(unit, deps=tuple(sorted({d for d in unit.deps if d in units and d != unit.id})))
        for unit in units.values()
    ]
    return BuildUnitIndex.from_units(
        resolved, ctx.sorted_paths, listed, failed_ecosystems=frozenset(failed)
    )
