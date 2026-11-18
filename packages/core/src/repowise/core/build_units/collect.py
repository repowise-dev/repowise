"""Collect a repo's build units from the resolver indexes: the one entry point."""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import replace

import structlog

from repowise.core.build_units.adapters import cargo, dotnet, go, gradle, manifest_roots, npm
from repowise.core.build_units.model import BuildUnit, BuildUnitIndex
from repowise.core.ingestion.resolvers.context import ResolverContext

log = structlog.get_logger(__name__)

Adapter = Callable[[ResolverContext], list[tuple[BuildUnit, tuple[str, ...]]]]

# Ecosystem adapters, each reading its resolver index through the accessor
# that caches it on the context, with the manifest names it is the authority
# on. ``manifest_roots`` runs after them all and fills in the package roots
# they left: a root holding only manifests of an adapter that found units is
# left out, since that adapter already decided it is no unit (a virtual
# Cargo workspace, a ``package.json`` outside the declared workspace).
_ADAPTERS: tuple[tuple[str, Adapter, tuple[str, ...]], ...] = (
    ("dotnet", dotnet.units, ("*.csproj", "*.vbproj")),
    ("cargo", cargo.units, ("cargo.toml",)),
    (
        "gradle",
        gradle.units,
        ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"),
    ),
    ("npm", npm.units, ("package.json",)),
    ("go", go.units, ("go.mod",)),
)


def _run(name: str, adapter: Adapter, ctx: ResolverContext) -> list[tuple[BuildUnit, tuple[str, ...]]]:
    """*adapter*'s units, sorted by root then id; none when its index fails to build."""
    try:
        found = adapter(ctx)
    except Exception as exc:  # one broken manifest must not cost every other unit
        log.warning("build_units_adapter_failed", adapter=name, error=str(exc))
        return []
    return sorted(found, key=lambda claim: (claim[0].root, claim[0].id))


def collect_build_units(ctx: ResolverContext) -> BuildUnitIndex:
    """Every build unit of the repo *ctx* resolves, and the units owning each indexed file.

    An ecosystem the repo does not use costs a cheap check, and one whose
    index fails to build is skipped, its roots left to the manifest fallback.
    A unit id produced twice keeps the first by root. A specific adapter's
    unit wins over the fallback's at the same root. Only files in
    ``ctx.path_set`` get an owner.
    """
    units: dict[str, BuildUnit] = {}
    listed: dict[str, list[str]] = {}
    authoritative: list[str] = []
    for name, adapter, manifests in _ADAPTERS:
        found = _run(name, adapter, ctx)
        if found:
            authoritative.extend(manifests)
        for unit, files in found:
            if unit.id in units:
                log.debug("build_units_duplicate_id", unit_id=unit.id, root=unit.root)
                continue
            units[unit.id] = unit
            for path in files:
                if path in ctx.path_set:
                    listed.setdefault(path, []).append(unit.id)

    def decided(manifest_name: str) -> bool:
        lowered = manifest_name.lower()
        return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in authoritative)

    claimed = {unit.root for unit in units.values()}
    fallback = _run("manifest_roots", lambda c: manifest_roots.units(c, decided), ctx)
    for unit, _files in fallback:
        if unit.root not in claimed:
            units[unit.id] = unit

    resolved = [
        replace(unit, deps=tuple(sorted({d for d in unit.deps if d in units and d != unit.id})))
        for unit in units.values()
    ]
    return BuildUnitIndex.from_units(resolved, ctx.sorted_paths, listed)
