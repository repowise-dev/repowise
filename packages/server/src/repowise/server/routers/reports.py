"""/api/repos/{repo_id}/reports/first-look: the repo report, its prompts, marks, exports and a rewrite.

A thin layer over ``repowise.core.report.first_look``: every read is the
living report (:func:`~repowise.core.report.first_look.living.build_living`),
the report at the index now, measured against the stored first look, with each
item's status. Marks go through :func:`~repowise.core.report.first_look.marks.mark_item`,
the call ``repowise mark`` makes. Each item's prompt is rendered on demand, one
per request, because the action prompts read the whole action view.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.agent_prompts import Flavor
from repowise.core.cost_estimator import estimate_first_look_cost
from repowise.core.persistence import crud
from repowise.core.persistence.models import Repository
from repowise.core.report.first_look.config import first_look_settings
from repowise.core.report.first_look.living import LivingReport, build_living
from repowise.core.report.first_look.llm import first_look_model
from repowise.core.report.first_look.marks import MarkError, find_item, mark_item
from repowise.core.report.first_look.prompts import render_item_prompt, render_report_prompt
from repowise.core.report.first_look.render import render_first_look_markdown
from repowise.core.report.first_look.write import render_document, write_first_look
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.routers._repo_pricing import _provider_configured, resolve_provider
from repowise.server.schemas.agent_prompts import AgentPromptResponse
from repowise.server.schemas.reports import (
    FirstLookMarkRequest,
    FirstLookMarkResponse,
    FirstLookResponse,
)

router = APIRouter(tags=["reports"], dependencies=[Depends(verify_api_key)])


async def _require_repo(session: AsyncSession, repo_id: str) -> Repository:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


def _root(repo: Repository) -> Path | None:
    root = Path(repo.local_path) if repo.local_path else None
    return root if root is not None and root.is_dir() else None


async def _living(session: AsyncSession, repo: Repository) -> LivingReport:
    # A read never writes: with no stored report the page shows the report now, without a baseline.
    return await build_living(session, repo.id, _root(repo))


def _statuses(living: LivingReport) -> dict[str, str]:
    return {i: s.status for i, s in living.statuses.items()}


def _rewrite_offer(repo: Repository) -> tuple[bool, float | None]:
    """Whether a model can write the report, and what its one call costs."""
    provider, provider_name, _, _ = resolve_provider(repo.local_path, repo_id=repo.id)
    if provider is None or not provider_name:
        return _provider_configured(repo.local_path, repo.id), None
    root = _root(repo)
    model = first_look_model(provider, first_look_settings(root).model if root else None)
    return True, estimate_first_look_cost(provider_name, model).estimated_cost_usd


async def _page(session: AsyncSession, repo: Repository, flavor: Flavor) -> FirstLookResponse:
    living = await _living(session, repo)
    can_rewrite, cost = _rewrite_offer(repo)
    return FirstLookResponse(
        report=living.to_json(),
        report_prompt=render_report_prompt(
            living.report, flavor=flavor, repo_name=repo.name, statuses=_statuses(living)
        ),
        flavor=flavor,
        can_rewrite=can_rewrite,
        rewrite_cost_usd=cost,
    )


@router.get("/api/repos/{repo_id}/reports/first-look", response_model=FirstLookResponse)
async def get_first_look(
    repo_id: str,
    flavor: Flavor = Query("generic"),
    session: AsyncSession = Depends(get_db_session),
) -> FirstLookResponse:
    """The report at the index now, each item's status against the first look, and the open items' prompt."""
    return await _page(session, await _require_repo(session, repo_id), flavor)


@router.get("/api/repos/{repo_id}/reports/first-look/prompt", response_model=AgentPromptResponse)
async def get_first_look_prompt(
    repo_id: str,
    flavor: Flavor = Query("generic"),
    item: str | None = Query(None, max_length=32, description="An item id; omit for the whole report."),
    section: str | None = Query(None, max_length=32, description="A section key: its open items only."),
    session: AsyncSession = Depends(get_db_session),
) -> AgentPromptResponse:
    """One item's prompt, one section's, or the whole report's, worded for *flavor*."""
    if item is not None and section is not None:
        raise HTTPException(status_code=422, detail="Ask for an item or a section, not both")
    repo = await _require_repo(session, repo_id)
    if item is not None:
        found = await find_item(session, repo_id, item, _root(repo))
        if found is None:
            raise HTTPException(status_code=404, detail="No item with that id in the report")
        text = await render_item_prompt(session, repo_id, found, flavor=flavor, repo_name=repo.name)
        return AgentPromptResponse(flavor=flavor, text=text)
    living = await _living(session, repo)
    report = living.report
    if section is not None:
        block = report.section(section)
        if block is None:
            raise HTTPException(status_code=404, detail="No section with that key in the report")
        report = replace(report, sections=(block,))
    text = render_report_prompt(report, flavor=flavor, repo_name=repo.name, statuses=_statuses(living))
    return AgentPromptResponse(flavor=flavor, text=text)


@router.put(
    "/api/repos/{repo_id}/reports/first-look/items/{item_id}/mark",
    response_model=FirstLookMarkResponse,
)
async def mark_first_look_item(
    repo_id: str,
    item_id: str,
    body: FirstLookMarkRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Done, acknowledged, a false positive or snoozed: the answer `repowise mark` records."""
    repo = await _require_repo(session, repo_id)
    try:
        return await mark_item(
            session,
            repo.id,
            item_id,
            body.status,
            reason=body.reason,
            days=body.days,
            repo_root=_root(repo),
        )
    except MarkError as exc:
        status = 404 if exc.code == "unknown_item" else 422
        raise HTTPException(status_code=status, detail=exc.message) from exc


@router.get("/api/repos/{repo_id}/reports/first-look/export.md", response_class=PlainTextResponse)
async def export_first_look_markdown(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> PlainTextResponse:
    """The report as a Markdown file: the document the CLI writes, statuses as text, no prompts."""
    repo = await _require_repo(session, repo_id)
    doc = await render_document(session, repo_id, await _living(session, repo), prompts=False)
    return PlainTextResponse(
        render_first_look_markdown(doc),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="repo-report.md"'},
    )


@router.post("/api/repos/{repo_id}/reports/first-look/rewrite", response_model=FirstLookResponse)
async def rewrite_first_look(
    repo_id: str,
    flavor: Flavor = Query("generic"),
    session: AsyncSession = Depends(get_db_session),
) -> FirstLookResponse:
    """Write the report again at the indexed commit, its prose by the repo's model (one call)."""
    repo = await _require_repo(session, repo_id)
    provider, _, _, error = resolve_provider(repo.local_path, repo_id=repo_id)
    if provider is None:
        raise HTTPException(
            status_code=409, detail=error or "No model provider is configured for this repository"
        )
    await write_first_look(session, repo_id, _root(repo), provider=provider)
    return await _page(session, repo, flavor)
