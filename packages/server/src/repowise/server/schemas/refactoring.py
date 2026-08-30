"""Refactoring response models.

The composed opportunity and the stored rollup are assembled elsewhere — the
opportunity in ``core.analysis.health.refactoring``, the rollup as persisted
JSON — so those payloads stay open here. Closing them would silently drop
whichever keys this module had not been told about, which is the failure these
schemas exist to prevent. The envelopes around them are pinned.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


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

    resolved: bool
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
