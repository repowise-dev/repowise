/**
 * Canonical dead-code finding types.
 *
 * Canonical source: engine `DeadCodeFindingResponse` + `DeadCodeSummaryResponse`.
 * Some downstream pipelines emit extra raw-shape fields (`evidence`,
 * `age_days`) — preserved here as optional so consumer adapters don't lose
 * information when normalising. `package` was dropped: it duplicated the
 * first segment of `file_path`.
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
  /**
   * The list endpoint & CLI's default `min_confidence` floor (0.4); mirrors
   * `RISK_CAP_CONFIDENCE` in `core/analysis/dead_code/risk_factors.py`. Below this
   * findings are not fetched.
   */
  MEDIUM: 0.4,
} as const;

/** Which tier a confidence falls in, using the boundaries above. */
export function deadCodeConfidenceTier(confidence: number): "high" | "medium" | "low" {
  if (confidence >= DEAD_CODE_CONFIDENCE.HIGH) return "high";
  if (confidence >= DEAD_CODE_CONFIDENCE.MEDIUM) return "medium";
  return "low";
}

/**
 * Human-readable label per runtime-load risk factor.
 *
 * Mirrors `_FACTOR_BLURB` in `core/analysis/dead_code/risk_factors.py`, which
 * is where the engine's own evidence line gets its wording. The slugs are an
 * API vocabulary, not English: joining them straight into a sentence renders
 * "Runtime-load risk (config, asset)" where the engine says
 * "configuration, runtime-loaded web asset".
 *
 * Keyed on every tag `path_risk_factors` can emit; a contract test
 * (`tests/unit/dead_code/test_confidence_parity.py`) fails when the two key
 * sets drift, so a new engine factor cannot ship without a label here.
 */
export const DEAD_CODE_RISK_FACTOR_LABELS: Record<string, string> = {
  config: "configuration",
  environment: "environment/bootstrap",
  bootstrap: "bootstrap/entry-point",
  database: "database/schema",
  script: "script/task",
  asset: "runtime-loaded web asset",
};

/** Label for one risk factor, falling back to the raw slug for an unknown tag. */
export function deadCodeRiskFactorLabel(factor: string): string {
  return DEAD_CODE_RISK_FACTOR_LABELS[factor] ?? factor;
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
   * script / asset). Non-empty means a review candidate, never
   * deletion-ready.
   */
  risk_factors?: string[];
  primary_owner: string | null;
  status: DeadCodeStatus;
  note: string | null;
  /**
   * When the file was last touched. This is the staleness signal, and it is
   * not `age_days`: that is measured from the *first* commit, so it answers
   * "how old is this file" rather than "how long has this been dead". The two
   * disagree on 75% of findings, so never label `age_days` as deadness.
   */
  last_commit_at?: string | null;
  /** Commits to the file in the last 90 days; 0 is what earns a high confidence. */
  commit_count_90d?: number;
  /** Raw engine artifact fields — present in some downstream pipelines, optional here. */
  evidence?: string[] | null;
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
