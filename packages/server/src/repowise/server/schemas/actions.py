"""Wire shapes for next actions. Mirrors ``packages/types/src/actions.ts``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from repowise.core.analysis.next_call import ActionCommand
from repowise.server.schemas._datetime import UTCDateTime


class ActionWhy(BaseModel):
    label: str
    value: str
    basis: Literal["measured", "inferred", "unknown"]


class ActionTarget(BaseModel):
    kind: Literal["file", "symbol", "folder", "document", "decision", "repo"]
    path: str
    symbol: str | None = None


class ActionDetail(BaseModel):
    path: str
    line: int | None = None
    symbol: str | None = None
    marker: str | None = None
    severity: str | None = None
    reason: str = ""
    ref: str | None = None


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
    value: int = Field(
        default=0, description="0-4: the Fix first value band, or what the severity says."
    )
    priority: float = Field(
        default=0.0, description="Value x confidence / effort: the order inside a tier."
    )
    done_when: str
    command: str | None = None
    marker: str | None = None
    evidence_ids: list[str]
    evidence_total: int
    includes: list[str]
    fingerprint: str
    details: list[ActionDetail] = Field(default_factory=list)
    details_total: int = 0
    commands: list[ActionCommand] = Field(default_factory=list)


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
    history_too_short: bool = Field(
        False,
        description="The index holds too few commits for history-driven rules to rank by.",
    )


class SetupUnlocks(BaseModel):
    """Depth rows usage has unlocked: Claude Code calls for the first two,
    synced commits for the last. Never a calendar."""

    always_load: bool
    distill: bool
    suggested_settings: bool


class ActionsSetup(BaseModel):
    """Setup steps left, and whether the finish-setup agent pass is offered."""

    open: int = Field(description="Visible improve_signal actions: what the pass works through.")
    offer: Literal["finish_setup"] | None = Field(
        description="Set from the first agent call while steps are left and the pass is not dismissed."
    )
    dismissed: bool
    completed: bool = Field(
        False,
        description="Steps reached zero once; the pass is never offered again for this repo.",
    )
    agent_calls: int | None = Field(description="Calls to this index from every agent.")
    claude_code_calls: int = 0
    first_call_at: str | None = None
    synced_commits: int | None = Field(
        description="Indexed commits made after the index was first built."
    )
    unlocks: SetupUnlocks


class ActionsResponse(BaseModel):
    status: Literal["available"]
    anchor: str | None = Field(description="Newest indexed commit time; windows count back from it.")
    week_start: str | None
    context: ActionContext
    horizons: dict[Literal["week", "quarter"], ActionHorizon]
    summary: dict[Literal["week", "quarter"], str] = Field(
        description="Where things stand in each horizon, in one sentence."
    )
    rules: list[ActionRuleStatus]
    unavailable: dict[str, str]
    #: Absent where the setup facts were not read (a workspace member's lead rows).
    setup: ActionsSetup | None = None


class ActionStateRequest(BaseModel):
    state: Literal["dismissed", "snoozed", "done"] | None = Field(
        description="None clears the person's answer."
    )
    fingerprint: str = Field("", max_length=32)
    snooze_days: int = Field(14, ge=1, le=365)


class ActionStateResponse(BaseModel):
    action_id: str
    state: Literal["dismissed", "snoozed", "done"] | None
    until: UTCDateTime | None = None


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


class WorkspaceSetupRepo(BaseModel):
    """One member's setup, as the workspace Setup table shows it."""

    alias: str
    #: The member's directory, relative to the workspace root.
    path: str
    repo_id: str | None
    status: Literal["available", "not_indexed", "unavailable"]
    reason: str = ""
    index_state: Literal["up_to_date", "behind", "incomplete"] | None = None
    #: Calls to this member's index from every agent; the agent essential is
    #: met once one has landed.
    agent_calls: int | None = None
    first_call_at: str | None = None
    #: The post-commit sync hook; ``None`` when it could not be read.
    sync: bool | None = None
    #: ``on`` = stored, ``found`` = a file on disk not yet read, ``none``.
    coverage: Literal["on", "found", "none"] | None = None
    evidence: Literal["on", "found", "none"] | None = None
    #: Setup steps left: the member's visible improve_signal actions.
    open: int = 0
    offer: Literal["finish_setup"] | None = None
    #: Index current, an agent call landed, and sync on.
    essentials_done: bool = False


class WorkspaceSetupResponse(BaseModel):
    repos: list[WorkspaceSetupRepo]
    #: Members with every essential done, of ``len(repos)``.
    essentials_done: int
    #: Steps left across members.
    open: int
    #: Set while any member offers the pass.
    offer: Literal["finish_setup"] | None = None
    #: The workspace form of the finish-setup prompt, naming the members with
    #: steps left; ``None`` when none has.
    prompt: str | None = None
