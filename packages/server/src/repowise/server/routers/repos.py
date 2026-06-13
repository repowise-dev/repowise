"""/api/repos — Repository CRUD + sync endpoints."""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GenerationJob,
    GraphNode,
    Page,
    Repository,
)
from repowise.server.deps import get_db_session, get_fts, verify_api_key
from repowise.server.job_executor import execute_job
from repowise.server.schemas import RepoCreate, RepoResponse, RepoStatsResponse, RepoUpdate

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/repos",
    tags=["repos"],
    dependencies=[Depends(verify_api_key)],
)


@router.post("", response_model=RepoResponse, status_code=201)
async def create_repo(
    body: RepoCreate,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> RepoResponse:
    """Register a new repository (or update if same local_path exists)."""
    repo = await crud.upsert_repository(
        session,
        name=body.name,
        local_path=body.local_path,
        url=body.url,
        default_branch=body.default_branch,
        settings=body.settings,
    )
    return RepoResponse.from_orm(repo)


@router.get("", response_model=list[RepoResponse])
async def list_repos(
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[RepoResponse]:
    """List all registered repositories.

    In workspace mode, aggregates indexed repos from the primary DB and
    every workspace repo DB, AND includes synthetic entries for
    workspace repos that haven't been indexed yet (status="needs_index")
    or whose directory has gone missing (status="missing_dir"). This is
    what powers the web UI sidebar — silently dropping unindexed repos
    used to cause the "I only see the primary" Discord report.
    """
    result = await session.execute(select(Repository).order_by(Repository.updated_at.desc()))
    repos = list(result.scalars().all())
    seen_ids = {r.id for r in repos}

    # In workspace mode, also fetch repos from other workspace DBs
    ws_sessions: dict = getattr(request.app.state, "workspace_sessions", {})
    for repo_id, ws_factory in ws_sessions.items():
        if repo_id in seen_ids:
            continue
        try:
            async with ws_factory() as ws_session:
                ws_result = await ws_session.execute(
                    select(Repository).where(Repository.id == repo_id)
                )
                ws_repo = ws_result.scalar_one_or_none()
                if ws_repo:
                    repos.append(ws_repo)
                    seen_ids.add(ws_repo.id)
        except Exception:
            pass

    # Sort indexed repos by updated_at descending
    repos.sort(key=lambda r: r.updated_at or r.created_at, reverse=True)
    responses = [RepoResponse.from_orm(r) for r in repos]

    # Augment with workspace metadata. We do this in a second pass (rather
    # than during from_orm) because the workspace context lives on
    # app.state, not on the Repository row.
    ws_config = getattr(request.app.state, "workspace_config", None)
    ws_root = getattr(request.app.state, "workspace_root", None)
    if ws_config is None or ws_root is None:
        return responses

    import json as _json
    from pathlib import Path as _P

    ws_root_path = _P(ws_root)
    # Map local_path → alias entry for quick attach on indexed rows.
    by_path: dict[str, object] = {
        str((ws_root_path / e.path).resolve()): e for e in ws_config.repos
    }

    # Attach alias + status + docs status to already-indexed rows.
    indexed_aliases: set[str] = set()
    for resp in responses:
        entry = by_path.get(str(_P(resp.local_path).resolve()))
        if entry is None:
            continue
        resp.workspace_alias = entry.alias
        resp.is_primary = bool(entry.is_primary)
        resp.workspace_status = "indexed"
        indexed_aliases.add(entry.alias)

        # docs_enabled is recorded per-repo in state.json. Read it once
        # per response — cheap, and never failing.
        state_path = _P(resp.local_path) / ".repowise" / "state.json"
        if state_path.is_file():
            try:
                state = _json.loads(state_path.read_text(encoding="utf-8"))
                resp.docs_enabled = bool(state.get("docs_enabled", True))
                resp.docs_skip_reason = state.get("docs_skip_reason")
            except Exception:
                pass

    # Synthesize entries for repos in the workspace that aren't indexed yet.
    from datetime import UTC as _UTC
    from datetime import datetime

    now = datetime.now(_UTC)
    for entry in ws_config.repos:
        if entry.alias in indexed_aliases:
            continue
        abs_path = (ws_root_path / entry.path).resolve()
        if abs_path.is_dir():
            status = "needs_index"
        else:
            status = "missing_dir"
        # Synthetic, stable, prefixed ID so the frontend can route to a
        # CTA card without colliding with real repo UUIDs.
        synthetic_id = f"ws:{entry.alias}"
        responses.append(
            RepoResponse(
                id=synthetic_id,
                name=entry.alias,
                url="",
                local_path=str(abs_path),
                default_branch="main",
                head_commit=None,
                settings={},
                created_at=now,
                updated_at=now,
                workspace_alias=entry.alias,
                workspace_status=status,
                is_primary=bool(entry.is_primary),
                docs_enabled=False,
                docs_skip_reason="not indexed yet",
            )
        )

    return responses


@router.get("/{repo_id}", response_model=RepoResponse)
async def get_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> RepoResponse:
    """Get a single repository by ID."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return RepoResponse.from_orm(repo)


@router.patch("/{repo_id}", response_model=RepoResponse)
async def update_repo(
    repo_id: str,
    body: RepoUpdate,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> RepoResponse:
    """Update repository fields."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    if body.name is not None:
        repo.name = body.name
    if body.url is not None:
        repo.url = body.url
    if body.default_branch is not None:
        repo.default_branch = body.default_branch
    if body.settings is not None:
        import json

        from repowise.core.generation.styles import is_known_style, list_styles

        # Validate a wiki_style setting up front so a typo surfaces as a 400 here
        # rather than silently falling back to the default during generation.
        style = body.settings.get("wiki_style")
        if style is not None and not is_known_style(style):
            valid = ", ".join(s.name for s in list_styles())
            raise HTTPException(
                status_code=400,
                detail=f"Unknown wiki_style '{style}'. Valid styles: {valid}.",
            )
        repo.settings_json = json.dumps(body.settings)
    await session.flush()
    return RepoResponse.from_orm(repo)


@router.delete("/{repo_id}")
async def delete_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    fts=Depends(get_fts),  # noqa: B008
) -> dict:
    """Delete a repository and all its data."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Collect page IDs before CASCADE deletes the Page rows
    page_ids = await crud.list_page_ids(session, repo_id)

    # Clean up FTS index (FTS5 virtual table has no FK cascade).
    # fts is always initialized in the lifespan before the server accepts
    # requests, so this guard is purely defensive.
    if fts is not None:
        await fts.delete_many(page_ids)

    # Delete repository — CASCADE handles all child ORM tables
    await crud.delete_repository(session, repo_id)

    return {"ok": True, "deleted_pages": len(page_ids)}


@router.get("/{repo_id}/stats", response_model=RepoStatsResponse)
async def get_repo_stats(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> RepoStatsResponse:
    """Get aggregate stats for a repository."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    file_count_result = await session.execute(
        select(func.count(GraphNode.id)).where(GraphNode.repository_id == repo_id)
    )
    file_count = file_count_result.scalar_one() or 0

    symbol_count_result = await session.execute(
        select(func.sum(GraphNode.symbol_count)).where(GraphNode.repository_id == repo_id)
    )
    symbol_count = int(symbol_count_result.scalar_one() or 0)

    entry_count_result = await session.execute(
        select(func.count(GraphNode.id)).where(
            GraphNode.repository_id == repo_id,
            GraphNode.is_entry_point == True,  # noqa: E712
        )
    )
    entry_point_count = entry_count_result.scalar_one() or 0

    avg_conf_result = await session.execute(
        select(func.avg(Page.confidence)).where(Page.repository_id == repo_id)
    )
    avg_confidence = float(avg_conf_result.scalar_one() or 0.0)
    doc_coverage_pct = avg_confidence * 100

    dead_result = await session.execute(
        select(func.count(DeadCodeFinding.id)).where(
            DeadCodeFinding.repository_id == repo_id,
            DeadCodeFinding.kind == "unused_export",
            DeadCodeFinding.status == "open",
        )
    )
    dead_export_count = dead_result.scalar_one() or 0

    # Compute true freshness score from actual page freshness statuses
    total_pages_result = await session.execute(
        select(func.count(Page.id)).where(Page.repository_id == repo_id)
    )
    total_pages = total_pages_result.scalar_one() or 0

    fresh_pages_result = await session.execute(
        select(func.count(Page.id)).where(
            Page.repository_id == repo_id,
            Page.freshness_status == "fresh",
        )
    )
    fresh_pages = fresh_pages_result.scalar_one() or 0

    freshness_score = (fresh_pages / total_pages * 100) if total_pages > 0 else doc_coverage_pct

    return RepoStatsResponse(
        file_count=file_count,
        symbol_count=symbol_count,
        entry_point_count=entry_point_count,
        doc_coverage_pct=doc_coverage_pct,
        freshness_score=freshness_score,
        dead_export_count=dead_export_count,
    )


