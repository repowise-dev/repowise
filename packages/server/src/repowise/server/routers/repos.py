"""/api/repos — Repository CRUD + sync endpoints."""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.docs_mode import resolve_docs_mode
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
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> RepoResponse:
    """Register a new repository (or update if same local_path exists).

    The repository's data lives in its own ``<repo>/.repowise/wiki.db`` (the
    same store the CLI uses); the server's primary database only keeps a
    registry row so the repo stays listed across restarts. With ``index``
    (the default) the first full index — docs included, when a provider is
    configured — is enqueued immediately; the created job's id is returned
    as ``initial_job_id`` so clients can attach to its progress stream.
    """
    if not body.index:
        # Metadata-only registration (kept for API compatibility and tests):
        # the row lands in the ambient DB; per-repo storage is established
        # when the repo is first indexed (here with index=true, or later via
        # POST /api/repos/{id}/index).
        repo = await crud.upsert_repository(
            session,
            name=body.name,
            local_path=body.local_path,
            url=body.url,
            default_branch=body.default_branch,
            settings=body.settings,
        )
        return RepoResponse.from_orm(repo)

    from repowise.server.repo_db import ensure_repo_registration, upsert_registry_row

    app_state = request.app.state

    # Canonical row in the repo-local DB; the factory routes all later access.
    repo_factory, repo_id = await ensure_repo_registration(
        app_state,
        local_path=body.local_path,
        name=body.name,
        url=body.url,
        default_branch=body.default_branch,
        settings=body.settings,
    )

    # Apply any metadata updates to the canonical row (registration itself
    # never clobbers an existing row).
    async with get_session(repo_factory) as repo_session:
        repo = await crud.get_repository(repo_session, repo_id)
        if repo is not None:
            repo.name = body.name
            repo.url = body.url
            repo.default_branch = body.default_branch
            if body.settings is not None:
                import json as _json

                repo.settings_json = _json.dumps(body.settings)
            await repo_session.flush()
            response = RepoResponse.from_orm(repo)
        else:  # pragma: no cover — the row was created two lines above
            raise HTTPException(status_code=500, detail="Repository registration failed")

    # Registry row in the primary DB (skip when the repo IS the primary DB).
    if repo_factory is not app_state.session_factory:
        await upsert_registry_row(
            session,
            repo_id=repo_id,
            name=body.name,
            local_path=body.local_path,
            url=body.url,
            default_branch=body.default_branch,
            settings=body.settings,
        )
        await session.commit()

    response.initial_job_id = await _enqueue_index_job(request, repo_factory, repo_id)
    return response


