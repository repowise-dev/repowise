"""What every other lens holds for a set of files, for a drawer's see-also.

A thin adapter: one read per lens, scoped to the files in the body, then
``related_work`` in core groups and caps them. Fix first comes from its cached
loader, so asking costs a queue build at most once per store write.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.related_work import related_work
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.fix_first import load_fix_first
from repowise.core.persistence.crud.analysis.performance import (
    list_performance_opportunities,
)
from repowise.server.deps import get_db_session
from repowise.server.schemas import RelatedWorkRequest, RelatedWorkResponse

from ._router import router

MAX_FILES = 200
"""Files one request may name. A drawer asks about one; a selection, a few."""

_ROWS_CEILING = 10_000
"""Ceiling on rows one paged lens read returns for the whole request.

The per-file totals need every row for the named files, and the paged readers
always take a limit. 200 files never come near it on a real index; past it
the totals undercount. Upgrade path: a grouped count per file.
"""


def _validated(raw: list[str]) -> list[str]:
    """Repo-relative slash paths, unique, 1..MAX_FILES; 422 on anything else."""
    if not 1 <= len(raw) <= MAX_FILES:
        raise HTTPException(status_code=422, detail=f"file_paths must name 1 to {MAX_FILES} files")
    paths: list[str] = []
    for value in raw:
        path = value.strip().replace("\\", "/").removeprefix("./")
        if (
            not path
            or path.startswith("/")
            or (len(path) > 1 and path[1] == ":")
            or ".." in path.split("/")
        ):
            raise HTTPException(
                status_code=422, detail=f"not a repo-relative file path: {value!r}"
            )
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise HTTPException(status_code=422, detail="file_paths must be unique")
    return paths


@router.post(
    "/api/repos/{repo_id}/health/related-work",
    response_model=RelatedWorkResponse,
    # An item states only what its lens knows; a null would claim a field.
    response_model_exclude_none=True,
)
async def get_related_work(
    repo_id: str,
    payload: RelatedWorkRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Per file, what findings, Fix first, refactoring, performance and dead
    code each hold, capped per lens with the full count beside it."""
    paths = _validated(payload.file_paths)
    findings = await crud.get_health_findings(
        session,
        repo_id,
        file_paths=paths,
        # Performance findings are the evidence behind the performance lens's
        # opportunities; listing them as findings would show one cause twice.
        exclude_dimensions=("performance",),
    )
    queue = await load_fix_first(session, repo_id, limit=None)
    refactoring, _ = await crud.list_refactoring_opportunities(
        session, repo_id, file_paths=paths, order="rank", limit=_ROWS_CEILING
    )
    # Every execution context: the reader opened these files, so the question
    # is what was found in them, not whether they are production code.
    performance, _ = await list_performance_opportunities(
        session, repo_id, file_paths=tuple(paths), limit=_ROWS_CEILING
    )
    dead_code = await crud.get_dead_code_findings(session, repo_id, file_paths=paths)
    return related_work(
        paths,
        findings=findings,
        fix_first=queue.items,
        refactoring=refactoring,
        performance=performance,
        dead_code=dead_code,
    ).as_dict()


__all__ = ["get_related_work"]
