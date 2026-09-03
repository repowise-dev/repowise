/**
 * Blast-radius types — mirror the OSS engine's `BlastRadius*` schemas
 * (`packages/server/src/repowise/server/schemas.py`) and the hosted
 * backend's `app/models/schemas.py` `BlastRadius*` Pydantic models.
 *
 * Both backends produce the same shape; this is the canonical TS contract.
 */

import type {
  RiskCompatibilityField,
  RiskScalarSemantics,
} from "./risk-semantics.js";

export interface DirectRiskEntry {
  path: string;
  /** Raw unbounded structural heuristic; compare only within this change set. */
  structural_score: number;
  /** @deprecated Exact alias of structural_score for older clients. */
  risk_score: number;
  /** 0-1 normalised. */
  temporal_hotspot: number;
  /** Raw graph centrality (pagerank); typically well below 1. */
  centrality: number;
}

export interface TransitiveEntry {
  path: string;
  depth: number;
}

export interface CochangeWarning {
  changed: string;
  missing_partner: string;
  /** Co-change frequency count from git history. */
  score: number;
}

export interface ReviewerEntry {
  email: string;
  files: number;
  /** 0–1 fraction. UI multiplies by 100 for display. */
  ownership_pct: number;
}

export type TestRecommendationBasis = "measured" | "inferred";

export interface TestImpactEvidence {
  basis: TestRecommendationBasis;
  source_file: string;
  via: "coverage-map" | "call-graph" | "import-graph";
  source_format: string | null;
}

export interface TestRecommendation {
  test_id: string;
  test_file: string | null;
  repository_id: string;
  repository: string;
  /** Strongest evidence on this de-duplicated recommendation. */
  basis: TestRecommendationBasis;
  /** Every retained evidence category; measured and inferred may both occur. */
  bases: TestRecommendationBasis[];
  source_files: string[];
  evidence: TestImpactEvidence[];
}

export interface TestImpactResponse {
  recommendations: TestRecommendation[];
  recommendations_total: number;
  recommendations_emitted: number;
  recommendations_truncated: boolean;
  recommendations_omitted: number;
  /** Exact, mutually exclusive totals over the full recommendation population. */
  recommendations_by_primary_basis: Record<TestRecommendationBasis, number>;
  files: Array<{
    source_file: string;
    status: "measured" | "inferred" | "unknown";
    measured_tests: string[];
    measured_tests_total: number;
    inferred_tests: string[];
    inferred_tests_total: number;
  }>;
  files_total: number;
  files_without_measured_tests: string[];
  unknown_files: string[];
  coverage: {
    status: "available" | "partial" | "unavailable" | "degraded";
    reason: string | null;
    map_present: boolean;
    pair_count: number;
    test_count: number;
    source_file_count: number;
    changed_files_total: number;
    changed_files_with_measured_tests: number;
    changed_files_without_measured_tests: number;
    ingested_at: string | null;
    source_format: string | null;
    freshness: {
      status: "current" | "stale" | "unknown";
      reason: string | null;
      ingested_commit: string | null;
      indexed_commit: string | null;
    };
  };
  inference: {
    status: "available" | "degraded";
    reason: string | null;
    changed_files_total: number;
    changed_files_with_candidates: number;
    candidates_before_dedup: number;
  };
  analysis: {
    status: "available" | "partial" | "degraded";
    stale: boolean;
    partial: boolean;
    degraded: boolean;
    basis_categories: TestRecommendationBasis[];
  };
}

export interface BlastRadiusResponse {
  direct_risks: DirectRiskEntry[];
  transitive_affected: TransitiveEntry[];
  cochange_warnings: CochangeWarning[];
  recommended_reviewers: ReviewerEntry[];
  test_gaps: string[];
  /** Canonical typed test-impact population. Optional for older servers. */
  test_impact?: TestImpactResponse;
  /** Deterministic, uncalibrated structural-impact heuristic, 0–10. */
  structural_impact_score: number;
  structural_impact_band: "localized" | "moderate" | "broad";
  structural_impact_scale: RiskScalarSemantics;
  /** @deprecated Exact alias of structural_impact_score; not a probability. */
  overall_risk_score: number;
  overall_risk_score_compatibility: RiskCompatibilityField;
}

export interface BlastRadiusRequest {
  changed_files: string[];
  max_depth?: number;
}
