"""/api/repos/{repo_id}/blast-radius — PR blast radius analysis endpoint."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.change_candidates import (
    UnknownChangeError,
    change_revspec,
    list_change_candidates,
    resolve_change_files,
)
from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.index_cache import IndexStampCache
from repowise.server.auth import read_route
from repowise.server.deps import get_db_session, resolve_request_session_factory, verify_api_key
from repowise.server.schemas import BlastRadiusRequest, BlastRadiusResponse
from repowise.server.schemas.blast_radius import (
    ChangeBlastRadiusRequest,
    ChangeBlastRadiusResponse,
    ChangeCandidatesResponse,
    ChangeHealthCheckRequest,
    ChangeHealthCheckResponse,
)

#: Changed paths analyzed per change. A long-lived branch can touch thousands;
#: past this the picture is the whole repository and the response says so.
MAX_CHANGED_FILES = 400

#: How far the Review page looks on arrival; the change listing warms this depth.
DEFAULT_DEPTH = 3

log = logging.getLogger(__name__)

#: Change analyses kept per (database, repository, index state, analyzed paths,
#: depth), as futures so a request that arrives while the same analysis runs
#: waits for it instead of running it again. The analysis reads the index and
#: the path list; the one checkout fact it adds (whether coverage is stale
#: against the working tree) can lag until the next index run. Ceiling: one
#: process; a host with several workers caches per head sha itself.
_ANALYSES = IndexStampCache(16)
#: Warm-up tasks in flight by (repository, change), held so they are not
#: collected mid-run and so a page refreshed while one runs starts no second.
_WARMING: dict[tuple[str, str, int], asyncio.Task[None]] = {}


async def _analyze_cached(
    session: AsyncSession, repo_id: str, files: list[str], depth: int
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    # Futures belong to one event loop, so the loop is part of the key.
    key = await _ANALYSES.key(session, repo_id, tuple(files), depth, id(loop))
    if key in _ANALYSES:
        shared: asyncio.Future[dict[str, Any]] = _ANALYSES.get(key)
        try:
            return await asyncio.shield(shared)
        except asyncio.CancelledError:
            # The request running it went away; this one was not cancelled, so it runs its own.
            if not shared.cancelled():
                raise
    pending: asyncio.Future[dict[str, Any]] = loop.create_future()
    _ANALYSES.remember(key, pending)
    try:
        result = await PRBlastRadiusAnalyzer(session=session, repo_id=repo_id).analyze_files(
            changed_files=files, max_depth=depth, with_graph=True
        )
    except BaseException as exc:
        # A failure is never kept: the next request tries again.
        _ANALYSES.forget(key)
        if isinstance(exc, asyncio.CancelledError):
            pending.cancel()
        else:
            pending.set_exception(exc)
            pending.exception()  # retrieved here, so an unawaited failure is not logged twice
        raise
    pending.set_result(result)
    return result


async def _warm_default(factory: Any, repo_id: str, checkout: str | None, change_id: str) -> None:
    """Analyze the change the Review page opens on, so its request finds the answer ready."""
    try:
        async with get_session(factory) as session:
            files = await resolve_change_files(session, repo_id, checkout, change_id)
            await _analyze_cached(session, repo_id, files[:MAX_CHANGED_FILES], DEFAULT_DEPTH)
    except Exception:  # best-effort warm-up: any failure here is the real request's to report
        log.debug("blast radius warm-up failed", exc_info=True)
        return


router = APIRouter(
    prefix="/api/repos",
    tags=["blast-radius"],
    dependencies=[Depends(verify_api_key)],
)


@router.post("/{repo_id}/blast-radius", response_model=BlastRadiusResponse)
@read_route
async def analyze_blast_radius(
    repo_id: str,
    body: BlastRadiusRequest,
    session: AsyncSession = Depends(get_db_session),
) -> BlastRadiusResponse:
    """Compute blast radius for a proposed PR given its changed files.

    Returns raw per-file structural scores, transitive structural reach, historical
    co-change warnings, reviewers, compatibility test gaps, a canonical typed
    test-impact population with evidence/availability state, and an uncalibrated
    0–10 structural-impact heuristic. None is a runtime-breakage probability.
    """
    analyzer = PRBlastRadiusAnalyzer(session=session, repo_id=repo_id)
    result = await analyzer.analyze_files(
        changed_files=body.changed_files,
        max_depth=body.max_depth,
        with_graph=body.with_graph,
    )
    return BlastRadiusResponse(**result)


async def _checkout(session: AsyncSession, repo_id: str) -> str | None:
    """The repository's checkout on this server, ``None`` when it has none."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo.local_path if repo.local_path and os.path.isdir(repo.local_path) else None


