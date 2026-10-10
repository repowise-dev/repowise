"""Refactoring response models.

The composed opportunity and the stored rollup are assembled elsewhere — the
opportunity in ``core.analysis.health.refactoring``, the rollup as persisted
JSON — so those payloads stay open here. Closing them would silently drop
whichever keys this module had not been told about, which is the failure these
schemas exist to prevent. The envelopes around them are pinned.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from repowise.core.analysis.health.queue.counts import QueueCounts


class RefactoringHiddenCounts(BaseModel):
    """What the ``fix_first`` scope leaves out of a page's filtered set."""

    total: int = 0
    #: Count per Fix-first exclusion reason, largest first.
    by_reason: dict[str, int] = {}


class RefactoringOpportunitiesResponse(BaseModel):
    """One page of composed opportunities, with facets and the rollup."""

    #: Composed in core; see the module docstring.
    items: list[dict[str, Any]] = []
    total: int = 0
    offset: int = 0
    has_more: bool = False
    #: ``None`` on the last page.
    next_offset: int | None = None
    #: Counts per facet value, scoped to the status being listed.
    facets: dict[str, dict[str, int]] = {}
    summary: dict[str, Any] | None = None
    #: Present only when the request carried arguments the query ignored.
    ignored_arguments: dict[str, str] | None = None
    #: ``fix_first``: only what Fix first would take; ``all``: the inventory.
    scope: Literal["fix_first", "all"] = "all"
    #: Under ``fix_first``, the rest of the filtered set, by reason.
    hidden: RefactoringHiddenCounts | None = None
    #: The plans' count vocabulary over the open inventory (``queue.counts``).
    counts: QueueCounts | None = None


class RefactoringRollupResponse(BaseModel):
    """The repository rollup and its one lead."""

    summary: dict[str, Any] = {}
    directive: dict[str, Any] = {}


class RefactoringOpportunityStatusResponse(BaseModel):
    """What one opportunity-level triage decision wrote."""

    opportunity_id: str
    status: str
    #: Member plans the transition reached; the route 409s when it is zero.
    steps_updated: int
    status_changed_at: str | None = None


class RefactoringPlanStatusResponse(BaseModel):
    """What one plan-level triage decision wrote."""

    id: str
    public_id: str | None = None
    status: str
    status_reason: str | None = None
    status_changed_at: str | None = None


class RefactoringOpportunityDetailResponse(BaseModel):
    """One opportunity: its ordered steps, evidence, validation and plans.

    ``extra="allow"``: the base row is composed in core and this response
    spreads an evidence block over it, so the declared keys are the stable
    part and anything else passes through rather than being dropped.
    """

    model_config = ConfigDict(extra="allow")

    #: Whether the id named a stored opportunity. Lifecycle is ``status``.
    found: bool
    steps: list[dict[str, Any]] = []
    steps_total: int = 0
    steps_emitted: int = 0
    #: Present only when the page stopped short of the full step list.
    steps_reduced_reason: str | None = None
    steps_next_cursor: int | None = None
    validation_profiles: list[dict[str, Any]] = []
    affected_files: list[str] = []
    lead_finding_ids: list[str] = []
    next_actions: list[dict[str, Any]] = []
    #: Present when steps were returned and plans were requested.
    plans: list[dict[str, Any]] | None = None
    #: Present when a step names a symbol an earlier step moves.
    ordering_note: str | None = None


# Plan-level shapes: the plan lists, one plan, triage bodies and code generation.


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


class PlanRiskResponse(BaseModel):
    kind: str
    text: str
    ref: str | None = None


class RefactoringPlanDetailResponse(RefactoringPlanResponse):
    """One plan read alone: what other layers say about its target. Lists stay
    on :class:`RefactoringPlanResponse` and never carry these; both are absent
    on a plan that was never checked."""

    governed_by: list[str] | None = None
    risks: list[PlanRiskResponse] | None = None
    # Only with ``include=recipe``: the plan as preconditions, steps and
    # postconditions an agent applies (``refactoring.recipe``).
    recipe: dict[str, Any] | None = None


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


class RefactoringSettings(BaseModel):
    """The code-generation switch plus the model it will use.

    ``provider`` / ``model`` are read-only: they come from the same resolver
    chat uses, so the user configures a model once. Never carries a key.
    """

    enabled: bool = False
    provider: str | None = None
    model: str | None = None


class RefactoringSettingsUpdate(BaseModel):
    """The one writable field, ``refactoring.llm.enabled``."""

    enabled: bool


class RefactoringOpportunityStatusUpdate(BaseModel):
    """The finding-triage vocabulary, applied to a whole opportunity."""

    status: str = Field(..., description="open | acknowledged | resolved | false_positive")


class RefactoringStatusUpdate(BaseModel):
    """Same shape and vocabulary as health finding triage — one triage system."""

    status: str = Field(..., description="open | acknowledged | resolved | false_positive")


class GenerateCodeRequest(BaseModel):
    """Optional per-call provider/model overrides, as chat accepts."""

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
