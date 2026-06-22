"""Small request/response helper models (webhooks, provider config, cost)."""

from __future__ import annotations

from pydantic import BaseModel


class WebhookResponse(BaseModel):
    event_id: str
    status: str = "accepted"


class SetActiveProviderRequest(BaseModel):
    provider: str
    model: str | None = None
    # When set, the selection is scoped to this repo (so picking a model for
    # one repo never shadows another in a workspace). Omit for the global default.
    repo_id: str | None = None


class SetApiKeyRequest(BaseModel):
    api_key: str


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


class DistillSavingsGroup(BaseModel):
    group: str
    events: int
    raw_tokens: int
    distilled_tokens: int
    saved_tokens: int


class McpDropGroup(BaseModel):
    """Per-tool MCP savings (``tool`` with the ``mcp:`` prefix stripped).

    ``kind`` distinguishes a ``"counterfactual"`` saving (the answer replaced raw
    file exploration — recorded in the savings ledger) from a ``"truncation"``
    drop (content trimmed past the response budget — the only signal for tools
    without a counterfactual estimator yet).
    """

    tool: str
    events: int
    tokens: int
    kind: str = "truncation"


class DistillSavingsResponse(BaseModel):
    """Savings rollup for the Costs page hero card.

    The ``distill`` block (``saved_tokens`` etc.) covers the ``repowise
    distill`` command/hook path. The ``mcp`` block surfaces tokens already
    dropped past MCP response budgets (the ``omissions`` store), which the
    distill ledger never recorded. Savings are priced at the *coding agent's*
    detected model (``pricing_model`` / ``pricing_agent`` / ``pricing_source``)
    — they are input tokens that agent never had to read. ``available`` is
    False when the repo has no omission store on disk (feature unused).
    """

    available: bool
    events: int = 0
    raw_tokens: int = 0
    distilled_tokens: int = 0
    saved_tokens: int = 0
    estimated_usd_saved: float = 0.0
    pricing_model: str = ""
    # How the pricing model was resolved (Phase 1 model-aware pricing).
    pricing_agent: str = "unknown"
    pricing_source: str = "default"
    per_filter: list[DistillSavingsGroup] = []
    per_day: list[DistillSavingsGroup] = []
    # Unified MCP savings: counterfactual ledger rows (each answer replaced raw
    # file exploration) merged with truncation drops for tools that have no
    # counterfactual estimator. ``mcp_queries`` counts counterfactual tool calls
    # ("N MCP queries answered"); ``mcp_tokens`` is total saved.
    mcp_events: int = 0
    mcp_tokens: int = 0
    mcp_queries: int = 0
    mcp_per_tool: list[McpDropGroup] = []
    # Missed savings — raw (non-distilled) agent commands a filter would have
    # caught, scanned best-effort from local Claude Code transcripts.
    missed_events: int = 0
    missed_tokens_est: int = 0
    missed_window_days: float = 0.0
    # Missed MCP savings — full re-reads of unchanged files a targeted
    # get_symbol / range read would have replaced, same transcript scan.
    reread_events: int = 0
    reread_tokens_est: int = 0
