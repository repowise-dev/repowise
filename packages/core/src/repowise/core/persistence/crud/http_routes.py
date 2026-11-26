"""CRUD for the route table (``http_routes``), see :mod:`repowise.core.workspace.repo_routes`.

Written whole by every read, with the fingerprint of that read in
``http_routes_stamp``, and read back as :class:`~repowise.core.workspace.repo_routes.Route`.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...workspace.repo_routes import Route
from ..models import HttpRoute, HttpRoutesStamp
from ._shared import _BATCH_SIZE


async def replace_http_routes(
    session: AsyncSession, repository_id: str, routes: Sequence[Route], fingerprint: str
) -> int:
    """Replace the repository's routes with *routes* and stamp *fingerprint*; returns rows written."""
    rows = [
        {
            "repository_id": repository_id,
            "method": r.method,
            "path": r.path,
            "file_path": r.file_path,
            "line": r.line,
            "framework": r.framework,
            "handler_symbol_id": r.handler_symbol_id,
            "handler": r.handler,
            "service": r.service,
        }
        for r in routes
    ]
    await session.execute(delete(HttpRoute).where(HttpRoute.repository_id == repository_id))
    for i in range(0, len(rows), _BATCH_SIZE):
        await session.execute(insert(HttpRoute), rows[i : i + _BATCH_SIZE])
    await stamp_routes_fingerprint(session, repository_id, fingerprint)
    return len(rows)


async def stamp_routes_fingerprint(
    session: AsyncSession, repository_id: str, fingerprint: str | None
) -> None:
    """Record the read the stored routes come from; None makes the next update re-read."""
    await session.merge(HttpRoutesStamp(repository_id=repository_id, fingerprint=fingerprint))
    await session.flush()


async def list_http_routes(session: AsyncSession, repository_id: str) -> list[Route]:
    """The repository's stored routes, in file and line order."""
    result = await session.execute(
        select(HttpRoute)
        .where(HttpRoute.repository_id == repository_id)
        .order_by(HttpRoute.file_path, HttpRoute.line, HttpRoute.method, HttpRoute.path)
    )
    return [
        Route(
            method=row.method,
            path=row.path,
            framework=row.framework,
            file_path=row.file_path,
            line=row.line,
            handler_symbol_id=row.handler_symbol_id,
            handler=row.handler,
            service=row.service,
        )
        for row in result.scalars()
    ]


async def stored_routes_fingerprint(session: AsyncSession, repository_id: str) -> str | None:
    """The fingerprint of the read that last wrote the repository's routes, or None."""
    return (
        await session.execute(
            select(HttpRoutesStamp.fingerprint).where(
                HttpRoutesStamp.repository_id == repository_id
            )
        )
    ).scalar_one_or_none()


__all__ = [
    "list_http_routes",
    "replace_http_routes",
    "stamp_routes_fingerprint",
    "stored_routes_fingerprint",
]
