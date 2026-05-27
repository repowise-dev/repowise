"""Blast-radius request/response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BlastRadiusRequest(BaseModel):
    changed_files: list[str]
    max_depth: int = Field(default=3, ge=1, le=10)


class DirectRiskEntry(BaseModel):
    path: str
    risk_score: float
    temporal_hotspot: float
    centrality: float


class TransitiveEntry(BaseModel):
    path: str
    depth: int


class CochangeWarning(BaseModel):
    changed: str
    missing_partner: str
    score: float


class ReviewerEntry(BaseModel):
    email: str
    files: int
    ownership_pct: float


class BlastRadiusResponse(BaseModel):
    direct_risks: list[DirectRiskEntry]
    transitive_affected: list[TransitiveEntry]
    cochange_warnings: list[CochangeWarning]
    recommended_reviewers: list[ReviewerEntry]
    test_gaps: list[str]
    overall_risk_score: float
