"""Health / liveness response models."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str


class CoordinatorHealthResponse(BaseModel):
    sql_pages: int | None
    sql_decisions: int | None
    vector_count: int | None  # total page + decision vectors
    vector_page_count: int | None
    vector_decision_count: int | None
    graph_nodes: int | None
    drift_pct: float | None  # alias of page_drift_pct (backwards compat)
    page_drift_pct: float | None  # wiki_pages <-> page vectors
    decision_drift_pct: float | None  # decision_records <-> decision vectors
    status: str  # "ok" | "warning" | "critical"
    detail: str | None = None  # human-readable explanation of the status
