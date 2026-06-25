"""/api/repos/{repo_id}/refactoring — deterministic refactoring plans.

The refactoring layer writes one structured ``RefactoringSuggestion`` row per
opportunity (Extract Class, Extract Helper, Move Method, Break Cycle). These
endpoints read those rows from SQL and re-apply the unified rank so the order
the web tab shows matches every other surface (CLI / MCP): a plan on a central
hub file outranks the same plan on a leaf, blast radius amplifies, confidence
breaks ties.

No on-disk work happens here, so this works on hosted backends without a
checkout — the same property the C4 endpoints rely on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.rank import rank_suggestions, score


def blast_files(sug: RefactoringSuggestion) -> list[str]:
    """The other files a plan drags along, from whichever blast-radius shape it
    carries — used to scope the detail endpoint's centrality lookup."""
    files = (sug.blast_radius or {}).get("files")
    return [f for f in files if isinstance(f, str)] if isinstance(files, list) else []


from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key

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
    # The unified-rank score (higher = surface sooner). Carried so the tab can
    # plot/sort without recomputing the blend client-side.
    rank_score: float = 0.0


class RefactoringTypeCount(BaseModel):
    type: str
    count: int


class RefactoringSummary(BaseModel):
    total: int
    by_type: list[RefactoringTypeCount]


class RefactoringTargetsResponse(BaseModel):
    summary: RefactoringSummary
    plans: list[RefactoringPlanResponse]


# ---------------------------------------------------------------------------
# Row → dataclass → response adapters
# ---------------------------------------------------------------------------


def _row_to_suggestion(row: Any) -> RefactoringSuggestion:
    """Re-hydrate a persisted ORM row into the ranking dataclass.

    The rank module operates on ``RefactoringSuggestion`` dataclasses (open
    ``plan`` / ``blast_radius`` dicts); the DB stores those as ``*_json`` text.
    We stash the row id on the instance so the ranked order can be serialized
    back with its id intact (dataclass instances accept extra attributes)."""

    def _loads(value: Any) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    sug = RefactoringSuggestion(
        refactoring_type=row.refactoring_type,
        file_path=row.file_path,
        target_symbol=row.target_symbol,
        line_start=row.line_start,
        line_end=row.line_end,
        plan=_loads(row.plan_json),
        evidence=_loads(row.evidence_json),
        impact_delta=row.impact_delta,
        effort_bucket=row.effort_bucket,
        blast_radius=_loads(row.blast_radius_json),
        confidence=row.confidence,
        source_biomarker=row.source_biomarker,
    )
    sug.id = row.id  # type: ignore[attr-defined]
    return sug


def _to_response(
    sug: RefactoringSuggestion, centrality: dict[str, float]
) -> RefactoringPlanResponse:
    return RefactoringPlanResponse(
        id=getattr(sug, "id", ""),
        refactoring_type=sug.refactoring_type,
        file_path=sug.file_path,
        target_symbol=sug.target_symbol,
        line_start=sug.line_start,
        line_end=sug.line_end,
        plan=sug.plan or {},
        evidence=sug.evidence or {},
        impact_delta=sug.impact_delta,
        effort_bucket=sug.effort_bucket,
        blast_radius=sug.blast_radius or {},
        confidence=sug.confidence,
        source_biomarker=sug.source_biomarker,
        rank_score=round(score(sug, centrality), 4),
    )


async def _centrality_map(session: AsyncSession, repo_id: str) -> dict[str, float]:
    """File-path → in-degree (importer count), the cheap dependency-centrality
    proxy the unified rank reads. Empty when no graph metrics are materialized
    (the rank then degrades to impact × blast × confidence)."""
    metrics = await crud.get_graph_metrics(session, repo_id)
    return {node_id: float(m.get("in_degree") or 0) for node_id, m in metrics.items()}


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
    session: AsyncSession = Depends(get_db_session),
) -> RefactoringTargetsResponse:
    """Ranked refactoring plans for the repo, filterable by type and confidence.

    The summary ignores the *type* filter (so the per-type chips always show
    every type's total, even while one type is selected) but does honor
    *min_confidence* — so the summary and the plan list stay consistent under a
    confidence filter.
    """
    centrality = await _centrality_map(session, repo_id)

    # Summary is computed over the unfiltered-by-type set so the chips can show
    # every type's count even while one type is selected.
    all_rows = await crud.get_refactoring_suggestions(
        session, repo_id, min_confidence=min_confidence
    )
    by_type: dict[str, int] = {}
    for row in all_rows:
        by_type[row.refactoring_type] = by_type.get(row.refactoring_type, 0) + 1
    summary = RefactoringSummary(
        total=len(all_rows),
        by_type=[
            RefactoringTypeCount(type=t, count=c)
            for t, c in sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    )

    rows = (
        all_rows
        if refactoring_type is None
        else [r for r in all_rows if r.refactoring_type == refactoring_type]
    )
    suggestions = [_row_to_suggestion(r) for r in rows]
    ranked = rank_suggestions(suggestions, centrality=centrality)
    return RefactoringTargetsResponse(
        summary=summary,
        plans=[_to_response(s, centrality) for s in ranked],
    )


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
    sug = _row_to_suggestion(row)
    # Only this plan's file and its blast files affect its score / caller rollup,
    # so fetch in-degree for just those rather than the whole graph_metrics table.
    files = {sug.file_path, *(f for f in blast_files(sug) if isinstance(f, str))}
    centrality: dict[str, float] = {}
    for path in files:
        degrees = await crud.get_node_degree_counts(session, repo_id, path)
        centrality[path] = float(degrees.get("in_degree") or 0)
    # Enrich the blast radius the same way the ranking does, so the detail view
    # carries the caller rollup the list ranked on.
    rank_suggestions([sug], centrality=centrality)
    return _to_response(sug, centrality)


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
    sug = _row_to_suggestion(row)

    body = body or GenerateCodeRequest()
    try:
        provider = build_enrichment_provider(
            repo_path, provider_name=body.provider, model=body.model
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await enrich_suggestion(sug, provider=provider, repo_path=repo_path)
    return GenerateCodeResponse(**result.to_dict())
