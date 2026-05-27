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
