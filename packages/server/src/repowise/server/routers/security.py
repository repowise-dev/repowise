"""/api/repos/{repo_id}/security — Security findings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import SecurityFinding
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import SecurityFindingResponse

router = APIRouter(
    prefix="/api/repos",
    tags=["security"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/{repo_id}/security", response_model=list[SecurityFindingResponse])
async def list_security_findings(
    repo_id: str,
    file_path: str | None = Query(None, description="Filter by relative file path"),
    severity: str | None = Query(None, description="Filter by severity: high, med, or low"),
    history: bool | None = Query(
        None,
        description="If true, only full-history findings; if false, only working-tree findings; omit for both.",
    ),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[SecurityFindingResponse]:
    """List security findings for a repository, with optional filters."""
    stmt = select(SecurityFinding).where(SecurityFinding.repository_id == repo_id)

    if file_path is not None:
        stmt = stmt.where(SecurityFinding.file_path == file_path)

    if severity is not None:
        stmt = stmt.where(SecurityFinding.severity == severity)

    if history is not None:
        # Working-tree rows store "" for commit_sha; history rows store a SHA.
        if history:
            stmt = stmt.where(SecurityFinding.commit_sha != "")
        else:
            stmt = stmt.where(SecurityFinding.commit_sha == "")

    stmt = stmt.order_by(SecurityFinding.detected_at.desc()).limit(limit)

    result = await session.execute(stmt)
    rows = result.scalars().all()

    return [
        SecurityFindingResponse(
            id=row.id,
            file_path=row.file_path,
            kind=row.kind,
            severity=row.severity,
            snippet=row.snippet,
            detected_at=row.detected_at,
            commit_sha=row.commit_sha or None,
            found_in_history=bool(row.commit_sha),
        )
        for row in rows
    ]
