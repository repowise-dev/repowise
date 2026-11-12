"""/api/repos/{repo_id}/blast-radius — PR blast radius analysis endpoint."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.change_candidates import (
    UnknownChangeError,
    change_identity,
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
    ChangeReview,
)

#: Changed paths analyzed per change. A long-lived branch can touch thousands;
#: past this the picture is the whole repository and the response says so.
MAX_CHANGED_FILES = 400

#: How far the Review page looks on arrival; the change listing warms this depth.
DEFAULT_DEPTH = 3

#: Co-change pairs, recommended tests and per-file test rows a change reply
#: lists, strongest first; the rest are counted. A long branch holds hundreds
#: to thousands of each.
_LIST_ROWS = 100
#: Changed files named per recommended test, with the evidence for each.
_PER_TEST = 10

log = logging.getLogger(__name__)

#: Change analyses kept per (database, repository, index state, analyzed paths,
#: depth), as futures so a request that arrives while the same analysis runs
#: waits for it instead of running it again. The analysis reads the index and
#: the path list; the one checkout fact it adds (whether coverage is stale
#: against the working tree) can lag until the next index run. Ceiling: one
#: process; a host with several workers caches per head sha itself.
_ANALYSES = IndexStampCache(16)
#: Change reviews (score and tests) kept per (database, repository, index
#: state, change), the same way. A commit is keyed by its sha and a branch by
#: its tip and base commits, so a moved branch is reviewed again; uncommitted
#: work moves under any key and is never kept.
_REVIEWS = IndexStampCache(16)
#: Warm-up tasks in flight by (repository, change), held so they are not
#: collected mid-run and so a page refreshed while one runs starts no second.
_WARMING: dict[tuple[str, str, int], asyncio.Task[None]] = {}

_T = TypeVar("_T")


async def _shared(
    cache: IndexStampCache,
    key: tuple[Any, ...],
    compute: Callable[[], Awaitable[_T]],
    keep: Callable[[_T], bool] = lambda _answer: True,
) -> _T:
    """*compute*'s answer for *key*, run once while it is kept in *cache*.

    A request that arrives while the same key runs waits for that run. A
    failure, or an answer *keep* refuses, is never kept: the next request
    tries again.
    """
    if key in cache:
        shared: asyncio.Future[_T] = cache.get(key)
        try:
            return await asyncio.shield(shared)
        except asyncio.CancelledError:
            # The request running it went away; this one was not cancelled, so it runs its own.
            if not shared.cancelled():
                raise
    pending: asyncio.Future[_T] = asyncio.get_running_loop().create_future()
    cache.remember(key, pending)
    try:
        result = await compute()
    except BaseException as exc:
        cache.forget(key)
        if isinstance(exc, asyncio.CancelledError):
            pending.cancel()
        else:
            pending.set_exception(exc)
            pending.exception()  # retrieved here, so an unawaited failure is not logged twice
        raise
    if not keep(result):
        cache.forget(key)
    pending.set_result(result)
    return result


async def _analyze_cached(
    session: AsyncSession, repo_id: str, files: list[str], depth: int
) -> dict[str, Any]:
    # Futures belong to one event loop, so the loop is part of the key.
    key = await _ANALYSES.key(
        session, repo_id, tuple(files), depth, id(asyncio.get_running_loop())
    )
    return await _shared(
        _ANALYSES,
        key,
        lambda: PRBlastRadiusAnalyzer(session=session, repo_id=repo_id).analyze_files(
            changed_files=files, max_depth=depth, with_graph=True
        ),
    )


async def _review_key(
    session: AsyncSession, repo_id: str, checkout: str | None, change_id: str
) -> tuple[Any, ...] | None:
    """The key a change's review is kept under; ``None`` when it is never kept."""
    if checkout is None:
        return None
    try:
        identity = await asyncio.to_thread(change_identity, checkout, change_id)
    except UnknownChangeError:
        return None
    if identity is None:
        return None
    return await _REVIEWS.key(session, repo_id, identity, id(asyncio.get_running_loop()))


def _keep_review(answer: tuple[ChangeReview | None, str | None]) -> bool:
    """A review is kept unless it failed or its tests waited on another change's selection.

    A selection that ran past its budget is kept: the same change on the same
    index runs past it again, and a reader should not wait the budget out on
    every visit.
    """
    review, _ = answer
    return review is not None and review.tests.status != "busy"


async def _review_cached(
    key: tuple[Any, ...] | None, factory: Any, repo_id: str, checkout: str | None, change_id: str
) -> tuple[ChangeReview | None, str | None]:
    """:func:`_review`, kept per index state and change commits when *key* is set."""
    if key is None:
        return await _review(factory, repo_id, checkout, change_id)
    return await _shared(
        _REVIEWS, key, lambda: _review(factory, repo_id, checkout, change_id), _keep_review
    )


async def _warm_default(factory: Any, repo_id: str, checkout: str | None, change_id: str) -> None:
    """Analyze and review the change the Review page opens on, so its request finds both ready."""
    try:
        async with get_session(factory) as session:
            files = await resolve_change_files(session, repo_id, checkout, change_id)
            key = await _review_key(session, repo_id, checkout, change_id)
            reviewing = asyncio.ensure_future(
                _review_cached(key, factory, repo_id, checkout, change_id)
            )
            try:
                await _analyze_cached(session, repo_id, files[:MAX_CHANGED_FILES], DEFAULT_DEPTH)
            finally:
                await reviewing  # never raises: a failed review is an answer
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
        return ChangeReview(revspec=revspec, risk=change_risk_payload(scored), tests=tests), None
    except Exception as exc:  # a review bug must not take the blast radius with it
        log.warning("change review failed", exc_info=True)
        return None, f"The change review failed unexpectedly: {exc!r}"


