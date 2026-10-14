"""/api/repos/{repo_id}/changes — merged changes reconstructed from git."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.attribution import attribution_enabled
from repowise.core.changes.collect import build_stored_evidence, cached_evidence, save_evidence
from repowise.core.changes.evidence import ChangeEvidence
from repowise.core.changes.record import change_record
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.server.deps import get_db_session, resolve_request_session_factory, verify_api_key
from repowise.server.schemas.changes import (
    ChangeListResponse,
    ChangeRecord,
    summary_from_record,
)

router = APIRouter(
    prefix="/api/repos",
    tags=["changes"],
    dependencies=[Depends(verify_api_key)],
)

_MAX_PAGE = 200

#: How long a request waits on a checklist build before answering without it
#: (``evidence_building``); the build carries on and keeps its result.
EVIDENCE_WAIT_SECONDS = 15.0

#: One build per change at a time, shared by every request that asks.
_BUILDS: dict[tuple[str, str], asyncio.Task[ChangeEvidence]] = {}


async def _attribution(session: AsyncSession, repo_id: str) -> bool:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return attribution_enabled(repo.local_path)


@router.get("/{repo_id}/changes", response_model=ChangeListResponse)
async def list_changes(
    repo_id: str,
    category: str | None = Query(None, description="One commit category, e.g. fix"),
    shape: str | None = Query(None, description="merge | squash | commit"),
    q: str | None = Query(None, description="Substring of the title, id or a file path"),
    limit: int = Query(50, ge=1, le=_MAX_PAGE),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> ChangeListResponse:
    """A page of changes, newest merged first."""
    attribution = await _attribution(session, repo_id)
    rows, total = await crud.list_changes(
        session, repo_id, category=category, shape=shape, q=q, limit=limit, offset=offset
    )
    return ChangeListResponse(
        items=[summary_from_record(change_record(r, attribution=attribution)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
        attribution=attribution,
    )


@router.get("/{repo_id}/changes/{change_id}", response_model=ChangeRecord)
async def get_change(
    request: Request,
    repo_id: str,
    change_id: str,
    evidence: bool = Query(
        False, description="Include the evidence checklist (built on first read, then kept)"
    ),
    refresh: bool = Query(False, description="Rebuild the evidence checklist"),
    session: AsyncSession = Depends(get_db_session),
) -> ChangeRecord:
    """One change by id (``pr-123``), or the change that landed a commit sha."""
    attribution = await _attribution(session, repo_id)
    row = await crud.get_change(session, repo_id, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Change not found")
    record = change_record(row, attribution=attribution)
    if evidence:
        kept = None if refresh else cached_evidence(row)
        if kept is None:
            repo = await crud.get_repository(session, repo_id)
            factory = resolve_request_session_factory(request)
            task = _build(factory, repo_id, row.change_id, repo.local_path)
            try:
                kept = await asyncio.wait_for(asyncio.shield(task), EVIDENCE_WAIT_SECONDS)
            except TimeoutError:
                record["evidence_building"] = True
        if kept is not None:
            record["evidence"] = kept.to_dict()
    return ChangeRecord(**record, commit_count=len(record["commits"]))


def _build(factory: Any, repo_id: str, change_id: str, root: str) -> asyncio.Task[ChangeEvidence]:
    """The running build for this change, or a new one; a finished build leaves the map."""
    key = (repo_id, change_id)
    task = _BUILDS.get(key)
    if task is None:
        task = asyncio.create_task(_build_and_keep(factory, repo_id, change_id, root))
        _BUILDS[key] = task
        task.add_done_callback(lambda _t: _BUILDS.pop(key, None))
    return task


async def _build_and_keep(factory: Any, repo_id: str, change_id: str, root: str) -> ChangeEvidence:
    """Build outside any write, then keep the result in a short transaction of its own."""
    async with get_session(factory) as session:
        row = await crud.get_change(session, repo_id, change_id)
        evidence = await build_stored_evidence(session, repo_id, root, row)
    async with get_session(factory) as session:
        await save_evidence(session, repo_id, change_id, evidence)
    return evidence
