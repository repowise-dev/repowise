/**
 * Canonical dead-code finding types.
 *
 * Canonical source: engine `DeadCodeFindingResponse` + `DeadCodeSummaryResponse`.
 * Some downstream pipelines emit extra raw-shape fields (`evidence`, `package`,
 * `last_commit_at`, `commit_count_90d`, `age_days`) — preserved here as
 * optional so consumer adapters don't lose information when normalising.
 */

export type DeadCodeStatus = "open" | "acknowledged" | "resolved" | "false_positive";

/**
 * The one set of confidence boundaries, mirroring the engine.
 *
 * `HIGH` is `SAFE_CONFIDENCE_THRESHOLD` in
 * `core/analysis/dead_code/risk_factors.py`: below it nothing is ever
 * deletion-ready, and the summary endpoint counts the same way
 * (`persistence/crud/analysis/dead_code.py`). `MEDIUM` is the list endpoint's
 * default `min_confidence` floor, so anything under it is normally never
 * fetched at all.
 *
 * Three surfaces used to disagree about these numbers, which put one 0.72
 * finding in "high" on the summary card, "Medium" in the breakdown grid, and
 * an unremarkable colour in the table while wearing a green Candidate badge.
 */
export const DEAD_CODE_CONFIDENCE = {
  /** Deletion-ready floor; matches SAFE_CONFIDENCE_THRESHOLD. */
  HIGH: 0.7,
  /** The list endpoint's default floor; below this findings are not fetched. */
  MEDIUM: 0.4,
} as const;

/** Which tier a confidence falls in, using the boundaries above. */
export function deadCodeConfidenceTier(confidence: number): "high" | "medium" | "low" {
  if (confidence >= DEAD_CODE_CONFIDENCE.HIGH) return "high";
  if (confidence >= DEAD_CODE_CONFIDENCE.MEDIUM) return "medium";
  return "low";
}

export interface DeadCodeFinding {
  id: string;
  kind: string;
  file_path: string;
  symbol_name: string | null;
  symbol_kind: string | null;
  confidence: number;
  reason: string;
  lines: number;
  /**
   * Effective deletion-readiness — high confidence AND no runtime-load risk
   * factors. Re-derived server-side, not the raw persisted boolean.
   */
  safe_to_delete: boolean;
  /**
   * Runtime-load risk factors (config / bootstrap / database / environment /
   * script). Non-empty means a review candidate, never deletion-ready.
   */
  risk_factors?: string[];
  primary_owner: string | null;
  status: DeadCodeStatus;
  note: string | null;
  /** Raw engine artifact fields — present in some downstream pipelines, optional here. */
  evidence?: string[] | null;
  package?: string | null;
  last_commit_at?: string | null;
  commit_count_90d?: number;
  age_days?: number | null;
}

export interface DeadCodePatchInput {
  status: DeadCodeStatus;
  note?: string;
}

export interface DeadCodeSummary {
  total_findings: number;
  confidence_summary: Record<string, number>;
  deletable_lines: number;
  total_lines: number;
  by_kind: Record<string, number>;
}
