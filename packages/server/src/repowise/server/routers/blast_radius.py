"""/api/repos/{repo_id}/blast-radius — PR blast radius analysis endpoint."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.change_candidates import (
    UnknownChangeError,
    change_revspec,
    list_change_candidates,
    resolve_change_files,
)
from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer, run_commands
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.index_cache import IndexStampCache
from repowise.server.auth import read_route
from repowise.server.deps import get_db_session, resolve_request_session_factory, verify_api_key
from repowise.server.review_markdown import render_change_markdown
from repowise.server.schemas import BlastRadiusRequest, BlastRadiusResponse
from repowise.server.schemas.blast_radius import (
    ChangeBlastRadiusRequest,
    ChangeBlastRadiusResponse,
    ChangeCandidatesResponse,
    ChangeHealthCheckRequest,
    ChangeHealthCheckResponse,
    ChangeReview,
)

if TYPE_CHECKING:
    from repowise.core.analysis.change_health.check import HealthCheck

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


async def _review(
    factory: Any, repo_id: str, checkout: str | None, change_id: str
) -> tuple[ChangeReview | None, str | None]:
    """``(review, None)`` for the change, or ``(None, why it has none)``.

    The score is ``score_live_change`` and the tests ``scored_change_tests``,
    the calls behind ``repowise risk`` and ``get_change_risk``, on the revspec
    the change names.
    """
    from repowise.core.analysis.change_risk import change_risk_payload, score_live_change
    from repowise.server.mcp_server._test_selection import scored_change_tests

    if checkout is None:
        return None, "This server has no checkout to read the change from."
    try:
        revspec = await asyncio.to_thread(change_revspec, checkout, change_id)
        scored = await asyncio.to_thread(score_live_change, checkout, revspec)
    except Exception as exc:  # git could not read the change; the blast radius still stands
        log.debug("change score failed", exc_info=True)
        return None, f"The change could not be scored: {exc}"
    try:
        tests = await scored_change_tests(checkout, factory, revspec, scored, repo=repo_id)
        commands = await _test_commands(factory, repo_id, tests)
        review = ChangeReview(
            revspec=revspec, risk=change_risk_payload(scored), tests=tests, test_commands=commands
        )
        return review, None
    except Exception as exc:  # a review bug must not take the blast radius with it
        log.warning("change review failed", exc_info=True)
        return None, f"The change review failed unexpectedly: {exc!r}"


async def _change_analysis(
    session: AsyncSession, request: Request, repo_id: str, change_id: str, max_depth: int
) -> ChangeBlastRadiusResponse:
    checkout = await _checkout(session, repo_id)
    try:
        files = await resolve_change_files(session, repo_id, checkout, change_id)
    except UnknownChangeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    analyzed = files[:MAX_CHANGED_FILES]
    # The review reads git and opens its own sessions, so it runs beside the analysis.
    reviewing = asyncio.ensure_future(
        _review(resolve_request_session_factory(request), repo_id, checkout, change_id)
    )
    try:
        result = await _analyze_cached(session, repo_id, analyzed, max_depth)
    except BaseException:
        reviewing.cancel()
        raise
    review, unavailable = await reviewing
    return ChangeBlastRadiusResponse(
        **result,
        change_id=change_id,
        changed_files=analyzed,
        changed_files_total=len(files),
        review=review,
        review_unavailable=unavailable,
    )
async def _test_commands(factory: Any, repo_id: str, tests: dict[str, Any]) -> list[str]:
    """Commands that run the selected tests; none for a run-all verdict, or when
    the runners cannot be read (the score and the list stand without them)."""
    if tests["run_all"] or not tests["tests_to_run"]:
        return []
    try:
        async with get_session(factory) as session:
            return await run_commands(session, repo_id, tests["tests_to_run"], tests["total"])
    except Exception:
        log.warning("test commands failed", exc_info=True)
        return []


@router.post("/{repo_id}/blast-radius/change", response_model=ChangeBlastRadiusResponse)
@read_route
async def analyze_change_blast_radius(
    repo_id: str,
    body: ChangeBlastRadiusRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ChangeBlastRadiusResponse:
    """Blast radius of one listed change, with the per-file signals and
    reach edges a drawing needs, and its score and tests (``review``)."""
    return await _change_analysis(session, request, repo_id, body.change_id, body.max_depth)


@router.get("/{repo_id}/blast-radius/change.md", response_class=PlainTextResponse)
async def change_review_markdown(
    repo_id: str,
    change_id: str = Query(min_length=1, max_length=300),
    max_depth: int = Query(default=DEFAULT_DEPTH, ge=1, le=10),
    health_check: bool = False,
    *,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> PlainTextResponse:
    """The Review page for one listed change as Markdown, for a coding agent.

    ``health_check=true`` also runs the health check (seconds on a large
    change) and adds its verdict and findings; without a checkout the section
    says why it could not run.
    """
    from repowise.core.analysis.change_health.check import HealthCheckUnavailable

    response = await _change_analysis(session, request, repo_id, change_id, max_depth)
    health = None
    if health_check:
        root = await _checkout(session, repo_id)
        if root is None:
            health = "this server has no checkout to read the change from"
        else:
            try:
                health = await asyncio.to_thread(_run_check, root, change_id)
            except (UnknownChangeError, HealthCheckUnavailable) as exc:
                health = str(exc)
            except Exception as exc:  # git timeouts, unreadable blobs: the review still renders
                health = f"it failed unexpectedly: {exc!r}"
    return PlainTextResponse(
        render_change_markdown(response, health), media_type="text/markdown; charset=utf-8"
    )


def _run_check(root: str, change_id: str) -> HealthCheck:
    from repowise.core.analysis.change_health.check import run_health_check

    return run_health_check(root, change_revspec(root, change_id))


def _health_check(root: str, change_id: str) -> ChangeHealthCheckResponse:
    doc = _run_check(root, change_id).as_json()
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
