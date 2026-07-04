/**
 * Blast-radius types — mirror the OSS engine's `BlastRadius*` schemas
 * (`packages/server/src/repowise/server/schemas.py`) and the hosted
 * backend's `app/models/schemas.py` `BlastRadius*` Pydantic models.
 *
 * Both backends produce the same shape; this is the canonical TS contract.
 */

export interface DirectRiskEntry {
  path: string;
  /** centrality * (1 + temporal_hotspot); unbounded, compare within a change set. */
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

export interface BlastRadiusResponse {
  direct_risks: DirectRiskEntry[];
  transitive_affected: TransitiveEntry[];
  cochange_warnings: CochangeWarning[];
  recommended_reviewers: ReviewerEntry[];
  test_gaps: string[];
  /** 0–10. */
  overall_risk_score: number;
}

export interface BlastRadiusRequest {
  changed_files: string[];
  max_depth?: number;
}
