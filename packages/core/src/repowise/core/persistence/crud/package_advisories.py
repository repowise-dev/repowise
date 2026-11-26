"""CRUD for the advisory table (``package_advisories``), see :mod:`repowise.core.inventory.advisories`.

Written whole by every import, each row stamped with the document's format and
file hash, and read back as :class:`~repowise.core.inventory.advisories.StoredAdvisories`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...inventory.advisories import Advisory, PackageAdvisories, StoredAdvisories
from ...inventory.osv import AffectedImport
from ..models import PackageAdvisory, _now_utc
from ._shared import _BATCH_SIZE


def _advisory_columns(a: Advisory) -> dict:
    return {
        "advisory_id": a.id,
        "aliases_json": json.dumps(list(a.aliases)),
        "summary": a.summary,
        "severity": a.severity,
        "score": a.score,
        "affected_json": json.dumps(
            [{"path": i.path, "symbols": list(i.symbols)} for i in a.affected]
        ),
        "fixed_json": json.dumps(list(a.fixed)),
        "ranges_json": json.dumps([[list(e) for e in events] for events in a.ranges]),
        "versions_affected_json": json.dumps(list(a.versions)),
    }


async def replace_package_advisories(
    session: AsyncSession,
    repository_id: str,
    packages: Sequence[PackageAdvisories],
    *,
    format: str,
    sha256: str | None,
    source: str | None,
    imported_at: datetime | None = None,
) -> int:
    """Replace the repository's stored document with *packages*; returns rows written."""
    stamp = {
        "repository_id": repository_id,
        "source_format": format,
        "source_sha256": sha256,
        "source": source,
        "imported_at": imported_at or _now_utc(),
    }
    rows: dict[tuple[str, str, str], dict] = {}
    for p in packages:
        base = {
            **stamp,
            "ecosystem": p.ecosystem or "",
            "name": p.name,
            "versions_json": json.dumps(list(p.versions)),
            "direct": p.direct,
        }
        for cols in [_advisory_columns(a) for a in p.advisories] or [{"advisory_id": ""}]:
            # A name spelled twice under one ecosystem keeps its first entry.
            rows.setdefault((base["ecosystem"], p.name, cols["advisory_id"]), {**base, **cols})
    values = list(rows.values())
    await session.execute(
        delete(PackageAdvisory).where(PackageAdvisory.repository_id == repository_id)
    )
    for i in range(0, len(values), _BATCH_SIZE):
        await session.execute(insert(PackageAdvisory), values[i : i + _BATCH_SIZE])
    await session.flush()
    return len(values)


def _advisory(row: PackageAdvisory) -> Advisory:
    return Advisory(
        row.advisory_id,
        tuple(json.loads(row.aliases_json or "[]")),
        row.summary,
        severity=row.severity,
        score=row.score,
        affected=tuple(
            AffectedImport(i.get("path") or "", tuple(i.get("symbols") or ()))
            for i in json.loads(row.affected_json or "[]")
        ),
        fixed=tuple(json.loads(row.fixed_json or "[]")),
        ranges=tuple(
            tuple((k, v) for k, v in events) for events in json.loads(row.ranges_json or "[]")
        ),
        versions=tuple(json.loads(row.versions_affected_json or "[]")),
    )


async def load_package_advisories(
    session: AsyncSession,
    repository_id: str,
    *,
    packages: Sequence[tuple[str | None, str]] | None = None,
) -> StoredAdvisories | None:
    """The repository's stored document, or ``None`` when no import stored one.

    *packages* narrows it to those ``(ecosystem, name)`` pairs, matched as
    :meth:`StoredAdvisories.get` matches.
    """
    result = await session.execute(
        select(PackageAdvisory)
        .where(PackageAdvisory.repository_id == repository_id)
        .order_by(PackageAdvisory.ecosystem, PackageAdvisory.name, PackageAdvisory.advisory_id)
    )
    rows = list(result.scalars())
    if not rows:
        return None
    grouped: dict[tuple[str, str], list[PackageAdvisory]] = {}
    for row in rows:
        grouped.setdefault((row.ecosystem, row.name), []).append(row)
    first = rows[0]
    stored = StoredAdvisories(
        format=first.source_format,
        sha256=first.source_sha256,
        source=first.source,
        imported_at=first.imported_at,
        packages=tuple(
            PackageAdvisories(
                eco or None,
                name,
                tuple(json.loads(group[0].versions_json or "[]")),
                tuple(_advisory(r) for r in group if r.advisory_id),
                group[0].direct,
            )
            for (eco, name), group in grouped.items()
        ),
    )
    if packages is None:
        return stored
    wanted = [p for eco, name in packages if (p := stored.get(eco, name)) is not None]
    return StoredAdvisories(
        stored.format,
        stored.sha256,
        stored.source,
        stored.imported_at,
        tuple(dict.fromkeys(wanted)),
    )


__all__ = ["load_package_advisories", "replace_package_advisories"]
