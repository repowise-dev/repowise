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
  /** Raw decayed churn sum, unbounded; use churn_percentile for 0–1 normalised rank. */
  temporal_hotspot: number;
  /** Repo-relative rank of temporal_hotspot, 0–1. Comparable as a rank, not as activity. */
  churn_percentile: number;
  /** The index's hotspot verdict: top-quartile churn AND its absolute activity floors. */
  is_hotspot: boolean;
  /** Raw graph centrality (pagerank); typically well below 1. */
  centrality: number;
}

export interface TransitiveEntry {
  path: string;
  depth: number;
  /** The file one hop nearer the change that this one imports. Only on a change analysis. */
  via?: string | null;
}

export interface CochangeWarning {
  changed: string;
  missing_partner: string;
  /** Co-change frequency count from git history. */
  score: number;
}

export interface ReviewerEntry {
  email: string;
  /** Display name from git history; only on a change analysis. */
  name?: string | null;
  files: number;
  /** 0–1 fraction. UI multiplies by 100 for display. */
  ownership_pct: number;
}

export type TestRecommendationBasis = "measured" | "inferred";

export interface TestImpactEvidence {
  basis: TestRecommendationBasis;
  source_file: string;
  via: "coverage-map" | "call-graph" | "import-graph";
  /** `file` on measured evidence: a test ran the changed file, not necessarily its changed lines. */
  match?: "file" | null;
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
  /** A measured row whose test file is gone or changed since the coverage run. */
  stale?: boolean;
  stale_reason?: "test_file_missing" | "test_file_changed" | null;
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
    status: "measured" | "inferred" | "unknown" | "deleted";
    /** The path's transition, when the caller supplied one. */
    change_status?: string | null;
    /** False for a path the change deletes: it has no head side to cover. */
    head_present?: boolean;
    measured_tests: string[];
    measured_tests_total: number;
    inferred_tests: string[];
    inferred_tests_total: number;
  }>;
  files_total: number;
  files_without_measured_tests: string[];
  unknown_files: string[];
  /** Changed paths the change deletes. Not a coverage gap. */
  deleted_files?: string[];
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
    /** Stored per-file line coverage, when there is no per-test map. */
    aggregate?: {
      file_count: number;
      ingested_commit: string | null;
      freshness: "current" | "stale" | "unknown";
    } | null;
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
  /** Per affected file; only when the request asked `with_graph`. */
  affected_signals?: AffectedSignal[];
  /** Owners of the changed files themselves; only with `with_graph`. */
  changed_owners?: ChangedOwner[] | null;
  /** Commands that run the recommended tests, one per runner; only with `with_graph`. */
  test_commands?: string[] | null;
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
  /** Also return `via` on transitive rows and `affected_signals`, for drawing. */
  with_graph?: boolean;
}

/** What the index knows about one affected file. `null` means unknown, never healthy. */
export interface AffectedSignal {
  path: string;
  is_hotspot: boolean | null;
  health_score: number | null;
  module: string | null;
  /** A test file: a test to run, never code the change reaches. Absent from older servers. */
  is_test?: boolean;
}

/** Who owns some of the changed files, by primary authorship. */
export interface ChangedOwner {
  email: string;
  name: string | null;
  /** Changed files this person is the primary owner of. */
  files: number;
}

export type ChangeCandidateKind = "working_tree" | "branch" | "commit";

/** One change a reviewer can pick. `id` is what the analysis call takes back. */
export interface ChangeCandidate {
  id: string;
  kind: ChangeCandidateKind;
  label: string;
  title: string | null;
  author: string | null;
  committed_at: string | null;
  head_sha: string | null;
  /** The branch it is compared against, for a branch. */
  base: string | null;
  /** Commits only on this branch, for a branch. */
  ahead: number | null;
  files_changed: number | null;
  /** The checked-out branch, or uncommitted work in the checkout. */
  is_current: boolean;
  /** For a branch: whether it exists on the remote; local-only is often scratch work. */
  pushed?: boolean | null;
}

export interface ChangeCandidatesResponse {
  changes: ChangeCandidate[];
  default_id: string | null;
  /** False where the server has no checkout: only indexed commits are offered. */
  working_tree_supported: boolean;
}

export interface ChangeBlastRadiusRequest {
  change_id: string;
  max_depth?: number;
}

export interface ChangeBlastRadiusResponse extends BlastRadiusResponse {
  change_id: string;
  /** The paths analyzed, after the server's cap. */
  changed_files: string[];
  /** Every path the change touches, before the cap. */
  changed_files_total: number;
}
