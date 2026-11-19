"""Collect a repo's build units from the resolver indexes: the one entry point."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import structlog

from repowise.core.build_units.adapters import (
    Found,
    cargo,
    dart,
    dotnet,
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
from repowise.core.build_units.model import BuildUnit, BuildUnitIndex
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


def collect_build_units(ctx: ResolverContext) -> BuildUnitIndex:
    """Every build unit of the repo *ctx* resolves, and the units owning each indexed file.

    An ecosystem the repo does not use costs a cheap check. One whose index
    fails to build is skipped with one warning and named in
    ``failed_ecosystems``, and its roots are left to the manifest fallback.
    A unit id produced twice keeps the first by root. A specific adapter's
    unit wins over the fallback's at the same root. Only files in
    ``ctx.path_set`` get an owner.
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
    claimed = {unit.root for unit in units.values()}
    for unit, _files in fallback:
        if unit.root not in claimed:
            units[unit.id] = unit

    resolved = [
        replace(unit, deps=tuple(sorted({d for d in unit.deps if d in units and d != unit.id})))
        for unit in units.values()
    ]
    return BuildUnitIndex.from_units(
        resolved, ctx.sorted_paths, listed, failed_ecosystems=frozenset(failed)
    )
