"""/api/repos/{repo_id}/claude-md — CLAUDE.md generation endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.generation.editor_files import ClaudeMdGenerator, EditorFileDataFetcher
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key

router = APIRouter(
    tags=["claude-md"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/api/repos/{repo_id}/claude-md")
async def get_claude_md(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Return the Repowise-managed CLAUDE.md section as JSON.

    Does not write to disk — useful for previewing in the web UI.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    repo_path = Path(repo.local_path) if repo.local_path else Path(".")
    fetcher = EditorFileDataFetcher(session, repo_id, repo_path)
    data = await fetcher.fetch()

    gen = ClaudeMdGenerator()
    section_content = gen.render(data)

    return {
        "content": section_content,
        "generated_at": data.indexed_at,
        "repo_name": data.repo_name,
        "sections": _detect_sections(section_content),
    }


@router.post("/api/repos/{repo_id}/claude-md/generate", status_code=202)
async def generate_claude_md(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Regenerate CLAUDE.md and write it to the repository root.

    Returns the generated content. Runs synchronously (fast — no LLM calls).
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    repo_path = Path(repo.local_path)
    if not repo_path.exists():
        raise HTTPException(
            status_code=422,
            detail=f"Repository path not accessible: {repo_path}",
        )

    fetcher = EditorFileDataFetcher(session, repo_id, repo_path)
    data = await fetcher.fetch()

    gen = ClaudeMdGenerator()
    written = gen.write(repo_path, data)

    return {
        "status": "generated",
        "path": str(written),
        "generated_at": data.indexed_at,
    }


def _detect_sections(content: str) -> list[str]:
    """Return the markdown H3 section names present in the content."""
    import re

    return re.findall(r"^### (.+)$", content, re.MULTILINE)
