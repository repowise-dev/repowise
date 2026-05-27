"""Health / liveness response models."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str


class CoordinatorHealthResponse(BaseModel):
    sql_pages: int | None
    vector_count: int | None
    graph_nodes: int | None
    drift_pct: float | None
    status: str  # "ok" | "warning" | "critical"
