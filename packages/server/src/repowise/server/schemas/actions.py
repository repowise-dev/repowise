"""Wire shapes for next actions. Mirrors ``packages/types/src/actions.ts``."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ActionWhy(BaseModel):
    label: str
    value: str
    basis: Literal["measured", "inferred", "unknown"]


class ActionTarget(BaseModel):
    kind: Literal["file", "symbol", "folder", "document", "decision", "repo"]
    path: str
    symbol: str | None = None


class NextAction(BaseModel):
    id: str
    rule: str
    tier: Literal["act_now", "plan", "improve_signal"]
    horizons: list[Literal["week", "quarter"]]
    severity: Literal["critical", "high", "medium", "low"]
    title: str = Field(description="Verb first; paths and symbols wrapped in backticks.")
    impact: str
    why: list[ActionWhy]
    target: ActionTarget
    surface: str
    effort: Literal["S", "M", "L"]
    confidence: Literal["high", "medium"]
    done_when: str
    command: str | None = None
    marker: str | None = None
    evidence_ids: list[str]
    evidence_total: int
    includes: list[str]
    fingerprint: str


class ActionHorizon(BaseModel):
    actions: list[NextAction]
    total: int = Field(description="Every visible action in this horizon, not only those listed.")
    hidden: int = Field(description="Actions the person dismissed, snoozed or marked done.")
    by_tier: dict[str, int]


class ActionRuleStatus(BaseModel):
    rule: str
    status: Literal["evaluated", "not_applicable", "unavailable"]
    reason: str
    emitted: int


class ActionContext(BaseModel):
    production_files: int
    active_authors_90d: int
    fix_commits_90d: int
    busy_threshold: int
    coverage: Literal["measured", "stale", "unknown"]


class ActionsResponse(BaseModel):
    status: Literal["available"]
    anchor: str | None = Field(description="Newest indexed commit time; windows count back from it.")
    week_start: str | None
    context: ActionContext
    horizons: dict[Literal["week", "quarter"], ActionHorizon]
    rules: list[ActionRuleStatus]
    unavailable: dict[str, str]


class ActionStateRequest(BaseModel):
    state: Literal["dismissed", "snoozed", "done"] | None = Field(
        description="None clears the person's answer."
    )
    fingerprint: str = Field("", max_length=32)
    snooze_days: int = Field(14, ge=1, le=365)


class ActionStateResponse(BaseModel):
    action_id: str
    state: Literal["dismissed", "snoozed", "done"] | None
    until: datetime | None = None


class WorkspaceRepoActions(BaseModel):
    alias: str
    repo_id: str | None
    status: Literal["available", "unavailable"]
    reason: str = ""
    #: The strongest work per horizon (act now and plan tiers only), with totals.
    horizons: dict[Literal["week", "quarter"], ActionHorizon] = Field(default_factory=dict)


class WorkspaceCrossRepoAction(BaseModel):
    kind: Literal["breaking_contract"]
    title: str
    impact: str
    count: int
    repos: list[str]


class WorkspaceActionsResponse(BaseModel):
    repos: list[WorkspaceRepoActions]
    cross_repo: list[WorkspaceCrossRepoAction]
