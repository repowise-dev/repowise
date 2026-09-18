"""Small request/response helper models (webhooks, provider config, cost)."""

from __future__ import annotations

from pydantic import BaseModel


class WebhookResponse(BaseModel):
    event_id: str
    status: str = "accepted"


class OkResponse(BaseModel):
    """Acknowledgement for a mutation with nothing else to report."""

    ok: bool = True


class SetActiveProviderRequest(BaseModel):
    provider: str
    model: str | None = None
    # When set, the selection is scoped to this repo (so picking a model for
    # one repo never shadows another in a workspace). Omit for the global default.
    repo_id: str | None = None


class SetApiKeyRequest(BaseModel):
    api_key: str
    # When set, the key is also mirrored into this repo's ``.repowise/.env`` so
    # a later CLI run in the repo picks it up. Omit for a server-global-only key.
    repo_id: str | None = None


class ProviderEntry(BaseModel):
    """One provider in the catalog, as the settings picker renders it."""

    id: str
    name: str
    #: Catalog models, plus the active model when it is configured but
    #: uncataloged (a local LiteLLM alias), so the picker can display it.
    models: list[str] = []
    default_model: str | None = None
    #: Has a key, or needs none.
    configured: bool = False


class ActiveProviderSelection(BaseModel):
    """The provider/model this scope resolves to; ``None`` when unset."""

    provider: str | None = None
    model: str | None = None


class ProviderStatusResponse(BaseModel):
    active: ActiveProviderSelection
    providers: list[ProviderEntry] = []


class ProviderValidationResponse(BaseModel):
    """Outcome of the live single-provider smoke test.

    A failed probe reports ``ok=False`` with ``error`` set rather than raising,
    so the settings UI renders a clean error state.
    """

    ok: bool
    provider: str | None = None
    model: str | None = None
    error: str | None = None


class CostGroupResponse(BaseModel):
    group: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class CostSummaryResponse(BaseModel):
    total_cost_usd: float
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    since: str | None


class SavingsBreakdownRow(BaseModel):
    """One bucket of a savings breakdown.

    ``group`` is nullable because one bucket genuinely has no name: an event
    written before its rate could be resolved has no model, and that is a real
    quantity -- how much saving carries no pricing evidence -- rather than a
    gap to drop from the list.
    """

    group: str | None = None
    events: int = 0
    saved_input_tokens: int = 0


class SavingsAgentRow(BaseModel):
    """One agent's savings, labelled from the identity registry."""

    agent: str
    agent_display_name: str | None = None
    events: int = 0
    saved_input_tokens: int = 0


class SavingsOpportunityRow(BaseModel):
    """One kind of observed opportunity. Never part of achieved savings."""

    kind: str
    observations: int = 0
    estimated_potential_input_tokens: int = 0


class SavingsResponse(BaseModel):
    """What agents avoided in one repository over one window.

    Every figure comes from the canonical savings ledger through the one core
    report service, so this response, the repository overview headline and
    ``repowise saved`` cannot disagree -- which the three of them did, in four
    separate ways, when each aggregated and priced the ledger for itself.

    Three things it deliberately keeps apart. *Measured* reductions are known
    before/after sizes from an operation that really ran; *inferred* avoidance
    is a documented counterfactual. *Priced* and *unpriced* tokens are split
    because an event records the rate it was worth at the time and some events
    carry none, and reporting a total as though it were all priced would be a
    guess. *Observed opportunities* are things that could have been saved and
    were not: they never enter the headline.

    ``available`` is False when the repository has no savings sidecar at all,
    which means "nothing has been measured here" and is different from a
    measured zero.
    """

    available: bool
    #: Window the figures cover; null means all time.
    window_days: int | None = None
    as_of: str = ""
    first_event_at: str | None = None
    last_event_at: str | None = None

    unique_events: int = 0
    successful_or_usable_partial_events: int = 0
    saving_interactions: int = 0
    mcp_queries_answered: int = 0
    dead_ends: int = 0

    saved_input_tokens: int = 0
    measured_saved_input_tokens: int = 0
    inferred_saved_input_tokens: int = 0
    priced_saved_input_tokens: int = 0
    unpriced_saved_input_tokens: int = 0
    priced_input_savings_usd: float = 0.0
    saved_output_tokens: int | None = None
    priced_saved_output_tokens: int = 0
    unpriced_saved_output_tokens: int = 0
    priced_output_savings_usd: float = 0.0

    per_operation: list[SavingsBreakdownRow] = []
    per_surface: list[SavingsBreakdownRow] = []
    per_agent: list[SavingsAgentRow] = []
    per_model: list[SavingsBreakdownRow] = []
    per_day: list[SavingsBreakdownRow] = []

    #: Recorded opportunities (a hook replacement that was declined, say).
    opportunity_count: int = 0
    opportunity_tokens_excluded: int = 0
    per_opportunity_kind: list[SavingsOpportunityRow] = []
    #: Transcript-mined opportunities: raw commands a filter would have caught,
    #: and full re-reads of unchanged files. Estimates of what was *not* saved.
    missed_events: int = 0
    missed_tokens_est: int = 0
    missed_window_days: float = 0.0
    reread_events: int = 0
    reread_tokens_est: int = 0
