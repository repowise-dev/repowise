"""/api/repos/{repo_id}/knowledge-map — Knowledge map endpoint."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    KnowledgeMapOwner,
    KnowledgeMapResponse,
    KnowledgeMapSilo,
    KnowledgeMapTarget,
)
from repowise.server.services.knowledge_map import compute_knowledge_map

router = APIRouter(
    prefix="/api/repos",
    tags=["knowledge-map"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/{repo_id}/knowledge-map", response_model=KnowledgeMapResponse)
async def get_knowledge_map(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> KnowledgeMapResponse:
    """Return knowledge-map data for a repository.

    Includes top owners by file count, knowledge silos (files with >80 %
    single-owner concentration), and onboarding targets (high-centrality
    files with least documentation).
    """
    data = await compute_knowledge_map(session, repo_id)
    if not data:
        raise HTTPException(
            status_code=404,
            detail="No git metadata found for this repository. Run an index first.",
        )

    return KnowledgeMapResponse(
        top_owners=[KnowledgeMapOwner(**o) for o in data["top_owners"]],
        knowledge_silos=[KnowledgeMapSilo(**s) for s in data["knowledge_silos"]],
        onboarding_targets=[KnowledgeMapTarget(**t) for t in data["onboarding_targets"]],
    )
