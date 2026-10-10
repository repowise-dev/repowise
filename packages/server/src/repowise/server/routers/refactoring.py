"""/api/repos/{repo_id}/refactoring — deterministic refactoring plans.

The refactoring layer writes one structured ``RefactoringSuggestion`` row per
opportunity (Extract Class, Extract Helper, Move Method, Break Cycle). These
endpoints read those rows through the canonical recommendation service so the
web tab, CLI, and MCP share priority components and ordering. Centrality is
leverage; a larger change surface raises cost and risk rather than benefit.

No on-disk work happens here, so this works on hosted backends without a
checkout — the same property the C4 endpoints rely on. The exceptions are the
code-generation settings and code generation itself, which need the checkout.

Routes only adapt: reads and ranking are ``services/refactoring_health.py``,
the settings ``services/refactoring_settings.py``, the shapes
``schemas/refactoring.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.agent_prompts import Flavor, render_opportunity, render_plan
from repowise.core.analysis.health.refactoring.recipe import build_recipe
from repowise.core.analysis.health.refactoring.serving import (
    CANONICAL_ORDERS,
    CANONICAL_VIEWS,
    DEFAULT_VIEW,
    parse_query,
)
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.refactoring import ALLOWED_STATUSES
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.routers._local_git import local_repo_path
from repowise.server.schemas import (
    GenerateCodeRequest,
    GenerateCodeResponse,
    RefactoringOpportunitiesResponse,
    RefactoringOpportunityDetailResponse,
    RefactoringOpportunityStatusResponse,
    RefactoringOpportunityStatusUpdate,
    RefactoringPlanDetailResponse,
    RefactoringPlanStatusResponse,
    RefactoringRollupResponse,
    RefactoringSettings,
    RefactoringSettingsUpdate,
    RefactoringStatusUpdate,
    RefactoringTargetsResponse,
)
from repowise.server.schemas.agent_prompts import AgentPromptResponse
from repowise.server.services.refactoring_health import PlanListQuery, RefactoringHealthService
from repowise.server.services.refactoring_settings import refactoring_settings, save_llm_enabled

_STEPS_PER_ROW = 3
"""Steps carried on a queue row; the detail call pages the rest."""

_PROMPT_STEPS = 50
_PROMPT_EVIDENCE = 20
"""What an opportunity prompt inlines: the pages the web drawer reads."""

router = APIRouter(
    prefix="/api/repos",
    tags=["refactoring"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Endpoints — declare the static `targets` path before the dynamic id path so
# FastAPI matches it first.
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/refactoring/targets", response_model=RefactoringTargetsResponse)
async def get_refactoring_targets(
    repo_id: str,
    refactoring_type: str | None = Query(
        None,
        description="Filter to one type: extract_class | extract_helper | move_method | break_cycle",
    ),
    min_confidence: str | None = Query(None, description="low | medium | high"),
    file_path: str | None = Query(None, description="Filter plans to one repo-relative file path"),
    view: Literal["canonical", "file_spread"] = Query(
        "canonical", description="Named ordering view; canonical is the product default"
    ),
    session: AsyncSession = Depends(get_db_session),
) -> RefactoringTargetsResponse:
    """Ranked refactoring plans for the repo, filterable by type, confidence,
    and file.

    The summary ignores the *type* and *file* filters (so the per-type chips
    always show every type's total, even while one type is selected) but does
    honor *min_confidence* — so the summary and the plan list stay consistent
    under a confidence filter.
    """
    body = await _service(session, repo_id).ranked_plans(
        PlanListQuery(
            refactoring_type=refactoring_type,
            min_confidence=min_confidence,
            file_path=file_path,
            view=view,
        )
    )
    return RefactoringTargetsResponse(**body)


# ---------------------------------------------------------------------------
# Composed opportunities. Thin adapters: filtering, ordering, paging, facets
# and detail all live in ``services/refactoring_health.py``, which the MCP
# surface reads through as well, so the two cannot answer differently.
# ---------------------------------------------------------------------------


def _service(session: AsyncSession, repo_id: str) -> RefactoringHealthService:
    return RefactoringHealthService(session, repo_id, repo_id)


@router.get(
    "/{repo_id}/refactoring/opportunities",
    response_model=RefactoringOpportunitiesResponse,
    # ``ignored_arguments`` is only present when the query dropped something;
    # a default would put an empty object on every response.
    response_model_exclude_unset=True,
)
async def get_refactoring_opportunities(
    repo_id: str,
    refactoring_type: str | None = Query(
        None, description="Lead refactoring type, or several comma-separated"
    ),
    status: str = Query(
        "open", description="open | acknowledged | resolved | false_positive"
    ),
    confidence: str | None = Query(None, description="low | medium | high"),
    effort: str | None = Query(None, description="S | M | L | XL"),
    file_path: str | None = Query(None, description="One repo-relative file path"),
    search: str | None = Query(None, description="Substring of the file path"),
    mechanical: bool = Query(False, description="Only opportunities with a mechanical step"),
    scope: str | None = Query(
        None,
        description=(
            "fix_first (default for the open queue with no file_path): only what Fix first "
            "would take, the rest counted in `hidden` | all: the full inventory"
        ),
    ),
    view: str = Query(DEFAULT_VIEW, description=" | ".join(CANONICAL_VIEWS)),
    order: str | None = Query(None, description=" | ".join(CANONICAL_ORDERS)),
    step_preview: int = Query(
        _STEPS_PER_ROW,
        ge=0,
        le=20,
        description="Steps inlined per row. 0 for a list that renders counts only.",
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """One page of composed opportunities, with facets and the rollup.

    ``step_preview`` defaults to three, which is what an agent reading the queue
    wants. A product list that renders the step *counts* and opens a drawer for
    the rest asks for zero: the steps are most of the row's bytes and none of
    its pixels.
    """
    query, ignored = parse_query(
        lead_type=refactoring_type,
        status=status,
        confidence=confidence,
        effort=effort,
        mechanical=mechanical,
        file_paths=[file_path] if file_path else None,
        search=search,
        view=view,
        order=order,
        limit=limit,
        offset=offset,
        scope=scope,
    )
    page = await _service(session, repo_id).page(
        query,
        steps_per_item=step_preview if step_preview > 0 else None,
        with_facets=True,
        with_summary=True,
    )
    body: dict[str, Any] = {
        "items": page.items,
        "total": page.total,
        "offset": page.offset,
        "has_more": page.next_offset is not None,
        "next_offset": page.next_offset,
        "facets": page.facets,
        "summary": page.summary,
        "scope": page.scope,
        "counts": page.counts,
    }
    # ``hidden`` restates ``counts.excluded`` for the board; remove once the
    # web reads ``counts`` (UI adoption).
    if page.hidden is not None:
        body["hidden"] = page.hidden
    if ignored:
        body["ignored_arguments"] = ignored
    return body


@router.get("/{repo_id}/refactoring/summary", response_model=RefactoringRollupResponse)
async def get_refactoring_rollup(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """The repository rollup and its one lead, by primary key."""
    service = _service(session, repo_id)
    return {"summary": await service.summary(), "directive": await service.directive()}


@router.get(
    "/{repo_id}/refactoring/opportunities/{opportunity_id}",
    response_model=RefactoringOpportunityDetailResponse,
    response_model_exclude_unset=True,
)
async def get_refactoring_opportunity_detail(
    repo_id: str,
    opportunity_id: str,
    step_limit: int = Query(20, ge=0, le=200),
    step_offset: int = Query(0, ge=0),
    evidence_limit: int = Query(8, ge=0, le=200),
    evidence_offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """One opportunity: its ordered steps, evidence, validation and plans."""
    detail = await _service(session, repo_id).detail(
        opportunity_id,
        step_limit=step_limit,
        step_offset=step_offset,
        evidence_limit=evidence_limit,
        evidence_offset=evidence_offset,
    )
    if not detail.get("found"):
        raise HTTPException(status_code=404, detail="Unknown opportunity id")
    return detail


@router.get(
    "/{repo_id}/refactoring/opportunities/{opportunity_id}/prompt",
    response_model=AgentPromptResponse,
)
async def get_refactoring_opportunity_prompt(
    repo_id: str,
    opportunity_id: str,
    flavor: Flavor = Query("generic"),
    session: AsyncSession = Depends(get_db_session),
) -> AgentPromptResponse:
    """One opportunity, its ordered steps and their plans, as an agent prompt."""
    repo = await _repository(session, repo_id)
    detail = await _service(session, repo_id).detail(
        opportunity_id, step_limit=_PROMPT_STEPS, evidence_limit=_PROMPT_EVIDENCE
    )
    if not detail.get("found"):
        raise HTTPException(status_code=404, detail="Unknown opportunity id")
    text = render_opportunity(detail, flavor, repo.name)
    return AgentPromptResponse(flavor=flavor, text=text)


@router.patch(
    "/{repo_id}/refactoring/opportunities/{opportunity_id}/status",
    response_model=RefactoringOpportunityStatusResponse,
)
async def update_refactoring_opportunity_state(
    repo_id: str,
    opportunity_id: str,
    payload: RefactoringOpportunityStatusUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Record a decision about one opportunity, and so about all of its steps.

    One request rather than one per step: the transition is applied to every
    member plan through the same owner the plan route uses, and the
    opportunity's own state is the rollup of what those plans then say.
    """
    if payload.status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {payload.status}")
    result = await crud.update_refactoring_opportunity_status(
        session, repo_id, opportunity_id, payload.status
    )
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"refactoring opportunity not found: {opportunity_id}"
        )
    row, updated = result
    if not updated:
        # The opportunity exists but none of its steps could be written, so
        # nothing was decided. Saying 200 here would report the caller's own
        # request back to them as the stored state.
        raise HTTPException(
            status_code=409,
            detail=(
                f"refactoring opportunity {opportunity_id} has no resolvable steps to "
                "transition; re-index the repository and try again"
            ),
        )
    await session.commit()
    return {
        "opportunity_id": row.opportunity_id,
        "status": row.status,
        "steps_updated": updated,
        "status_changed_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# Code-gen settings: the refactoring.llm config block. Declared before the
# dynamic /{suggestion_id} GET so the static `settings` path wins.


@router.get("/{repo_id}/refactoring/settings", response_model=RefactoringSettings)
async def get_refactoring_settings(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> RefactoringSettings:
    """Whether code generation is on for the repo, and the provider/model it uses."""
    from repowise.core.repo_config import load_repo_config

    repo_path = await local_repo_path(session, repo_id)
    return RefactoringSettings(
        **refactoring_settings(load_repo_config(repo_path), repo_id, repo_path)
    )


@router.put("/{repo_id}/refactoring/settings", response_model=RefactoringSettings)
async def update_refactoring_settings(
    repo_id: str,
    body: RefactoringSettingsUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> RefactoringSettings:
    """Write ``refactoring.llm.enabled`` to the repo's ``.repowise/config.yaml``."""
    repo_path = await local_repo_path(session, repo_id)
    config = save_llm_enabled(repo_path, body.enabled)
    return RefactoringSettings(**refactoring_settings(config, repo_id, repo_path))


@router.get(
    "/{repo_id}/refactoring/{suggestion_id}",
    response_model=RefactoringPlanDetailResponse,
    # Unset annotations stay absent: "never checked" is not "none found".
    response_model_exclude_unset=True,
)
async def get_refactoring_plan(
    repo_id: str,
    suggestion_id: str,
    include: Literal["recipe"] | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> RefactoringPlanDetailResponse:
    """One plan + its blast radius detail (deep-link / drill-down target)."""
    detail, public_id = await _plan_detail(session, repo_id, suggestion_id)
    if include == "recipe":
        detail["recipe"] = build_recipe({**detail, "id": public_id})
    return RefactoringPlanDetailResponse(**detail)


async def _plan_detail(
    session: AsyncSession, repo_id: str, suggestion_id: str
) -> tuple[dict[str, Any], str]:
    """The plan's detail dict and the public id an agent quotes back (the
    detail's own ``id`` is the storage key this route has always served)."""
    row = await crud.get_refactoring_suggestion(session, repo_id, suggestion_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"refactoring plan not found: {suggestion_id}")
    detail = (await _service(session, repo_id).plan_recommendation(row)).detail_dict()
    payoff = await crud.plan_payoff(session, row)
    if payoff is not None:
        detail["payoff"] = payoff
    return detail, row.public_id or row.id


async def _repository(session: AsyncSession, repo_id: str) -> Any:
    """The repository a prompt route names in its heading; 404 when unknown."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


@router.get(
    "/{repo_id}/refactoring/{suggestion_id}/prompt",
    response_model=AgentPromptResponse,
)
async def get_refactoring_plan_prompt(
    repo_id: str,
    suggestion_id: str,
    flavor: Flavor = Query("generic"),
    session: AsyncSession = Depends(get_db_session),
) -> AgentPromptResponse:
    """One plan as the prompt an agent starts from, worded for its harness."""
    repo = await _repository(session, repo_id)
    detail, public_id = await _plan_detail(session, repo_id, suggestion_id)
    text = render_plan({**detail, "id": public_id}, flavor, repo.name)
    return AgentPromptResponse(flavor=flavor, text=text)


@router.patch(
    "/{repo_id}/refactoring/{suggestion_id}/status",
    response_model=RefactoringPlanStatusResponse,
)
async def update_refactoring_plan_status(
    repo_id: str,
    suggestion_id: str,
    payload: RefactoringStatusUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Record a decision about one plan.

    ``false_positive`` also suppresses the plan on every later analysis, which
    is how a wrong suggestion stops coming back instead of being re-emitted.
    """
    if payload.status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {payload.status}")
    row = await crud.update_refactoring_suggestion_status(
        session, repo_id, suggestion_id, payload.status
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"refactoring plan not found: {suggestion_id}")
    await session.commit()
    return {
        "id": row.id,
        "public_id": row.public_id,
        "status": row.status,
        "status_reason": row.status_reason,
        "status_changed_at": row.status_changed_at.isoformat() if row.status_changed_at else None,
    }


# ---------------------------------------------------------------------------
# Opt-in LLM enrichment — plan -> generated code + diff
# ---------------------------------------------------------------------------


@router.post(
    "/{repo_id}/refactoring/{suggestion_id}/generate-code",
    response_model=GenerateCodeResponse,
)
async def generate_refactoring_code(
    repo_id: str,
    suggestion_id: str,
    body: GenerateCodeRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> GenerateCodeResponse:
    """Generate the refactored code + a unified diff for one plan, on demand.

    Opt-in: returns 403 unless ``refactoring.llm.enabled`` is true in the repo's
    ``.repowise/config.yaml``. The provider resolves exactly as chat's does.
    Needs the working tree on disk (it reads the plan's real source spans), so
    this is a local-``serve`` capability, not a hosted one — it returns 404 when
    the repo has no accessible checkout.
    """
    from repowise.core.analysis.health.refactoring.llm import (
        enrich_suggestion,
        llm_enrichment_enabled,
    )
    from repowise.core.repo_config import load_repo_config
    from repowise.server.provider_config import get_chat_provider_instance

    repo_path = await local_repo_path(session, repo_id)
    if not llm_enrichment_enabled(load_repo_config(repo_path)):
        raise HTTPException(
            status_code=403,
            detail="refactoring code generation is disabled (set refactoring.llm.enabled)",
        )

    row = await crud.get_refactoring_suggestion(session, repo_id, suggestion_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"refactoring plan not found: {suggestion_id}")
    recommendation = await _service(session, repo_id).plan_recommendation(row)
    sug = recommendation.suggestion
    detail = {**recommendation.detail_dict(), "id": row.public_id or row.id}

    body = body or GenerateCodeRequest()
    try:
        provider = get_chat_provider_instance(
            repo_path=repo_path,
            repo_id=repo_id,
            provider_override=body.provider,
            model_override=body.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await enrich_suggestion(sug, provider=provider, repo_path=repo_path, detail=detail)
    return GenerateCodeResponse(**result.to_dict())
