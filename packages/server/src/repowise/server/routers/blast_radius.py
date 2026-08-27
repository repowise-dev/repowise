"""/api/repos/{repo_id}/blast-radius — PR blast radius analysis endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import BlastRadiusRequest, BlastRadiusResponse

router = APIRouter(
    prefix="/api/repos",
    tags=["blast-radius"],
    dependencies=[Depends(verify_api_key)],
)


@router.post("/{repo_id}/blast-radius", response_model=BlastRadiusResponse)
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
    )
    return BlastRadiusResponse(**result)
