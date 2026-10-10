"""/api/repos/{repo_id}/refactoring — deterministic refactoring plans.

The refactoring layer writes one structured ``RefactoringSuggestion`` row per
opportunity (Extract Class, Extract Helper, Move Method, Break Cycle). These
endpoints read those rows through the canonical recommendation service so the
web tab, CLI, and MCP share priority components and ordering. Centrality is
leverage; a larger change surface raises cost and risk rather than benefit.

No on-disk work happens here, so this works on hosted backends without a
checkout — the same property the C4 endpoints rely on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.refactoring.serving import (
    CANONICAL_ORDERS,
    CANONICAL_VIEWS,
    DEFAULT_VIEW,
    parse_query,
)
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.refactoring import ALLOWED_STATUSES
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    RefactoringOpportunitiesResponse,
    RefactoringOpportunityDetailResponse,
    RefactoringOpportunityStatusResponse,
    RefactoringPlanStatusResponse,
    RefactoringRollupResponse,
)
from repowise.server.services.refactoring_health import PlanListQuery, RefactoringHealthService

_STEPS_PER_ROW = 3
"""Steps carried on a queue row; the detail call pages the rest."""

router = APIRouter(
    prefix="/api/repos",
    tags=["refactoring"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Response shapes (kept local — these surface only the refactoring layer)
# ---------------------------------------------------------------------------


class RefactoringPlanResponse(BaseModel):
    """One ranked refactoring plan, with its open ``plan`` / ``evidence`` /
    ``blast_radius`` dicts re-hydrated from the persisted ``*_json`` columns."""

    id: str
    refactoring_type: str
    file_path: str
    target_symbol: str
    line_start: int | None = None
    line_end: int | None = None
    plan: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    impact_delta: float = 0.0
    effort_bucket: str = ""
    blast_radius: dict[str, Any] = Field(default_factory=dict)
    confidence: str = "medium"
    source_biomarker: str = ""
    benefit: float = 0.0
    leverage: float = 0.0
    cost: float = 0.0
    risk: float = 0.0
    # The unified-rank score (higher = surface sooner). Carried so the tab can
    # plot/sort without recomputing the blend client-side.
    rank_score: float = 0.0
    # Two plot-ready figures, served rather than derived client-side.
    #
    # `dependents` is the file's in-degree — the same centrality the rank reads,
    # so every type reports it the same way. The blast-radius dict was the only
    # other source and it carries the count under `file_count`, `dependents_count`
    # or `callers` depending on which detector wrote it, which is how one file
    # ended up reporting two different dependent counts from two of its plans.
    #
    # Both default to 0 rather than being optional: a repo with no graph metrics
    # or no health pass yet is a real state, and 0 reads as "not measured" in the
    # same place a missing field would have.
    dependents: int = 0
    file_nloc: int = 0
    file_weighted_deficit: int = 0
    validation: dict[str, Any] = Field(default_factory=dict)


class RefactoringTypeCount(BaseModel):
    type: str
    count: int


class RefactoringSummary(BaseModel):
    total: int
    by_type: list[RefactoringTypeCount]
    files_total: int | None = None
    structural_total: int | None = None
    design_total: int | None = None
    performance_total: int | None = None
    small_effort_total: int | None = None
    health_recovery_total: int | None = None
    negligible_health_total: int | None = None
    best_health_gain: float | None = None


class RefactoringTargetsResponse(BaseModel):
    summary: RefactoringSummary
    plans: list[RefactoringPlanResponse]


class RefactoringPlanPageResponse(BaseModel):
    """Bounded product page; the legacy targets response remains unpaged."""

    items: list[RefactoringPlanResponse]
    total: int
    has_more: bool
    next_offset: int | None
    summary: RefactoringSummary
    structural_leads: list[RefactoringPlanResponse]


# ---------------------------------------------------------------------------
# Row → dataclass → response adapters
# ---------------------------------------------------------------------------


def _to_response(data: dict[str, Any]) -> RefactoringPlanResponse:
    return RefactoringPlanResponse(**data)


def _csv_values(value: str | None) -> frozenset[str]:
    return frozenset(part.strip() for part in (value or "").split(",") if part.strip())


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


@router.get("/{repo_id}/refactoring/targets/page", response_model=RefactoringPlanPageResponse)
async def get_refactoring_plan_page(
    repo_id: str,
    refactoring_type: str | None = Query(None),
    min_confidence: str | None = Query(None, description="Compatibility confidence floor"),
    confidence: str | None = Query(None, description="Comma-separated exact confidence values"),
    effort: str | None = Query(None, description="Comma-separated effort buckets"),
    file_path: str | None = Query(None),
    search: str | None = Query(None, max_length=200),
    sort: Literal["canonical", "health", "effort", "blast", "file"] = Query("canonical"),
    view: Literal["canonical", "file_spread"] = Query("canonical"),
    limit: int = Query(60, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> RefactoringPlanPageResponse:
    """Bounded list with server-owned filters and deterministic ordering.

    Reads the rank, factors and validation the finalizer stored: filters,
    order and paging are SQL, so the cost follows the page, not the plan count.
    """
    body = await _service(session, repo_id).ranked_plan_page(
        PlanListQuery(
            refactoring_type=refactoring_type,
            min_confidence=min_confidence,
            confidences=_csv_values(confidence),
            efforts=_csv_values(effort),
            file_path=file_path,
            search=(search or "").strip().lower(),
            sort=sort,
            view=view,
            limit=limit,
            offset=offset,
        )
    )
    return RefactoringPlanPageResponse(**body)


# ---------------------------------------------------------------------------
# Code-gen settings — read/write the refactoring.llm config block. Declared
# before the dynamic /{suggestion_id} GET so the static `settings` path wins.
# ---------------------------------------------------------------------------


class RefactoringSettings(BaseModel):
    """The opt-in code-generation switches, mirrored from ``refactoring.llm``."""

    enabled: bool = False
    provider: str | None = None
    model: str | None = None


def _read_refactoring_settings(config: dict[str, Any]) -> RefactoringSettings:
    """Project ``refactoring.llm`` out of a loaded config, tolerant of shape.

    ``enabled`` defaults to ``True`` when unset, matching
    :func:`llm_enrichment_enabled` — an untouched repo shows the toggle on.
    """
    refactoring = config.get("refactoring")
    llm = refactoring.get("llm") if isinstance(refactoring, dict) else None
    if not isinstance(llm, dict):
        return RefactoringSettings(enabled=True)
    provider = llm.get("provider")
    model = llm.get("model")
    return RefactoringSettings(
        enabled=bool(llm.get("enabled", True)),
        provider=provider if isinstance(provider, str) and provider else None,
        model=model if isinstance(model, str) and model else None,
    )


async def _local_repo_path(session: AsyncSession, repo_id: str) -> Path:
    """The repo's on-disk checkout, or a 404 — code-gen settings are a
    local-``serve`` capability (they live in the repo's ``.repowise``)."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None or not repo.local_path:
        raise HTTPException(status_code=404, detail=f"repository not found: {repo_id}")
    repo_path = Path(repo.local_path)
    if not repo_path.exists():
        raise HTTPException(
            status_code=404, detail="repository checkout not accessible on this server"
        )
    return repo_path


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
    }
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


