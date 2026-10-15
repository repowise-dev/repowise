"""/api/repos/{repo_id}/changes — merged changes reconstructed from git, and
``/suspects``, the changes in a window ranked against a symptom."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.attribution import attribution_enabled
from repowise.core.changes.collect import (
    build_stored_evidence,
    cached_evidence,
    evidence_summary,
    keep_outcomes,
    list_needing_attention,
    outcomes_item,
    outcomes_key,
    outcomes_stale,
    save_evidence,
    with_item,
)
from repowise.core.changes.evidence import ChangeEvidence, item_line
from repowise.core.changes.record import change_record
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.suspects import Symptom
from repowise.core.suspects.index import find_suspects
from repowise.server.deps import get_db_session, resolve_request_session_factory, verify_api_key
from repowise.server.schemas.changes import (
    ChangeEvidenceSummary,
    ChangeListResponse,
    ChangeRecord,
    ChecklistsBuilt,
    SuspectsRequest,
    SuspectsResponse,
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
#: Keyed by ``(repo, change, "full" | "outcomes")``: a refresh asked while
#: only outcomes are being read starts its own full build.
_BUILDS: dict[tuple[str, str, str], asyncio.Task[ChangeEvidence]] = {}


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
    evidence: bool = Query(
        False, description="Add each row's kept checklist status counts; never builds one"
    ),
    needs_attention: bool = Query(
        False, description="Only changes whose kept checklist has an attention item"
    ),
    limit: int = Query(50, ge=1, le=_MAX_PAGE),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> ChangeListResponse:
    """A page of changes, newest merged first."""
    attribution = await _attribution(session, repo_id)
    where = crud.ChangeFilter(category=category, shape=shape, q=q)
    if needs_attention:
        rows, total = await list_needing_attention(
            session, repo_id, where, limit=limit, offset=offset
        )
    else:
        rows, total = await crud.list_changes(session, repo_id, where, limit=limit, offset=offset)
    items = []
    for r in rows:
        item = summary_from_record(change_record(r, attribution=attribution))
        if evidence:
            summary = evidence_summary(r)
            item.evidence_summary = summary and ChangeEvidenceSummary(**summary)
        items.append(item)
    checklists = None
    if evidence:
        built, changes = await crud.count_checklists_built(session, repo_id, where)
        checklists = ChecklistsBuilt(built=built, changes=changes)
    return ChangeListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        attribution=attribution,
        checklists_built=checklists,
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
        repo = await crud.get_repository(session, repo_id)
        # A kept checklist is current but for ``outcomes``, which follows HEAD.
        task = None
        factory = resolve_request_session_factory(request)
        if kept is None:
            task = _build(factory, repo_id, row.change_id, repo.local_path)
        elif await outcomes_stale(repo.local_path, kept):
            task = _build(factory, repo_id, row.change_id, repo.local_path, kept)
        if task is not None:
            try:
                kept = await asyncio.wait_for(asyncio.shield(task), EVIDENCE_WAIT_SECONDS)
            except TimeoutError:
                record["evidence_building"] = True
        if kept is not None:
            wire = kept.to_dict()
            for item in wire["items"]:
                item["line"] = item_line(item)
            record["evidence"] = wire
    return ChangeRecord(**record, commit_count=len(record["commits"]))


def _build(
    factory: Any, repo_id: str, change_id: str, root: str, kept: ChangeEvidence | None = None
) -> asyncio.Task[ChangeEvidence]:
    """The running build for this change, or a new one; a finished build leaves the map.

    Without *kept* the whole checklist is built. With it only its outcomes are
    read again, unless a full build is already running, which reads them too.
    """
    full = (repo_id, change_id, "full")
    if kept is not None and full in _BUILDS:
        return _BUILDS[full]
    key = full if kept is None else (repo_id, change_id, "outcomes")
    task = _BUILDS.get(key)
    if task is None:
        job = (
            _build_and_keep(factory, repo_id, change_id, root)
            if kept is None
            else _outcomes_and_keep(factory, repo_id, change_id, root, kept)
        )
        task = asyncio.create_task(job)
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


async def _outcomes_and_keep(
    factory: Any, repo_id: str, change_id: str, root: str, kept: ChangeEvidence
) -> ChangeEvidence:
    """Read the outcomes item, then merge only it into what is stored by then."""
    key = await outcomes_key(root)
    async with get_session(factory) as session:
        row = await crud.get_change(session, repo_id, change_id)
        item = await outcomes_item(session, repo_id, root, row, key)
    async with get_session(factory) as session:
        merged = await keep_outcomes(session, repo_id, change_id, item)
    return merged or with_item(kept, item)


def _when(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} is not an ISO 8601 time") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@router.post("/{repo_id}/suspects", response_model=SuspectsResponse)
async def rank_suspects(
    repo_id: str,
    body: SuspectsRequest,
    session: AsyncSession = Depends(get_db_session),
) -> SuspectsResponse:
    """The changes merged in a window, ranked against a symptom, with each row's evidence.

    An order to read changes in, never a cause or a probability.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    if not (body.symptom.strip() or body.files or body.trace.strip()):
        raise HTTPException(status_code=422, detail="Give a symptom, files or a trace")
    until = _when(body.until, "until")
    got = await find_suspects(
        session,
        repo_id,
        repo.local_path,
        Symptom(text=body.symptom, files=tuple(body.files), trace=body.trace, at=until),
        since=_when(body.since, "since"),
        until=until,
        limit=body.limit,
    )
    return SuspectsResponse(**got)
