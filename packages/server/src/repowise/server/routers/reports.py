"""/api/repos/{repo_id}/reports/first-look: the stored report, its prompts and a rewrite.

A thin layer over ``repowise.core.report.first_look``: the report is read
from the index (the file in the checkout when the index has none), compared
with the index as it is now, and each item's prompt is rendered on demand,
one per request, because the action prompts read the whole action view.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.agent_prompts import Flavor
from repowise.core.cost_estimator import estimate_first_look_cost
from repowise.core.persistence import crud
from repowise.core.persistence.models import Repository
from repowise.core.report.first_look import FirstLookReport
from repowise.core.report.first_look.config import first_look_settings
from repowise.core.report.first_look.llm import first_look_model
from repowise.core.report.first_look.prompts import render_item_prompt, render_report_prompt
from repowise.core.report.first_look.staleness import staleness
from repowise.core.report.first_look.store import load_report, read_report_file
from repowise.core.report.first_look.write import write_first_look
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.routers._repo_pricing import _provider_configured, resolve_provider
from repowise.server.schemas.agent_prompts import AgentPromptResponse
from repowise.server.schemas.reports import FirstLookResponse, FirstLookStampResponse

router = APIRouter(tags=["reports"], dependencies=[Depends(verify_api_key)])

_NO_REPORT = (
    "No first-look report for this repository yet. `repowise init` writes one; "
    "`repowise report first-look` writes it again."
)


async def _require_repo(session: AsyncSession, repo_id: str) -> Repository:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


def _root(repo: Repository) -> Path | None:
    root = Path(repo.local_path) if repo.local_path else None
    return root if root is not None and root.is_dir() else None


async def _require_report(session: AsyncSession, repo: Repository) -> FirstLookReport:
    report = await load_report(session, repo.id)
    if report is None and (root := _root(repo)) is not None:
        report = read_report_file(root)
    if report is None:
        raise HTTPException(status_code=404, detail=_NO_REPORT)
    return report


def _rewrite_offer(repo: Repository) -> tuple[bool, float | None]:
    """Whether a model can write the report, and what its one call costs."""
    provider, provider_name, _, _ = resolve_provider(repo.local_path, repo_id=repo.id)
    if provider is None or not provider_name:
        return _provider_configured(repo.local_path, repo.id), None
    root = _root(repo)
    model = first_look_model(provider, first_look_settings(root).model if root else None)
    return True, estimate_first_look_cost(provider_name, model).estimated_cost_usd


async def _page(
    session: AsyncSession, repo: Repository, report: FirstLookReport, flavor: Flavor
) -> FirstLookResponse:
    stale = await staleness(session, repo.id, report, _root(repo))
    can_rewrite, cost = _rewrite_offer(repo)
    return FirstLookResponse(
        report=report.to_json(),
        staleness=stale.to_json(),
        report_prompt=render_report_prompt(report, flavor=flavor, repo_name=repo.name),
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
    """The stored report, how far the index has moved since, and its whole-report prompt."""
    repo = await _require_repo(session, repo_id)
    return await _page(session, repo, await _require_report(session, repo), flavor)


@router.get("/api/repos/{repo_id}/reports/first-look/stamp", response_model=FirstLookStampResponse)
async def get_first_look_stamp(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> FirstLookStampResponse:
    """When the stored report was written: enough for a link to it, without the staleness pass."""
    repo = await _require_repo(session, repo_id)
    stamp = (await _require_report(session, repo)).stamp
    return FirstLookStampResponse(
        commit=stamp.commit, created_at=stamp.created_at, generator=stamp.generator, cost=stamp.cost
    )


@router.get("/api/repos/{repo_id}/reports/first-look/prompt", response_model=AgentPromptResponse)
async def get_first_look_prompt(
    repo_id: str,
    flavor: Flavor = Query("generic"),
    item: str | None = Query(None, max_length=32, description="An item id; omit for the whole report."),
    session: AsyncSession = Depends(get_db_session),
) -> AgentPromptResponse:
    """One item's "Fix with your agent" prompt, or the whole report's, worded for *flavor*."""
    repo = await _require_repo(session, repo_id)
    report = await _require_report(session, repo)
    if item is None:
        return AgentPromptResponse(
            flavor=flavor, text=render_report_prompt(report, flavor=flavor, repo_name=repo.name)
        )
    found = report.item(item)
    if found is None:
        raise HTTPException(status_code=404, detail="No item with that id in the report")
    text = await render_item_prompt(session, repo_id, found, flavor=flavor, repo_name=repo.name)
    return AgentPromptResponse(flavor=flavor, text=text)


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
    report = await write_first_look(session, repo_id, _root(repo), provider=provider)
    return await _page(session, repo, report, flavor)