async def _enqueue_index_job(request: Request, session_factory, repo_id: str) -> str | None:
    """Create and launch an ``initial_index`` job unless one is already active."""
    async with get_session(session_factory) as session:
        active = await session.execute(
            select(GenerationJob.id)
            .where(GenerationJob.repository_id == repo_id)
            .where(GenerationJob.status.in_(["pending", "running"]))
            .limit(1)
        )
        if active.scalar_one_or_none() is not None:
            return None
        job = await crud.upsert_generation_job(
            session,
            repository_id=repo_id,
            status="pending",
            config={"mode": "initial_index"},
        )
        # Commit (not just flush) so the background task's separate session
        # can see the job row.
        await session.commit()
        job_id = job.id
    _launch_job_task(request, job_id, repo_id)
    return job_id


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

    # Self-heal the freshness stamp on read: prefer each repo's state.json
    # last_sync_commit over a possibly-stale DB head_commit, so a row left
    # un-stamped by an older build doesn't make the extension report "index
    # behind checkout". The DB row is repaired for good on the next update.
    from repowise.server.mcp_server._meta import resolve_indexed_commit

    for resp in responses:
        if resp.local_path:
            resp.head_commit = resolve_indexed_commit(resp.head_commit, resp.local_path)

    # Flag registered-but-never-indexed repos. head_commit can't signal this
    # (registration stamps it from the live git HEAD), so the honest check is
    # the repo-local store: since the initial-index path always establishes
    # <repo>/.repowise/wiki.db, its absence means the first index hasn't run.
    # Reuses the workspace "needs_index" contract the sidebar already renders.
    from pathlib import Path as _Path

    for resp in responses:
        if resp.workspace_status is None and resp.local_path:
            try:
                if not (_Path(resp.local_path) / ".repowise" / "wiki.db").is_file():
                    resp.workspace_status = "needs_index"
            except OSError:
                pass

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

        # The docs mode and index tier are recorded per-repo in state.json.
        # Read it once per response: cheap, and never failing.
        state_path = _P(resp.local_path) / ".repowise" / "state.json"
        if state_path.is_file():
            try:
                state = _json.loads(state_path.read_text(encoding="utf-8"))
                resp.docs_mode = resolve_docs_mode(state)
                # A state file predating every docs field used to report
                # docs_enabled=True by default. Deriving the flag from the
                # resolved mode alone would flip those old indexes to False,
                # so keep the legacy default when nothing at all is recorded.
                if not any(
                    k in state for k in ("docs_mode", "docs_enabled", "provider", "model")
                ):
                    resp.docs_enabled = True
                else:
                    resp.docs_enabled = resp.docs_mode != "none"
                resp.docs_skip_reason = state.get("docs_skip_reason")
                resp.run_mode = state.get("run_mode")
                resp.git_tier = state.get("git_tier")
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
                docs_mode="none",
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
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    fts=Depends(get_fts),  # noqa: B008
) -> dict:
    """Delete a repository and all its data."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Collect page IDs before CASCADE deletes the Page rows
    page_ids = await crud.list_page_ids(session, repo_id)

    # Clean up FTS index (FTS5 virtual table has no FK cascade). Use the
    # repo's own FTS instance when it lives in a per-repo database.
    repo_fts = getattr(request.app.state, "workspace_fts", {}).get(repo_id) or fts
    if repo_fts is not None:
        await repo_fts.delete_many(page_ids)

    # Delete repository — CASCADE handles all child ORM tables
    await crud.delete_repository(session, repo_id)

    # Drop per-repo routing and the primary-DB registry row, if any, so the
    # repo neither lingers in listings nor resurrects on the next restart.
    app_state = request.app.state
    ws_sessions = getattr(app_state, "workspace_sessions", None) or {}
    if repo_id in ws_sessions:
        ws_sessions.pop(repo_id, None)
        getattr(app_state, "workspace_fts", {}).pop(repo_id, None)
        try:
            async with get_session(app_state.session_factory) as primary:
                registry = await crud.get_repository(primary, repo_id)
                if registry is not None:
                    await crud.delete_repository(primary, repo_id)
        except Exception:
            logger.debug("registry_row_delete_failed", extra={"repo_id": repo_id})

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


async def _ensure_no_active_job(session: AsyncSession, repo_id: str) -> None:
    """Raise 409 if a pending/running job already holds this repo.

    The active-job guard is repo-wide: overlapping runs share a process-global
    cancel-token slot, so a second concurrent job is refused rather than started.
    Shared by every job-launching endpoint.
    """
    active = await session.execute(
        select(GenerationJob.id)
        .where(GenerationJob.repository_id == repo_id)
        .where(GenerationJob.status.in_(["pending", "running"]))
        .limit(1)
    )
    if active.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409, detail="A job is already in progress for this repository"
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

    await _ensure_no_active_job(session, repo_id)

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

    await _ensure_no_active_job(session, repo_id)

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


class GenerateSelectionBody(BaseModel):
    """Which pages a generate request targets.

    Mirrors the CLI's explicit selection: ``all`` / ``unwritten`` / ``stale``,
    an explicit ``page_ids`` list, or every page under a ``path_prefix``.
    """

    kind: Literal["all", "unwritten", "stale", "page_ids", "path_prefix"] = "unwritten"
    page_ids: list[str] | None = None
    path_prefix: str | None = None


class GenerateRequestBody(BaseModel):
    """Body for the generate + estimate endpoints."""

    selection: GenerateSelectionBody = Field(default_factory=GenerateSelectionBody)
    cascade: Literal["none", "dependents", "full"] = "dependents"
    style: str | None = None


def _validate_generate_style(style: str | None) -> None:
    """Reject an unknown wiki style with a 400 listing the valid ones."""
    if style is None:
        return
    from repowise.core.generation.styles import is_known_style, list_styles

    if not is_known_style(style):
        valid = ", ".join(s.name for s in list_styles())
        raise HTTPException(status_code=400, detail=f"Unknown style '{style}'. Valid styles: {valid}.")


def _generate_job_config(body: GenerateRequestBody) -> dict:
    """Build the executor's job config from a validated request body."""
    selection: dict = {"kind": body.selection.kind}
    if body.selection.kind == "page_ids":
        selection["page_ids"] = body.selection.page_ids or []
    elif body.selection.kind == "path_prefix":
        selection["path_prefix"] = body.selection.path_prefix
    config: dict = {"mode": "generate", "selection": selection, "cascade": body.cascade}
    if body.style is not None:
        config["style"] = body.style
    return config


