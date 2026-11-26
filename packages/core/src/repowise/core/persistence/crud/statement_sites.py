"""CRUD for the statement-site index (``statement_sites``), see :mod:`repowise.core.workspace.statement_sites`.

Written whole by every read, with the fingerprint of that read in
``statement_sites_stamp``, and read back as
:class:`~repowise.core.evidence.statement_join.StatementSite`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...evidence.statement_join import Columns, StatementSite
from ..models import StatementSiteRow, StatementSitesStamp
from ._shared import _BATCH_SIZE


def _columns_json(columns: Columns | None) -> str | None:
    return json.dumps(columns.as_json(), sort_keys=True) if columns is not None else None


def _columns(raw: str | None) -> Columns | None:
    return Columns.from_json(json.loads(raw)) if raw else None


async def replace_statement_sites(
    session: AsyncSession,
    repository_id: str,
    sites: Sequence[StatementSite],
    fingerprint: str,
    inputs: Mapping[str, Any] | None = None,
) -> int:
    """Replace the repository's sites with *sites* and stamp *fingerprint* and the
    read's *inputs* (the files and names it used); returns rows written."""
    rows = [
        {
            "repository_id": repository_id,
            "ordinal": i,
            "file_path": s.file_path,
            "line": s.line,
            "channel": s.channel,
            "target": s.target,
            "name": s.name,
            "schema_name": s.schema,
            "verb": s.verb,
            "via": s.via,
            "symbol_id": s.symbol_id,
            "columns_json": _columns_json(s.columns),
            "filters_json": _columns_json(s.filters),
        }
        for i, s in enumerate(sites)
    ]
    await session.execute(
        delete(StatementSiteRow).where(StatementSiteRow.repository_id == repository_id)
    )
    for i in range(0, len(rows), _BATCH_SIZE):
        await session.execute(insert(StatementSiteRow), rows[i : i + _BATCH_SIZE])
    await stamp_sites_fingerprint(session, repository_id, fingerprint, inputs)
    return len(rows)


async def stamp_sites_fingerprint(
    session: AsyncSession,
    repository_id: str,
    fingerprint: str | None,
    inputs: Mapping[str, Any] | None = None,
) -> None:
    """Record the read the stored sites come from; None makes the next update re-read."""
    await session.merge(
        StatementSitesStamp(
            repository_id=repository_id,
            fingerprint=fingerprint,
            inputs_json=json.dumps(dict(inputs), sort_keys=True) if inputs is not None else None,
        )
    )
    await session.flush()


async def list_statement_sites(session: AsyncSession, repository_id: str) -> list[StatementSite]:
    """The repository's stored statement sites, in the order they were read."""
    result = await session.execute(
        select(StatementSiteRow)
        .where(StatementSiteRow.repository_id == repository_id)
        .order_by(StatementSiteRow.ordinal)
    )
    return [
        StatementSite(
            file_path=row.file_path,
            line=row.line,
            channel=row.channel,
            target=row.target,
            name=row.name,
            verb=row.verb,
            via=row.via,
            symbol_id=row.symbol_id,
            schema=row.schema_name,
            columns=_columns(row.columns_json),
            filters=_columns(row.filters_json),
        )
        for row in result.scalars()
    ]


async def stored_sites_stamp(
    session: AsyncSession, repository_id: str
) -> tuple[str | None, dict[str, Any] | None]:
    """The fingerprint and inputs of the read that last wrote the sites, or Nones."""
    row = (
        await session.execute(
            select(StatementSitesStamp.fingerprint, StatementSitesStamp.inputs_json).where(
                StatementSitesStamp.repository_id == repository_id
            )
        )
    ).one_or_none()
    if row is None:
        return None, None
    return row.fingerprint, json.loads(row.inputs_json) if row.inputs_json else None


async def stored_sites_fingerprint(session: AsyncSession, repository_id: str) -> str | None:
    """The fingerprint of the read that last wrote the repository's sites, or None."""
    return (
        await session.execute(
            select(StatementSitesStamp.fingerprint).where(
                StatementSitesStamp.repository_id == repository_id
            )
        )
    ).scalar_one_or_none()


__all__ = [
    "list_statement_sites",
    "replace_statement_sites",
    "stamp_sites_fingerprint",
    "stored_sites_fingerprint",
    "stored_sites_stamp",
]
