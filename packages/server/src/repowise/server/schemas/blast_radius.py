"""Blast-radius request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from repowise.server.schemas.risk_semantics import (
    RiskCompatibilityField,
    RiskScalarSemantics,
)


class BlastRadiusRequest(BaseModel):
    changed_files: list[str]
    max_depth: int = Field(default=3, ge=1, le=10)


class DirectRiskEntry(BaseModel):
    path: str
    structural_score: float
    #: Deprecated exact alias of ``structural_score`` for older clients.
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


class TestImpactEvidence(BaseModel):
    basis: Literal["measured", "inferred"]
    source_file: str
    via: Literal["coverage-map", "call-graph", "import-graph"]
    source_format: str | None = None


class TestRecommendation(BaseModel):
    test_id: str
    test_file: str | None = None
    repository_id: str
    repository: str
    basis: Literal["measured", "inferred"]
    bases: list[Literal["measured", "inferred"]]
    source_files: list[str]
    evidence: list[TestImpactEvidence]


class TestImpactFile(BaseModel):
    source_file: str
    status: Literal["measured", "inferred", "unknown"]
    measured_tests: list[str]
    measured_tests_total: int
    inferred_tests: list[str]
    inferred_tests_total: int


class TestImpactFreshness(BaseModel):
    status: Literal["current", "stale", "unknown"]
    reason: str | None = None
    ingested_commit: str | None = None
    indexed_commit: str | None = None


class TestImpactCoverage(BaseModel):
    status: Literal["available", "partial", "unavailable", "degraded"]
    reason: str | None = None
    map_present: bool
    pair_count: int
    test_count: int
    source_file_count: int
    changed_files_total: int
    changed_files_with_measured_tests: int
    changed_files_without_measured_tests: int
    ingested_at: str | None = None
    source_format: str | None = None
    freshness: TestImpactFreshness


class TestImpactInference(BaseModel):
    status: Literal["available", "degraded"]
    reason: str | None = None
    changed_files_total: int
    changed_files_with_candidates: int
    candidates_before_dedup: int


class TestImpactAnalysis(BaseModel):
    status: Literal["available", "partial", "degraded"]
    stale: bool
    partial: bool
    degraded: bool
    basis_categories: list[Literal["measured", "inferred"]]


class TestImpactResponse(BaseModel):
    recommendations: list[TestRecommendation]
    recommendations_total: int
    recommendations_emitted: int
    recommendations_truncated: bool
    recommendations_omitted: int
    recommendations_by_primary_basis: dict[Literal["measured", "inferred"], int]
    files: list[TestImpactFile]
    files_total: int
    files_without_measured_tests: list[str]
    unknown_files: list[str]
    coverage: TestImpactCoverage
    inference: TestImpactInference
    analysis: TestImpactAnalysis


class BlastRadiusResponse(BaseModel):
    direct_risks: list[DirectRiskEntry]
    transitive_affected: list[TransitiveEntry]
    cochange_warnings: list[CochangeWarning]
    recommended_reviewers: list[ReviewerEntry]
    test_gaps: list[str]
    test_impact: TestImpactResponse
    structural_impact_score: float
    structural_impact_band: Literal["localized", "moderate", "broad"]
    structural_impact_scale: RiskScalarSemantics
    #: Deprecated exact alias; never repurposed to a new unit.
    overall_risk_score: float
    overall_risk_score_compatibility: RiskCompatibilityField
