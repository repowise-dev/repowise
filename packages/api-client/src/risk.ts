/**
 * REST client for the change-risk endpoints.
 * Backend: packages/server/src/repowise/server/routers/git.py (risk/range)
 */

import { apiGet } from "./client";
import type { RiskDriverResponse } from "./types/git";

export interface RiskRangeParams {
  /** Base revision of the comparison (branch name, sha, or ref). */
  base: string;
  /** Head revision; the server defaults to HEAD when omitted. */
  head?: string;
  /**
   * How many recent commits to build the percentile baseline from. 0 skips
   * the percentile (risk_percentile and review_priority come back null).
   */
  baseline?: number;
}

export interface RiskRangeResponse {
  base: string;
  head: string;
  score: number;
  /** The unit `score` is calibrated on: a single commit, not a whole range. */
  score_unit: string;
  risk_percentile: number | null;
  review_priority: string | null;
  classification: string | null;
  /** Absolute band, present only when there was no baseline to rank against. */
  fallback_band: string | null;
  is_fix: boolean;
  features: Record<string, number | null>;
  drivers: RiskDriverResponse[];
}

/** Scores the aggregate diff between two revisions (0-10, with drivers). */
export async function getRiskRange(
  repoId: string,
  params: RiskRangeParams,
): Promise<RiskRangeResponse> {
  return apiGet<RiskRangeResponse>(`/api/repos/${repoId}/risk/range`, {
    base: params.base,
    head: params.head,
    baseline: params.baseline,
  });
}
