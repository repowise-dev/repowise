"""Small request/response helper models (webhooks, provider config, cost)."""

from __future__ import annotations

from pydantic import BaseModel


class WebhookResponse(BaseModel):
    event_id: str
    status: str = "accepted"


class SetActiveProviderRequest(BaseModel):
    provider: str
    model: str | None = None


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


class DistillSavingsResponse(BaseModel):
    """Savings ledger rollup for the Costs page card.

    Covers the ``repowise distill`` command/hook path only — MCP response
    truncation is not recorded in the ledger. ``available`` is False when
    the repo has no omission store on disk (feature unused).
    """

    available: bool
    events: int = 0
    raw_tokens: int = 0
    distilled_tokens: int = 0
    saved_tokens: int = 0
    estimated_usd_saved: float = 0.0
    pricing_model: str = ""
    per_filter: list[DistillSavingsGroup] = []
    per_day: list[DistillSavingsGroup] = []
