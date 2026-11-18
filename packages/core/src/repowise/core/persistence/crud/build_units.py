"""CRUD operations for build units (``build_units``, ``build_unit_deps``, ``build_unit_files``).

A repository's units are written whole by every run that collected them and
read back into the same :class:`BuildUnitIndex`, so no reader re-reads a
manifest.
"""

from __future__ import annotations

from sqlalchemy import delete, insert, select
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
    """
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


async def load_build_units(session: AsyncSession, repository_id: str) -> BuildUnitIndex:
    """The repository's stored units as a :class:`BuildUnitIndex`; empty when none are stored."""
    deps: dict[str, list[str]] = {}
    for unit_id, dep in await session.execute(
        select(BuildUnitDep.unit_id, BuildUnitDep.dep_unit_id).where(
            BuildUnitDep.repository_id == repository_id
        )
    ):
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
            ).where(BuildUnitRow.repository_id == repository_id)
        )
    }
    owners: dict[str, list[str]] = {}
    for path, unit_id in await session.execute(
        select(BuildUnitFile.path, BuildUnitFile.unit_id).where(
            BuildUnitFile.repository_id == repository_id
        )
    ):
        owners.setdefault(path, []).append(unit_id)
    return BuildUnitIndex(units=units, file_owners={p: tuple(ids) for p, ids in owners.items()})


__all__ = ["load_build_units", "replace_build_units"]
