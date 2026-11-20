"""Small request/response helper models (webhooks, provider config, cost)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class WebhookResponse(BaseModel):
    event_id: str
    status: str = "accepted"


class OkResponse(BaseModel):
    """Acknowledgement for a mutation with nothing else to report."""

    ok: bool = True


class SetActiveProviderRequest(BaseModel):
    provider: str
    #: Any model id the provider accepts, not only a catalog model.
    model: str | None = None
    #: An ``http(s)://`` endpoint (an OpenAI-compatible server such as LM
    #: Studio). Omit to keep the current one; ``""`` or ``null`` clears it.
    base_url: str | None = None
    # When set, the selection is written to this repo's .repowise/config.yaml
    # (so picking a model for one repo never shadows another in a workspace).
    # Omit for the global default.
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
    #: Takes an API key; ``False`` hides the key field.
    requires_key: bool = True
    #: Takes a custom endpoint (an OpenAI-compatible local server, a proxy).
    accepts_base_url: bool = False
    #: Can point at a local OpenAI-compatible server (LM Studio) with no key.
    local_server: bool = False
    #: A model that runs on this machine (not an agent CLI).
    local_model: bool = False
    #: Env vars a user sets to make the provider usable.
    env_vars: list[str] = []
    #: One line on how to install or set it up.
    setup_hint: str = ""
    #: What on this machine makes it usable: ``signed_in`` (a signed-in agent
    #: CLI, reported only for ``?detect=true``), ``env`` (a key in the process
    #: env or the repo's ``.repowise/.env``) or ``saved`` (a key stored here).
    #: ``installed``: an agent CLI is on PATH but its sign-in cannot be told
    #: (no login query, or ``REPOWISE_OFFLINE``/``DO_NOT_TRACK`` is set).
    detected: Literal["signed_in", "installed", "env", "saved"] | None = None


class EmbedderEntry(BaseModel):
    """One registered embedder, as the settings picker renders it."""

    id: str
    #: Env vars a user sets to make the embedder usable.
    env_vars: list[str] = []
    #: ``False`` for the keyless embedder, whose vectors carry no signal.
    semantic: bool = True


class SelectionProvenance(BaseModel):
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None


class ActiveProviderSelection(BaseModel):
    """The provider/model this scope resolves to; ``None`` when unset."""

    provider: str | None = None
    model: str | None = None
    #: The endpoint requests go to; ``None`` means the provider's own default.
    base_url: str | None = None
    #: Where each value came from, per field: ``request``, ``config.yaml``,
    #: ``global``, ``env:<VAR>``, ``default`` or ``autodetect``; ``None`` if unset.
    provenance: SelectionProvenance = SelectionProvenance()
    #: The repo's config.yaml base URL is refused until the user confirms it
    #: here, so a cloned repo cannot send stored keys to its own host.
    base_url_unconfirmed: bool = False
    #: USD per 1K tokens for ``model``; ``None`` when no model resolves.
    input_cost_per_1k: float | None = None
    output_cost_per_1k: float | None = None
    #: The embedder this server runs with.
    embedder: str | None = None


class PlanRoute(BaseModel):
    """A signed-in agent CLI on a paid plan: a model with no new spend."""

    #: The provider id that runs on it.
    provider: str
    #: What the user calls the CLI, e.g. ``Claude Code``.
    cli_name: str
    #: About how long ``repowise generate`` takes on it; ``None`` when uncounted.
    minutes: int | None = None


class ProviderStatusResponse(BaseModel):
    active: ActiveProviderSelection
    providers: list[ProviderEntry] = []
    #: Registerable providers the catalog leaves out (picker_rank ``None``).
    flag_only_providers: list[str] = []
    embedders: list[EmbedderEntry] = []
    #: With ``?detect=true`` and a repo: the plan-billed sign-in the repo can
    #: add a model with, as the keyless upgrade line names it. ``None`` when a
    #: provider already resolves or no signed-in CLI reports a paid plan.
    plan_route: PlanRoute | None = None


class ProviderModelEntry(BaseModel):
    id: str
    label: str | None = None


class ProviderModelsResponse(BaseModel):
    """Models a provider offers, newest-looking first after the default.

    ``source`` is ``live`` when the provider listed them and ``catalog`` when
    the built-in list stands in, with ``error`` saying why.
    """

    models: list[ProviderModelEntry]
    source: Literal["live", "catalog"]
    default_model: str
    error: str | None = None


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
    # Calls priced at $0 and why: an agent CLI on the user's plan, or a model
    # on their own machine. Lets a page say so instead of printing $0.00.
    subscription_calls: int = 0
    local_calls: int = 0


class CostSummaryResponse(BaseModel):
    total_cost_usd: float
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    since: str | None
    subscription_calls: int = 0
    local_calls: int = 0


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
    # Input savings valued at each event's own rate; unpriced events add 0.
    priced_input_savings_usd: float = 0.0


class SavingsAgentRow(BaseModel):
    """One agent's savings, labelled from the identity registry."""

    agent: str
    agent_display_name: str | None = None
    events: int = 0
    saved_input_tokens: int = 0


