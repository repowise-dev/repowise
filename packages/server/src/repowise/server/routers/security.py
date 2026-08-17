"""/api/repos/{repo_id}/security — Security findings endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import Repository, SecurityFinding
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import SecurityFindingResponse
from repowise.server.services.security_lines import check_finding_line

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
    session: AsyncSession = Depends(get_db_session),
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

    repo_root = await _repo_root(session, repo_id)
    # One read per distinct file in this page, not per finding — findings
    # cluster heavily by file. Ceiling: at limit=500 this is up to 500 reads
    # on a cold cache. If that ever bites, verify at scan time and persist the
    # outcome instead of re-deriving it per request.
    file_cache: dict[str, list[str] | None] = {}

    responses = []
    for row in rows:
        check = check_finding_line(
            _read_lines(repo_root, row.file_path, file_cache),
            row.line_number,
            row.snippet,
            row.kind,
        )
        responses.append(
            SecurityFindingResponse(
                id=row.id,
                file_path=row.file_path,
                kind=row.kind,
                severity=row.severity,
                snippet=row.snippet,
                detected_at=row.detected_at,
                line_number=check.line_number,
                line_verified=check.verified,
                commit_sha=row.commit_sha or None,
                commit_at=row.commit_at,
                found_in_history=bool(row.commit_sha),
            )
        )
    return responses


async def _repo_root(session: AsyncSession, repo_id: str) -> Path | None:
    """Live checkout root for *repo_id*, or None when it is not on disk."""
    repo = await session.get(Repository, repo_id)
    if repo is None or not repo.local_path:
        return None
    root = Path(repo.local_path).resolve()
    return root if root.is_dir() else None


def _read_lines(
    repo_root: Path | None,
    file_path: str,
    cache: dict[str, list[str] | None],
) -> list[str] | None:
    """Live file split into lines, or None when it cannot be read.

    A history finding's file may have been deleted since; that is a None, and
    the caller degrades the line rather than failing the row.

    Guarded the same way ``/file-content`` is (``routers/repos.py``), and for
    the same reason: containment alone is not enough, because ``.repowise/.env``
    and ``.git/config`` live inside the root too. History findings make this
    load-bearing rather than theoretical — their paths come from git tree
    entries, not from the indexer's own file list.
    """
    if repo_root is None:
        return None
    if file_path not in cache:
        cache[file_path] = _read_guarded(repo_root, file_path)
    return cache[file_path]


def _read_guarded(repo_root: Path, file_path: str) -> list[str] | None:
    segments = file_path.replace("\\", "/").split("/")
    if segments and segments[0] in (".git", ".repowise"):
        return None
    try:
        target = (repo_root / file_path).resolve()
        if not target.is_relative_to(repo_root):
            return None
        return target.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return None
