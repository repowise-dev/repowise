import { apiPost } from "./client";

// ---------------------------------------------------------------------------
// Types (mirror packages/server/src/repowise/server/schemas.py BlastRadius*)
// ---------------------------------------------------------------------------

export interface DirectRiskEntry {
  path: string;
  risk_score: number;
  temporal_hotspot: number;
  centrality: number;
}

export interface TransitiveEntry {
  path: string;
  depth: number;
}

export interface CochangeWarning {
  changed: string;
  missing_partner: string;
  score: number;
}

export interface ReviewerEntry {
  email: string;
  files: number;
  ownership_pct: number;
}

export interface BlastRadiusResponse {
  direct_risks: DirectRiskEntry[];
  transitive_affected: TransitiveEntry[];
  cochange_warnings: CochangeWarning[];
  recommended_reviewers: ReviewerEntry[];
  test_gaps: string[];
  overall_risk_score: number;
}

export interface BlastRadiusRequest {
  changed_files: string[];
  max_depth?: number;
}

// ---------------------------------------------------------------------------
// API call
// ---------------------------------------------------------------------------

export async function analyzeBlastRadius(
  repoId: string,
  body: BlastRadiusRequest,
): Promise<BlastRadiusResponse> {
  return apiPost<BlastRadiusResponse>(
    `/api/repos/${repoId}/blast-radius`,
    body,
  );
}
