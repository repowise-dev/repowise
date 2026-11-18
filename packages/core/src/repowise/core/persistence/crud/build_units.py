"""CRUD operations for build units (``build_units``, ``build_unit_deps``, ``build_unit_files``).

A repository's units are written whole by every run that collected them and
read back into the same :class:`BuildUnitIndex`, so no reader re-reads a
manifest. An ecosystem whose reader failed keeps the rows it already had.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import Select, and_, delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...build_units import BuildUnit, BuildUnitIndex
from ..models import BuildUnitDep, BuildUnitFile, BuildUnitRow
from ._shared import _BATCH_SIZE

_TABLES = (BuildUnitFile.__table__, BuildUnitDep.__table__, BuildUnitRow.__table__)


async def replace_build_units(
    session: AsyncSession, repository_id: str, index: BuildUnitIndex
) -> int:
    """Replace the repository's stored units with *index*; returns the units written.

    One delete per table, then bulk inserts, so a file or unit the index no
    longer has (a deleted file, a removed workspace member) leaves no row.
    The stored rows of each of ``index.failed_ecosystems`` are kept instead
    (:func:`_keep_failed`), so one broken reader never empties its ecosystem.
    """
    if index.failed_ecosystems:
        index = _keep_failed(
            index, await _read(session, repository_id, index.failed_ecosystems)
        )
    units = [
        {
            "repository_id": repository_id,
            "unit_id": unit.id,
            "ecosystem": unit.ecosystem,
            "name": unit.name,
            "root": unit.root,
            "manifest": unit.manifest,
            "source": unit.source,
        }
        for unit in index.units.values()
    ]
    deps = [
        {"repository_id": repository_id, "unit_id": unit.id, "dep_unit_id": dep}
        for unit in index.units.values()
        for dep in unit.deps
    ]
    files = [
        {"repository_id": repository_id, "path": path, "unit_id": unit_id}
        for path, unit_ids in index.file_owners.items()
        for unit_id in unit_ids
    ]
    for table in _TABLES:
        await session.execute(delete(table).where(table.c.repository_id == repository_id))
    for table, rows in (
        (BuildUnitRow.__table__, units),
        (BuildUnitDep.__table__, deps),
        (BuildUnitFile.__table__, files),
    ):
        for i in range(0, len(rows), _BATCH_SIZE):
            await session.execute(insert(table), rows[i : i + _BATCH_SIZE])
    await session.flush()
    return len(units)


def _keep_failed(fresh: BuildUnitIndex, kept: BuildUnitIndex) -> BuildUnitIndex:
    """*fresh* with the stored units of its failed ecosystems, *kept*, put back.

    A specific unit wins at a root over a ``manifest:`` fallback unit, in
    both directions. A failed reader left its manifests undecided, so the
    fallback may have given their roots ``manifest:`` units: one at the root
    of a kept unit stands in for it and is dropped, with the fresh units of a
    failed ecosystem (a reader that failed produced none). When the fallback
    itself failed, a kept ``manifest:`` unit at the root of a fresh unit is
    dropped instead. A kept unit keeps its stored deps. A kept file row stays
    only while *fresh* still owns the path, so a deleted file leaves no row; a
    path *fresh* gave only to a dropped unit goes back to its kept owners.
    """
    dropped = {uid for uid, unit in fresh.units.items() if unit.ecosystem in fresh.failed_ecosystems}
    dropped |= set(kept.units) & set(fresh.units)
    dropped |= _fallbacks_at(fresh, {unit.root for unit in kept.units.values()})
    units = {uid: unit for uid, unit in fresh.units.items() if uid not in dropped}
    stale = _fallbacks_at(kept, {unit.root for unit in units.values()})
    units.update((uid, unit) for uid, unit in kept.units.items() if uid not in stale)
    owners: dict[str, tuple[str, ...]] = {}
    for path, ids in fresh.file_owners.items():
        merged = tuple(uid for uid in ids if uid not in dropped) + tuple(
            uid for uid in kept.file_owners.get(path, ()) if uid not in stale
        )
        if merged:
            owners[path] = merged
    return BuildUnitIndex(units=units, file_owners=owners)


def _fallbacks_at(index: BuildUnitIndex, roots: set[str]) -> set[str]:
    """Ids of *index*'s ``manifest:`` fallback units whose root is one of *roots*."""
    return {
        uid for uid, unit in index.units.items() if unit.ecosystem == "manifest" and unit.root in roots
    }


async def load_build_units(session: AsyncSession, repository_id: str) -> BuildUnitIndex:
    """The repository's stored units as a :class:`BuildUnitIndex`; empty when none are stored."""
    return await _read(session, repository_id)


async def _read(
    session: AsyncSession, repository_id: str, ecosystems: Collection[str] | None = None
) -> BuildUnitIndex:
    """The stored units of *repository_id*, only those of *ecosystems* when given."""
    unit_filter = BuildUnitRow.repository_id == repository_id
    if ecosystems is not None:
        unit_filter = and_(unit_filter, BuildUnitRow.ecosystem.in_(sorted(ecosystems)))
    deps: dict[str, list[str]] = {}
    dep_rows = select(BuildUnitDep.unit_id, BuildUnitDep.dep_unit_id).where(
        BuildUnitDep.repository_id == repository_id
    )
    for unit_id, dep in await session.execute(_kept(dep_rows, BuildUnitDep, ecosystems)):
        deps.setdefault(unit_id, []).append(dep)
    units = {
        row.unit_id: BuildUnit(
            id=row.unit_id,
            ecosystem=row.ecosystem,
            name=row.name,
            root=row.root,
            manifest=row.manifest,
            source=row.source,
            deps=tuple(sorted(deps.get(row.unit_id, ()))),
        )
        for row in await session.execute(
            select(
                BuildUnitRow.unit_id,
                BuildUnitRow.ecosystem,
                BuildUnitRow.name,
                BuildUnitRow.root,
                BuildUnitRow.manifest,
                BuildUnitRow.source,
            ).where(unit_filter)
        )
    }
    owners: dict[str, list[str]] = {}
    file_rows = select(BuildUnitFile.path, BuildUnitFile.unit_id).where(
        BuildUnitFile.repository_id == repository_id
    )
    for path, unit_id in await session.execute(_kept(file_rows, BuildUnitFile, ecosystems)):
        owners.setdefault(path, []).append(unit_id)
    return BuildUnitIndex(units=units, file_owners={p: tuple(ids) for p, ids in owners.items()})


def _kept(stmt: Select, model: type, ecosystems: Collection[str] | None) -> Select:
    """*stmt* over *model*'s rows, limited to units of *ecosystems* when given."""
    if ecosystems is None:
        return stmt
    return stmt.join(
        BuildUnitRow,
        and_(
            BuildUnitRow.repository_id == model.repository_id,
            BuildUnitRow.unit_id == model.unit_id,
        ),
    ).where(BuildUnitRow.ecosystem.in_(sorted(ecosystems)))


__all__ = ["load_build_units", "replace_build_units"]
