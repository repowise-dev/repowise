/**
 * Canonical dead-code finding types.
 *
 * Canonical source: engine `DeadCodeFindingResponse` + `DeadCodeSummaryResponse`.
 * Some downstream pipelines emit extra raw-shape fields (`evidence`, `package`,
 * `last_commit_at`, `commit_count_90d`, `age_days`) — preserved here as
 * optional so consumer adapters don't lose information when normalising.
 */

export type DeadCodeStatus = "open" | "acknowledged" | "resolved" | "false_positive";

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