@router.post("/{repo_id}/generate", status_code=202)
async def generate_pages(
    repo_id: str,
    request: Request,
    body: GenerateRequestBody,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Write a subset of the wiki with a model (the HTTP ``repowise generate``).

    Launches a background ``generate`` job that rehydrates the graph, resolves
    the requested selection + cascade, and writes exactly those pages via the
    shared core engine. Returns immediately with a job id to stream.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    _validate_generate_style(body.style)
    await _ensure_no_active_job(session, repo_id)

    job = await crud.upsert_generation_job(
        session,
        repository_id=repo_id,
        status="pending",
        config=_generate_job_config(body),
    )
    # Commit (not just flush) so the background task's separate session sees the
    # job row.  See sync_repo comment for rationale.
    await session.commit()
    _launch_job_task(request, job.id, repo_id)
    return {"job_id": job.id, "status": "accepted"}


@router.post("/{repo_id}/generate/estimate")
async def generate_estimate(
    repo_id: str,
    request: Request,
    body: GenerateRequestBody,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Cost + page counts for a generate selection, including cascade fallout.

    Resolves the exact same scope the job would (rehydrating the graph and
    re-parsing), so the returned page count and estimate match what a launched
    job spends. Heavier than the pre-index preflight because it walks the real
    dependency graph rather than a file count.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    _validate_generate_style(body.style)
    repo_path = Path(repo.local_path)

    from repowise.core.generation.scope import resolve_scope
    from repowise.core.pipeline.scoped_generation import rehydrate_repo
    from repowise.server.job_executor import (
        _build_generate_intent,
        _build_generation_config,
        _load_state,
        _repo_exclude_patterns,
        _repo_wiki_style,
    )

    exclude_patterns = _repo_exclude_patterns(repo, str(repo_path))
    wiki_style = _repo_wiki_style(repo, str(repo_path))
    job_config = _generate_job_config(body)
    gen_config = _build_generation_config(repo_path, job_config, wiki_style)

    # Price with the repo's configured provider/model, if one resolves.
    provider_name: str | None = None
    model_name: str | None = None
    provider_error: str | None = None
    try:
        from repowise.server.provider_config import get_chat_provider_instance

        llm_client = get_chat_provider_instance(repo_path=str(repo_path))
        provider_name = getattr(llm_client, "provider_name", None)
        model_name = getattr(llm_client, "model_name", None)
    except Exception as exc:
        provider_error = str(exc)

    session_factory = _resolve_repo_session_factory(request.app.state, repo_id)
    state = _load_state(repo_path)
    # Read-only preflight: an un-indexed repo (no persisted graph) or one with no
    # wiki pages yet is a zero estimate, not an error. A launched job would fail
    # loudly instead; here we just report there is nothing to price.
    note: str | None = None
    rehydrated = None
    try:
        rehydrated = await rehydrate_repo(
            session_factory,
            repo_id,
            repo_path,
            generation_config=gen_config,
            exclude_patterns=exclude_patterns,
            include_submodules=bool(state.get("include_submodules", False)),
            include_nested_repos=bool(state.get("include_nested_repos", False)),
        )
    except Exception as exc:
        note = str(exc)

    if rehydrated is None:
        return {
            "total_pages": 0,
            "pages_by_type": {},
            "pages_to_mark_stale": 0,
            "unknown_page_ids": [],
            "provider": {"name": provider_name, "model": model_name, "error": provider_error},
            "estimate": None,
            "note": note,
        }

    plan = resolve_scope(
        records=rehydrated.records,
        intent=_build_generate_intent(job_config),
        cascade_mode=body.cascade,
        deps=rehydrated.deps,
    )
    pages_by_type = {p.page_type: p.count for p in plan.cost_plans}
    total_pages = sum(pages_by_type.values())

    estimate: dict | None = None
    if provider_name and model_name and plan.cost_plans:
        from repowise.core.cost_estimator import estimate_cost

        est = estimate_cost(plan.cost_plans, provider_name, model_name, repo_path=str(repo_path))
        estimate = {
            "estimated_cost_usd": round(est.estimated_cost_usd, 4),
            "cost_low_usd": round(est.cost_range.low, 4) if est.cost_range else None,
            "cost_high_usd": round(est.cost_range.high, 4) if est.cost_range else None,
            "estimated_input_tokens": est.estimated_input_tokens,
            "estimated_output_tokens": est.estimated_output_tokens,
            "is_calibrated": est.is_calibrated,
        }

    return {
        "total_pages": total_pages,
        "pages_by_type": pages_by_type,
        "pages_to_mark_stale": len(plan.stale_ids),
        "unknown_page_ids": list(plan.unknown_page_ids),
        "provider": {"name": provider_name, "model": model_name, "error": provider_error},
        "estimate": estimate,
    }


@router.post("/{repo_id}/index", status_code=202)
async def index_repo(
    repo_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Run the first full index (docs included) for a registered repository.

    Unlike ``/sync`` and ``/full-resync``, this endpoint also establishes the
    repo-local database and writes the ``repowise init`` baseline
    (``state.json``, ``config.yaml``), so a repo that was merely registered
    becomes fully indexed and CLI-compatible. Safe to call on an already
    indexed repo — it behaves like a full rebuild.
    """
    from repowise.server.repo_db import ensure_repo_registration

    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Carry settings (e.g. a wiki_style chosen at registration) into the
    # repo-local row this call may be creating; an existing row is never
    # clobbered by registration.
    import json as _json

    try:
        settings = _json.loads(repo.settings_json) or None
    except (TypeError, ValueError):
        settings = None

    factory, canonical_id = await ensure_repo_registration(
        request.app.state,
        local_path=repo.local_path,
        name=repo.name,
        url=repo.url,
        default_branch=repo.default_branch,
        settings=settings,
        repo_id=repo.id,
    )
    job_id = await _enqueue_index_job(request, factory, canonical_id)
    if job_id is None:
        raise HTTPException(
            status_code=409, detail="A job is already in progress for this repository"
        )
    return {"job_id": job_id, "status": "accepted"}


@router.post("/{repo_id}/preflight")
async def preflight_index(
    repo_id: str,
    request: Request,
    coverage_pct: float = Query(0.20, ge=0.0, le=1.0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Pre-index readiness check: provider connectivity + rough cost estimate.

    Mirrors the CLI's pre-generation gate — a live provider smoke test plus a
    page-count/cost estimate — so the UI can surface the expected spend and a
    broken API key *before* launching an index job. The estimate is derived
    from a fast file walk (no parsing), so page counts are approximate; the
    reported range absorbs the variance.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    repo_path = repo.local_path
    from repowise.server.job_executor import _repo_exclude_patterns

    exclude_patterns = _repo_exclude_patterns(repo, repo_path)

    # ---- Provider smoke test (same probe the CLI uses at init) ----
    provider_ok = False
    provider_name: str | None = None
    model_name: str | None = None
    provider_error: str | None = None
    llm_client = None
    try:
        from repowise.server.provider_config import get_chat_provider_instance

        llm_client = get_chat_provider_instance(repo_path=repo_path)
        provider_name = getattr(llm_client, "provider_name", None)
        model_name = getattr(llm_client, "model_name", None)
    except Exception as exc:
        provider_error = str(exc)

    if llm_client is not None:
        try:
            await llm_client.generate("You are a test.", "Reply with OK.", max_tokens=50)
            provider_ok = True
        except Exception as exc:
            provider_error = str(exc)

    # ---- File count + cost estimate ----
    def _count_files() -> int:
        from repowise.core.ingestion import FileTraverser

        traverser = FileTraverser(
            Path(repo_path),
            extra_exclude_patterns=exclude_patterns or None,
        )
        return sum(1 for _ in traverser.traverse())

    try:
        file_count = await asyncio.to_thread(_count_files)
    except Exception:
        logger.exception("preflight_file_count_failed", extra={"repo_id": repo_id})
        file_count = 0

    estimate: dict | None = None
    if provider_name and model_name:
        from repowise.core.cost_estimator import approximate_generation_plan, estimate_cost

        plans = approximate_generation_plan(file_count, coverage_pct=coverage_pct)
        est = estimate_cost(plans, provider_name, model_name, repo_path=repo_path)
        estimate = {
            "total_pages": est.total_pages,
            "estimated_cost_usd": round(est.estimated_cost_usd, 4),
            "cost_low_usd": round(est.cost_range.low, 4) if est.cost_range else None,
            "cost_high_usd": round(est.cost_range.high, 4) if est.cost_range else None,
            "estimated_input_tokens": est.estimated_input_tokens,
            "estimated_output_tokens": est.estimated_output_tokens,
            "is_calibrated": est.is_calibrated,
            "coverage_pct": coverage_pct,
        }

    return {
        "provider": {
            "ok": provider_ok,
            "name": provider_name,
            "model": model_name,
            "error": provider_error,
        },
        "file_count": file_count,
        "estimate": estimate,
    }


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

    async def _mark_terminal(status: str, reason: str) -> None:
        try:
            from repowise.core.persistence.crud import update_job_status

            async with get_session(session_factory) as session:
                await update_job_status(
                    session,
                    job_id,
                    status,
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
        asyncio.create_task(_mark_terminal("failed", f"Failed to launch background task: {exc}"))
        return

    bg_tasks: set[asyncio.Task] = app_state.background_tasks  # type: ignore[assignment]
    bg_tasks.add(task)
    # Track by job id so the cancel endpoint can interrupt the task itself.
    job_tasks = getattr(app_state, "job_tasks", None)
    if job_tasks is None:
        job_tasks = {}
        app_state.job_tasks = job_tasks
    job_tasks[job_id] = task

    def _on_done(t: asyncio.Task) -> None:
        bg_tasks.discard(t)
        job_tasks.pop(job_id, None)
        if t.cancelled():
            # execute_job normally records "cancelled" itself; this covers a
            # cancel that landed before its try block was entered.
            asyncio.create_task(_mark_terminal("cancelled", "Cancelled by user"))
            return
        exc = t.exception()
        if exc is not None:
            logger.error("background_job_failed", exc_info=exc)
            # execute_job already tries to mark failed in its except block,
            # but if that itself raised we must still ensure the row is
            # not left in pending/running.
            asyncio.create_task(_mark_terminal("failed", f"Background task crashed: {exc}"))

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