@router.post("/{repo_id}/blast-radius/change", response_model=ChangeBlastRadiusResponse)
@read_route
async def analyze_change_blast_radius(
    repo_id: str,
    body: ChangeBlastRadiusRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ChangeBlastRadiusResponse:
    """Blast radius of one listed change: one page of the files it reaches
    (``via`` on each with ``with_graph``), the per-file signals for the changed
    files and that page, and on the first page its score and tests (``review``)."""
    checkout = await _checkout(session, repo_id)
    try:
        files = await resolve_change_files(session, repo_id, checkout, body.change_id)
    except UnknownChangeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    analyzed = files[:MAX_CHANGED_FILES]
    review: ChangeReview | None = None
    unavailable: str | None = None
    reviewing: asyncio.Future[tuple[ChangeReview | None, str | None]] | None = None
    if body.offset == 0:
        key = await _review_key(session, repo_id, checkout, body.change_id)
        # The review reads git and opens its own sessions, so it runs beside the analysis.
        reviewing = asyncio.ensure_future(
            _review_cached(
                key, resolve_request_session_factory(request), repo_id, checkout, body.change_id
            )
        )
    try:
        result = await _analyze_cached(session, repo_id, analyzed, body.max_depth)
    except BaseException:
        if reviewing is not None:
            reviewing.cancel()
        raise
    if reviewing is not None:
        review, unavailable = await reviewing
    return ChangeBlastRadiusResponse(
        **_page(result, analyzed, body),
        change_id=body.change_id,
        changed_files=analyzed,
        changed_files_total=len(files),
        review=review,
        review_unavailable=unavailable,
    )


def _module_of(path: str, module: str | None) -> str:
    """The index's module for a file, else its folder two levels down (as the Review page groups)."""
    if module:
        return module
    parts = path.split("/")
    return "/".join(parts[: min(2, len(parts) - 1)]) if len(parts) > 1 else "(root)"


def _reach_summary(rows: list[dict[str, Any]], signals: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Counts over every reached file, tests apart, as the Review page counts them."""
    source = [(r, signals.get(r["path"]) or {}) for r in rows]
    source = [(r, s) for r, s in source if not s.get("is_test")]
    return {
        "files": len(source),
        "tests": len(rows) - len(source),
        "direct": sum(1 for r, _ in source if r["depth"] == 1),
        "modules": len({_module_of(r["path"], s.get("module")) for r, s in source}),
        "max_depth": max((r["depth"] for r, _ in source), default=0),
        "imported": sorted({r["via"] for r, _ in source if r["depth"] == 1 and r.get("via")}),
    }


def _cut_test(rec: dict[str, Any]) -> dict[str, Any]:
    """One recommended test with its evidence cut to the first few changed files it reaches."""
    return rec | {
        "source_files": rec["source_files"][:_PER_TEST],
        "source_files_total": len(rec["source_files"]),
        "evidence": rec["evidence"][:_PER_TEST],
    }


def _cut_lists(result: dict[str, Any]) -> dict[str, Any]:
    """The co-change pairs and the test impact cut to what a page reads, with what was cut counted.

    Each of these grows with the changed files, up to hundreds of rows on a
    long branch; the totals (``files_total``, the coverage counts,
    ``recommendations_total``) stay whole.
    """
    cochange = result["cochange_warnings"]
    tests = result["test_impact"]
    kept = tests["recommendations"][:_LIST_ROWS]
    return {
        # Strongest first, so the cut keeps the pairs most worth a look.
        "cochange_warnings": cochange[:_LIST_ROWS],
        "missing_partners_total": len({w["missing_partner"] for w in cochange}),
        "test_impact": tests
        | {
            "recommendations": [_cut_test(rec) for rec in kept],
            "recommendations_emitted": len(kept),
            "recommendations_truncated": len(kept) < len(tests["recommendations"]),
            "recommendations_omitted": len(tests["recommendations"]) - len(kept),
            "files": tests["files"][:_LIST_ROWS],
            "files_without_measured_tests": tests["files_without_measured_tests"][:_LIST_ROWS],
            "unknown_files": tests["unknown_files"][:_LIST_ROWS],
        },
    }


def _page(result: dict[str, Any], changed: list[str], body: ChangeBlastRadiusRequest) -> dict[str, Any]:
    """One page of the reached files, with counts over all of them.

    The analysis is kept whole (with ``via`` and a signal per file); a reply
    carries ``limit`` reached files from ``offset``, ``via`` only when asked
    for, and the signals and test gaps of the changed files and that page.
    """
    rows = result["transitive_affected"]
    window = rows[body.offset : body.offset + body.limit]
    shown = set(changed).union(r["path"] for r in window)
    signals = {s["path"]: s for s in result.get("affected_signals") or []}
    end = body.offset + len(window)
    return {
        **result,
        **_cut_lists(result),
        "transitive_affected": window if body.with_graph else [r | {"via": None} for r in window],
        "affected_signals": [s for p, s in signals.items() if p in shown],
        "test_gaps": [p for p in result["test_gaps"] if p in shown],
        "transitive_total": len(rows),
        "next_offset": end if end < len(rows) else None,
        "reach_summary": _reach_summary(rows, signals),
    }


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