@router.post("/{repo_id}/sync", status_code=202)
async def sync_repo(
    repo_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Trigger an incremental documentation sync for a repository.

    Creates a generation job, launches the pipeline in the background,
    and returns immediately with the job ID.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Prevent concurrent pipeline runs on the same repo
    active = await session.execute(
        select(GenerationJob.id)
        .where(GenerationJob.repository_id == repo_id)
        .where(GenerationJob.status.in_(["pending", "running"]))
        .limit(1)
    )
    if active.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409, detail="A sync job is already in progress for this repository"
        )

    job = await crud.upsert_generation_job(
        session,
        repository_id=repo_id,
        status="pending",
    )
    # Commit (not just flush) so the background task's separate session can
    # see the job row.  SQLite WAL isolation hides uncommitted rows from
    # other connections, so flush() alone is not sufficient.
    await session.commit()
    _launch_job_task(request, job.id, repo_id)
    return {"job_id": job.id, "status": "accepted"}


@router.post("/{repo_id}/full-resync", status_code=202)
async def full_resync(
    repo_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Trigger a full re-generation of all documentation.

    Creates a generation job, launches the pipeline in the background,
    and returns immediately with the job ID.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Prevent concurrent pipeline runs on the same repo
    active = await session.execute(
        select(GenerationJob.id)
        .where(GenerationJob.repository_id == repo_id)
        .where(GenerationJob.status.in_(["pending", "running"]))
        .limit(1)
    )
    if active.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409, detail="A sync job is already in progress for this repository"
        )

    job = await crud.upsert_generation_job(
        session,
        repository_id=repo_id,
        status="pending",
        config={"mode": "full_resync"},
    )
    # Commit (not just flush) so the background task's separate session can
    # see the job row.  See sync_repo comment for rationale.
    await session.commit()
    _launch_job_task(request, job.id, repo_id)
    return {"job_id": job.id, "status": "accepted"}


