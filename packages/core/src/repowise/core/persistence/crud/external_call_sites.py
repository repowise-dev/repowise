"""CRUD for calls into packages (``external_call_sites``).

Written from a graph build (:func:`replace_external_call_sites`), stamped with
:data:`~repowise.core.ingestion.external_calls.EXTERNAL_CALL_SITES_VERSION`, and
read back as :class:`~repowise.core.ingestion.external_calls.ExternalCallSite`.
A reader must check :func:`external_call_sites_complete` first: an incomplete
table means "unknown", never "not called".
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import fields

from sqlalchemy import delete, insert, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from ...ingestion.external_calls import EXTERNAL_CALL_SITES_VERSION, ExternalCallSite
from ..models import ExternalCallSiteRow, ExternalCallSitesStamp
from ._shared import _BATCH_SIZE

_COLUMNS = tuple(f.name for f in fields(ExternalCallSite))


async def replace_external_call_sites(
    session: AsyncSession,
    repository_id: str,
    sites: Mapping[str, Iterable[ExternalCallSite]],
    *,
    scope: Collection[str] | None,
    live_paths: Collection[str],
    complete: bool = True,
) -> int:
    """Store one graph build's package calls and stamp the current version.

    *scope* None (a full build): every row is replaced. Otherwise only the files
    in *scope* (the ones the build re-resolved) are replaced, and rows of files
    outside *live_paths* (gone from the index) are dropped. *complete* False (a
    build whose check raised on some call) leaves the stamp empty, so readers
    answer "unknown" and the next update rebuilds. Returns rows written.
    """
    if scope is None:
        await session.execute(
            delete(ExternalCallSiteRow).where(ExternalCallSiteRow.repository_id == repository_id)
        )
    else:
        stored = set(
            (
                await session.execute(
                    select(ExternalCallSiteRow.file_path)
                    .where(ExternalCallSiteRow.repository_id == repository_id)
                    .distinct()
                )
            ).scalars()
        )
        live = set(live_paths)
        stale = sorted(p for p in stored if p in scope or p not in live)
        for i in range(0, len(stale), _BATCH_SIZE):
            await session.execute(
                delete(ExternalCallSiteRow).where(
                    ExternalCallSiteRow.repository_id == repository_id,
                    ExternalCallSiteRow.file_path.in_(stale[i : i + _BATCH_SIZE]),
                )
            )
    rows: dict[tuple, dict] = {}
    for path, file_sites in sites.items():
        if scope is not None and path not in scope:
            continue
        for site in file_sites:
            row = {"repository_id": repository_id, **site.as_dict()}
            key = (
                site.file_path,
                site.line,
                site.caller_symbol_id,
                site.local_name,
                site.symbol_path,
            )
            rows.setdefault(key, row)
    values = list(rows.values())
    for i in range(0, len(values), _BATCH_SIZE):
        await session.execute(insert(ExternalCallSiteRow), values[i : i + _BATCH_SIZE])
    # Rows from a build that could not check every call are kept but unstamped.
    version = EXTERNAL_CALL_SITES_VERSION if complete else None
    await stamp_external_call_sites(session, repository_id, version)
    return len(values)


async def stamp_external_call_sites(
    session: AsyncSession, repository_id: str, version: int | None
) -> None:
    """Record the version the stored rows hold; None marks them incomplete."""
    await session.merge(ExternalCallSitesStamp(repository_id=repository_id, version=version))
    await session.flush()


async def external_call_sites_version(session: AsyncSession, repository_id: str) -> int | None:
    """The version stamped on the repository's rows, or None when never written."""
    return (
        await session.execute(
            select(ExternalCallSitesStamp.version).where(
                ExternalCallSitesStamp.repository_id == repository_id
            )
        )
    ).scalar_one_or_none()


async def external_call_sites_complete(session: AsyncSession, repository_id: str) -> bool:
    """Whether the stored rows are every proven package call of the current version."""
    return await external_call_sites_version(session, repository_id) == EXTERNAL_CALL_SITES_VERSION


async def list_external_call_sites(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: Collection[str] | None = None,
    packages: Collection[tuple[str, str]] | None = None,
    external_nodes: Collection[str] | None = None,
    caller_symbol_ids: Collection[str] | None = None,
) -> list[ExternalCallSite]:
    """The repository's stored package calls, in file and line order.

    Each filter, when given, keeps only rows matching one of its values:
    *packages* as ``(ecosystem, package)`` pairs. Each is one ``IN`` list, so a
    caller holding thousands of values reads unfiltered and narrows in Python.
    """
    stmt = select(ExternalCallSiteRow).where(ExternalCallSiteRow.repository_id == repository_id)
    if file_paths is not None:
        stmt = stmt.where(ExternalCallSiteRow.file_path.in_(list(file_paths)))
    if packages is not None:
        stmt = stmt.where(
            tuple_(ExternalCallSiteRow.ecosystem, ExternalCallSiteRow.package).in_(list(packages))
        )
    if external_nodes is not None:
        stmt = stmt.where(ExternalCallSiteRow.external_node.in_(list(external_nodes)))
    if caller_symbol_ids is not None:
        stmt = stmt.where(ExternalCallSiteRow.caller_symbol_id.in_(list(caller_symbol_ids)))
    stmt = stmt.order_by(
        ExternalCallSiteRow.file_path,
        ExternalCallSiteRow.line,
        ExternalCallSiteRow.caller_symbol_id,
        ExternalCallSiteRow.symbol_path,
    )
    result = await session.execute(stmt)
    return [
        ExternalCallSite.from_dict({c: getattr(row, c) for c in _COLUMNS})
        for row in result.scalars()
    ]


__all__ = [
    "external_call_sites_complete",
    "external_call_sites_version",
    "list_external_call_sites",
    "replace_external_call_sites",
    "stamp_external_call_sites",
]
