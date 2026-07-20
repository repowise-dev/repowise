"""Search request/response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str
    search_type: str = "semantic"
    limit: int = Field(default=10, ge=1, le=100)


class SearchResultResponse(BaseModel):
    page_id: str
    title: str
    page_type: str
    target_path: str
    score: float
    snippet: str
    search_type: str
    # True for deterministic template pages (the coverage tail) — drives the
    # "Auto" badge on the search result card. Defaults False so older callers
    # and unresolved pages read as regular LLM pages.
    is_deterministic: bool = False