def _resolve_repo_session_factory(app_state, repo_id: str):
    """Backward-compatible alias for :func:`deps.resolve_session_factory`.

    Kept to avoid churn at call sites; new code should call
    ``resolve_session_factory`` (or its request-scoped sibling
    ``resolve_request_session_factory``) directly.
    """
    from repowise.server.deps import resolve_session_factory

    return resolve_session_factory(app_state, repo_id)


def _launch_job_task(request: Request, job_id: str, repo_id: str) -> None:
    """Launch a background job task with proper lifecycle management.

    Stores a strong reference in ``app.state.background_tasks`` to prevent
    garbage collection, and removes it when the task finishes.  Exceptions
    are logged instead of silently swallowed.

    If task creation itself fails (or the task ends with an unhandled
    exception that ``execute_job`` couldn't record), we mark the job as
    failed via a fallback path so the active-job guard never gets stuck.

    ``repo_id`` is required so we can resolve the per-repo session factory
    in workspace mode — that's the same DB the route handler just wrote
    the job to.
    """
    app_state = request.app.state
    session_factory = _resolve_repo_session_factory(app_state, repo_id)

    async def _mark_failed(reason: str) -> None:
        try:
            from repowise.core.persistence.crud import update_job_status

            async with get_session(session_factory) as session:
                await update_job_status(
                    session,
                    job_id,
                    "failed",
                    error_message=reason[:500],
                )
        except Exception:
            logger.exception("fallback_job_failure_record_failed", extra={"job_id": job_id})

    try:
        task = asyncio.create_task(
            execute_job(job_id, app_state, session_factory_override=session_factory),
            name=f"job-{job_id}",
        )
    except Exception as exc:
        logger.exception("create_task_failed", extra={"job_id": job_id})
        # Schedule the failure-marking on the running loop; we're already in
        # an async request handler so a fresh task is fine.
        asyncio.create_task(_mark_failed(f"Failed to launch background task: {exc}"))
        return

    bg_tasks: set[asyncio.Task] = app_state.background_tasks  # type: ignore[assignment]
    bg_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        bg_tasks.discard(t)
        if t.cancelled():
            asyncio.create_task(_mark_failed("Job task was cancelled"))
            return
        exc = t.exception()
        if exc is not None:
            logger.error("background_job_failed", exc_info=exc)
            # execute_job already tries to mark failed in its except block,
            # but if that itself raised we must still ensure the row is
            # not left in pending/running.
            asyncio.create_task(_mark_failed(f"Background task crashed: {exc}"))

    task.add_done_callback(_on_done)


@router.get("/{repo_id}/export")
async def export_wiki(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> StreamingResponse:
    """Export all wiki pages as a ZIP of markdown files with folder structure."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    pages = (
        (await session.execute(select(Page).where(Page.repository_id == repo_id))).scalars().all()
    )
    if not pages:
        raise HTTPException(status_code=404, detail="No pages to export")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for page in pages:
            target = page.target_path or page.id
            safe = target.replace("::", "/").replace("->", "--").replace("\\", "/")
            path = PurePosixPath("wiki") / page.page_type / safe
            if path.suffix != ".md":
                path = path.with_suffix(path.suffix + ".md")

            content = f"# {page.title}\n\n{page.content}"
            zf.writestr(str(path), content)

    buf.seek(0)
    filename = f"{repo.name}-wiki.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{repo_id}/file-content")
async def get_file_content(
    repo_id: str,
    file_path: str = Query(...),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> PlainTextResponse:
    """Return raw file content from the repository's local checkout."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    base = Path(repo.local_path).resolve()
    target = (base / file_path).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        content = target.read_text(errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return PlainTextResponse(content)
