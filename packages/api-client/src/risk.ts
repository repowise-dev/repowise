/**
 * REST client for the change-risk endpoints.
 * Backend: packages/server/src/repowise/server/routers/git.py (risk/range)
 */

import { apiGet } from "./client";
import type { RiskAuthority } from "@repowise-dev/types/risk-semantics";
import type { RiskDriverResponse } from "./types/git";

export interface RiskRangeParams {
  /** Base revision of the comparison (branch name, sha, or ref). */
  base: string;
  /** Head revision; the server defaults to HEAD when omitted. */
  head?: string;
  /**
   * How many recent commits to build the percentile baseline from. 0 skips
   * every percentile (risk_percentile, review_priority and
   * fix_history.percentile all come back null).
   */
  baseline?: number;
}

export interface FixHistoryFile {
  path: string;
  churn: number;
  /** Prior bug fixes on this file, recency-weighted (a year ago counts a half). */
  fix_pressure: number;
}

/**
 * Bug-fix history of the files a change touches — the part of the answer that
 * does not grow with the diff, and the part `score` cannot see.
 */
export interface FixHistory {
  /** False when the history walk could not run — not the same as "no fixes". */
  available: boolean;
  /** Churn-weighted mean fix pressure across the changed files. */
  density: number;
  /** Rank against the fix density of this repo's own recent commits; null if too few to rank. */
  percentile: number | null;
  files: FixHistoryFile[];
}

export interface RiskRangeResponse {
  base: string;
  head: string;
  /** Separate historical evidence about where the change lands. */
  fix_history: FixHistory;
  /** Percentile/classification authority plus explicit absolute fallback. */
  risk_authority: RiskAuthority;
  score: number;
  /** What `score` measures: diff size and spread, not where the change lands. */
  score_measures: string;
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

/** Assesses a live diff; lead with its repo-relative percentile/classification. */
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
