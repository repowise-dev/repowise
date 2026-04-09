"""/api/repos/{repo_id}/blast-radius — PR blast radius analysis endpoint."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends
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
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> BlastRadiusResponse:
    """Compute blast radius for a proposed PR given its changed files.

    Returns direct risk scores per file, transitively affected files (BFS up to
    max_depth), historical co-change warnings, recommended reviewers, test gaps,
    and an overall risk score (0–10).
    """
    analyzer = PRBlastRadiusAnalyzer(session=session, repo_id=repo_id)
    result = await analyzer.analyze_files(
        changed_files=body.changed_files,
        max_depth=body.max_depth,
    )
    return BlastRadiusResponse(**result)