class RefactoringOpportunityStatusUpdate(BaseModel):
    """The finding-triage vocabulary, applied to a whole opportunity."""

    status: str = Field(..., description="open | acknowledged | resolved | false_positive")


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


@router.get("/{repo_id}/refactoring/settings", response_model=RefactoringSettings)
async def get_refactoring_settings(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> RefactoringSettings:
    """Current ``refactoring.llm`` settings for the repo (enabled + provider/model)."""
    from repowise.core.repo_config import load_repo_config

    repo_path = await _local_repo_path(session, repo_id)
    return _read_refactoring_settings(load_repo_config(repo_path))


@router.put("/{repo_id}/refactoring/settings", response_model=RefactoringSettings)
async def update_refactoring_settings(
    repo_id: str,
    body: RefactoringSettings,
    session: AsyncSession = Depends(get_db_session),
) -> RefactoringSettings:
    """Persist the ``refactoring.llm`` block to the repo's ``.repowise/config.yaml``.

    Round-trips through the loaded config so unrelated keys are preserved, then
    writes only the ``refactoring.llm.{enabled,provider,model}`` sub-tree. A
    blank provider/model clears that key rather than writing an empty string.
    """
    from repowise.core.repo_config import load_repo_config, save_repo_config

    repo_path = await _local_repo_path(session, repo_id)
    config = load_repo_config(repo_path)

    refactoring = config.get("refactoring")
    if not isinstance(refactoring, dict):
        refactoring = {}
        config["refactoring"] = refactoring
    llm = refactoring.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        refactoring["llm"] = llm

    llm["enabled"] = bool(body.enabled)
    provider = (body.provider or "").strip()
    model = (body.model or "").strip()
    if provider:
        llm["provider"] = provider
    else:
        llm.pop("provider", None)
    if model:
        llm["model"] = model
    else:
        llm.pop("model", None)

    save_repo_config(repo_path, config)
    return _read_refactoring_settings(config)


@router.get("/{repo_id}/refactoring/{suggestion_id}", response_model=RefactoringPlanResponse)
async def get_refactoring_plan(
    repo_id: str,
    suggestion_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> RefactoringPlanResponse:
    """One plan + its blast radius detail (deep-link / drill-down target)."""
    row = await crud.get_refactoring_suggestion(session, repo_id, suggestion_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"refactoring plan not found: {suggestion_id}")
    recommendation = await _service(session, repo_id).plan_recommendation(row)
    return _to_response(recommendation.detail_dict())


class RefactoringStatusUpdate(BaseModel):
    """Same shape and vocabulary as health finding triage — one triage system."""

    status: str = Field(..., description="open | acknowledged | resolved | false_positive")


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


class GenerateCodeRequest(BaseModel):
    """Optional per-call overrides for the enrichment provider/model."""

    provider: str | None = None
    model: str | None = None


class GenerateCodeResponse(BaseModel):
    """Generated refactored code + diff for one plan, with the self-check."""

    suggestion_id: str | None = None
    refactoring_type: str
    file_path: str
    target_symbol: str
    content: str
    diff: str
    provider: str
    model: str
    cached: bool
    input_tokens: int
    output_tokens: int
    validation: dict[str, Any] = Field(default_factory=dict)
    spans: list[dict[str, Any]] = Field(default_factory=list)


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

    Strictly opt-in: returns 403 unless ``refactoring.llm.enabled`` is set in the
    repo's ``.repowise/config.yaml``. Needs the working tree on disk (it reads
    the plan's real source spans), so this is a local-``serve`` capability, not a
    hosted one — it returns 404 when the repo has no accessible checkout.
    """
    from repowise.core.analysis.health.refactoring.llm import (
        build_enrichment_provider,
        enrich_suggestion,
        llm_enrichment_enabled,
    )
    from repowise.core.repo_config import load_repo_config

    repo = await crud.get_repository(session, repo_id)
    if repo is None or not repo.local_path:
        raise HTTPException(status_code=404, detail=f"repository not found: {repo_id}")
    repo_path = Path(repo.local_path)
    if not repo_path.exists():
        raise HTTPException(
            status_code=404,
            detail="repository checkout not accessible on this server",
        )

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

    body = body or GenerateCodeRequest()
    try:
        provider = build_enrichment_provider(
            repo_path, provider_name=body.provider, model=body.model
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await enrich_suggestion(sug, provider=provider, repo_path=repo_path)
    return GenerateCodeResponse(**result.to_dict())
