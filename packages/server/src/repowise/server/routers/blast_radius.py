"""/api/repos/{repo_id}/blast-radius — PR blast radius analysis endpoint."""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.change_candidates import (
    UnknownChangeError,
    change_revspec,
    list_change_candidates,
    resolve_change_files,
)
from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer
from repowise.core.persistence import crud
from repowise.server.auth import read_route
from repowise.server.deps import get_db_session, verify_api_key
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
    session: AsyncSession = Depends(get_db_session),
) -> ChangeCandidatesResponse:
    """Changes to pick for a blast radius: uncommitted work, branches ahead of
    the default branch, and recent indexed commits. Without a checkout on this
    server, only the commits."""
    candidates = await list_change_candidates(session, repo_id, await _checkout(session, repo_id))
    return ChangeCandidatesResponse(**candidates.to_dict())


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
    analyzer = PRBlastRadiusAnalyzer(session=session, repo_id=repo_id)
    result = await analyzer.analyze_files(
        changed_files=analyzed, max_depth=body.max_depth, with_graph=True
    )
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
        raise HTTPException(status_code=404, detail="This server has no checkout to read the change from.")
    try:
        return await asyncio.to_thread(_health_check, root, body.change_id)
    except UnknownChangeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HealthCheckUnavailable as exc:
        raise HTTPException(status_code=422, detail=f"{exc} ({exc.code})") from exc
    except Exception as exc:  # git timeouts, unreadable blobs: say so, as the CLI does
        raise HTTPException(
            status_code=500, detail=f"The health check failed unexpectedly: {exc!r} (internal_error)"
        ) from exc