@router.get("/{repo_id}/blast-radius/changes", response_model=ChangeCandidatesResponse)
async def list_blast_radius_changes(
    repo_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ChangeCandidatesResponse:
    """Changes to pick for a blast radius: uncommitted work, branches ahead of
    the default branch, and recent indexed commits. Without a checkout on this
    server, only the commits."""
    checkout = await _checkout(session, repo_id)
    candidates = await list_change_candidates(session, repo_id, checkout)
    # Tasks belong to one event loop, so the loop is part of the key.
    warm_key = (repo_id, candidates.default_id or "", id(asyncio.get_running_loop()))
    if candidates.default_id and warm_key not in _WARMING:
        # The page analyzes the default change next; start it now in its own session.
        task = asyncio.create_task(
            _warm_default(
                resolve_request_session_factory(request), repo_id, checkout, candidates.default_id
            )
        )
        _WARMING[warm_key] = task
        task.add_done_callback(lambda _t, k=warm_key: _WARMING.pop(k, None))
    # The health check reads both sides of a change from git, so it needs the checkout.
    return ChangeCandidatesResponse(
        **candidates.to_dict(), health_check_supported=candidates.working_tree_supported
    )


@router.post("/{repo_id}/blast-radius/change", response_model=ChangeBlastRadiusResponse)
@read_route
async def analyze_change_blast_radius(
    repo_id: str,
    body: ChangeBlastRadiusRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ChangeBlastRadiusResponse:
    """Blast radius of one listed change, with the per-file signals and
    reach edges a drawing needs."""
    try:
        files = await resolve_change_files(
            session, repo_id, await _checkout(session, repo_id), body.change_id
        )
    except UnknownChangeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    analyzed = files[:MAX_CHANGED_FILES]
    result = await _analyze_cached(session, repo_id, analyzed, body.max_depth)
    return ChangeBlastRadiusResponse(
        **result,
        change_id=body.change_id,
        changed_files=analyzed,
        changed_files_total=len(files),
    )


def _health_check(root: str, change_id: str) -> ChangeHealthCheckResponse:
    from repowise.core.analysis.change_health.check import run_health_check

    check = run_health_check(root, change_revspec(root, change_id))
    doc = check.as_json()
    return ChangeHealthCheckResponse(
        change_id=change_id,
        revspec=doc["revspec"],
        status=doc["status"],
        explanation=doc.get("explanation"),
        scope=doc["scope"],
        skipped=doc["skipped"],
        findings=doc["findings"],
        gate=doc["gate"],
    )


@router.post("/{repo_id}/blast-radius/health-check", response_model=ChangeHealthCheckResponse)
@read_route
async def check_change_health(
    repo_id: str,
    body: ChangeHealthCheckRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ChangeHealthCheckResponse:
    """``repowise health check`` on one listed change, read from git (no index).

    On demand only: it parses both sides of every changed file, so it can take
    seconds on a large change. Needs a checkout on this server.
    """
    from repowise.core.analysis.change_health.check import HealthCheckUnavailable

    root = await _checkout(session, repo_id)
    if root is None:
        raise HTTPException(
            status_code=404, detail="This server has no checkout to read the change from."
        )
    try:
        return await asyncio.to_thread(_health_check, root, body.change_id)
    except UnknownChangeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HealthCheckUnavailable as exc:
        raise HTTPException(status_code=422, detail=f"{exc} ({exc.code})") from exc
    except Exception as exc:  # git timeouts, unreadable blobs: say so, as the CLI does
        raise HTTPException(
            status_code=500,
            detail=f"The health check failed unexpectedly: {exc!r} (internal_error)",
        ) from exc