class SavingsFilesRow(BaseModel):
    """Files not opened and tool calls answered in one day, agent or surface."""

    group: str | None = None
    files_not_opened: int = 0
    files_not_opened_tokens: int = 0
    tool_calls_answered: int = 0


class SavingsCheckRow(BaseModel):
    """One check tool: the calls agents made and what Repowise returned.

    Outcome fields are null when no call in the window recorded one (calls made
    before outcomes were kept, or a host that records none) and where a field
    does not apply to the tool. ``high``/``medium``/``low`` count changes by
    their repo-relative level for ``get_change_risk`` and assessed files for
    ``get_risk``. Counts of what was returned, never of what an agent did.
    """

    tool: str
    calls: int = 0
    #: Calls that recorded an outcome; the sums below cover these calls only.
    outcome_calls: int = 0
    high: int | None = None
    medium: int | None = None
    low: int | None = None
    #: ``get_why``: replies that served an accepted decision, only candidate
    #: decisions, or none.
    decisions_accepted: int | None = None
    decisions_candidate: int | None = None
    decisions_none: int | None = None
    #: ``get_health``: findings reported and files judged, summed over calls.
    findings: int | None = None
    files: int | None = None
    #: ``get_dead_code``: matching items, and the high-confidence share of them.
    items: int | None = None
    high_confidence: int | None = None


class SavingsOpportunityRow(BaseModel):
    """One kind of observed opportunity. Never part of achieved savings."""

    kind: str
    observations: int = 0
    estimated_potential_input_tokens: int = 0


class SavingsOutputModelRow(BaseModel):
    """The output estimate for sessions on one model and output rate."""

    model: str | None = None
    #: ``measured`` (this model's benchmark row), ``anthropic_fallback`` (a
    #: Claude model with no row) or ``other_fallback`` (any other model).
    basis: str
    sessions: int = 0
    #: Questions credited across these sessions: each session's MCP calls over
    #: ``mcp_calls_per_question``, at least 1 and at most the cap.
    questions: float = 0.0
    mcp_calls_per_question: float = 0.0
    reduction: float = 0.0
    output_tokens_per_question: int = 0
    output_tokens_saved: int = 0
    output_rate_usd_per_million: float | None = None
    output_usd_saved: float | None = None


class SavingsOutputCalibration(BaseModel):
    """Where the output estimate comes from, so the figure is reproducible."""

    source: str
    version: str
    #: True: credited per question asked, not once per session.
    per_question: bool = True
    questions_per_session_cap: int = 0
    formula: str = ""
    sessions: int = 0
    questions: float = 0.0
    unpriced_output_tokens_saved: int = 0
    per_model: list[SavingsOutputModelRow] = []


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

    #: How much smaller the input got, over the events that carry a baseline.
    #: Null ratios mean no event in the window had anything to compare against.
    baseline_events: int = 0
    reducing_events: int = 0
    baseline_input_tokens: int = 0
    baseline_saved_input_tokens: int = 0
    input_reduction_ratio: float | None = None
    input_reduction_ratio_p90: float | None = None

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

    #: Files not opened: distinct (session, path) pairs a usable event served
    #: in place of a Read, once per session, dropped when the agent Read the
    #: path later in that session. Tokens are one Read of each. Tool calls
    #: answered counts usable MCP, VS Code tool and hook events.
    files_not_opened: int = 0
    files_not_opened_tokens: int = 0
    tool_calls_answered: int = 0
    files_per_day: list[SavingsFilesRow] = []
    files_per_agent: list[SavingsFilesRow] = []
    files_per_surface: list[SavingsFilesRow] = []
    #: Attribution and report-time pricing. Each is part of a total above,
    #: never added to it. ``backfilled_*`` prices unpriced tokens at the rate
    #: another event of the same session captured, else at the cached rate for
    #: the event's agent, so ``priced_input_savings_usd`` keeps meaning "rate
    #: captured when it happened".
    subagent_events: int = 0
    subagent_saved_input_tokens: int = 0
    backfilled_saved_input_tokens: int = 0
    backfilled_input_savings_usd: float = 0.0
    #: Output tokens agents did not write, *estimated* from the paired
    #: benchmark per question asked in each agent session with an MCP call
    #: (``calibration.formula``), priced at that session's captured output rate. Each model is credited the benchmark's point
    #: estimate. Separate from the recorded ``saved_output_tokens``. The total
    #: adds this to the priced input saving. All null when no calibration
    #: could be loaded.
    output_tokens_saved_estimated: int | None = None
    output_usd_saved_estimated: float | None = None
    total_usd_saved_estimated: float | None = None
    calibration: SavingsOutputCalibration | None = None
    #: One row per check tool (get_change_risk, get_why, get_risk, get_health,
    #: get_dead_code), zero calls included, in that order.
    checks: list[SavingsCheckRow] = []
