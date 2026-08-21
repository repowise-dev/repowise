/**
 * Canonical code-health wire contract — shared by the web dashboard
 * (`packages/web`), the shared UI (`packages/ui`), the hosted frontend, and
 * the bot. Mirrors the server's `routers/code_health.py` response shapes plus
 * the band/distribution "currency" layer.
 *
 * Before this module the health types lived web-locally in
 * `packages/web/src/lib/api/code-health.ts`; they were migrated here so every
 * consumer reads one contract.
 *
 * Band cutoffs are the SINGLE TypeScript mirror of the canonical Python source
 * in `packages/core/src/repowise/core/analysis/health/grading.py`. The two are
 * kept in sync by a parity test (`__tests__/health/band-cutoffs.test.ts` here,
 * `tests/unit/health/test_grading.py` in core). Do not hardcode `4`/`8` band
 * cutoffs anywhere else — derive from these consts or read the API `band`.
 */

import type { C4IoKind } from "./external-systems.js";
import type { Paginated } from "./pagination.js";

/** Finding severity used across the health surface. */
export type HealthSeverity = "low" | "medium" | "high" | "critical";

/* ------------------------------------------------------------------ *
 * Health dimensions (the three-signal split)
 * ------------------------------------------------------------------ */

/**
 * The orthogonal health signals. `defect` is the historical, calibrated score
 * surfaced as the overall number; `maintainability` is a co-surfaced signal
 * made of the smells the defect calibration floors (they don't predict bugs, so
 * they get a proper home here instead of diluting the defect score);
 * `performance` is the co-surfaced third signal: static performance RISK
 * (I/O-in-loop / N+1 shapes that waste work). All three are co-equal views; the
 * overall number stays the defect score and is never a blend.
 *
 * Mirror of `DIMENSIONS` in
 * `packages/core/src/repowise/core/analysis/health/scoring.py`, kept in sync by
 * a parity test (`__tests__/health.test.ts` here,
 * `tests/unit/health/test_scoring_dimensions.py` in core).
 */
export type HealthDimension = "defect" | "maintainability" | "performance";

/** Canonical dimension order (parity-locked against core's `DIMENSIONS`). */
export const HEALTH_DIMENSIONS: readonly HealthDimension[] = [
  "defect",
  "maintainability",
  "performance",
] as const;

/** Display labels for the dimensions surfaced today. */
export const HEALTH_DIMENSION_LABEL: Record<HealthDimension, string> = {
  defect: "Defect risk",
  maintainability: "Maintainability",
  performance: "Performance",
};

/**
 * Human-readable labels for the I/O-boundary kind a performance finding crosses
 * (the `boundary_kind` on an `io_in_loop` finding's `details`). The kind set is
 * the canonical `C4IoKind` from `external-systems.ts`, parity-locked against the
 * Python `IO_KINDS` classifier; this only adds display strings, no new wire
 * enum. Used to render "a database call runs once per loop iteration" detail.
 */
export const PERF_BOUNDARY_LABEL: Record<C4IoKind, string> = {
  db: "Database",
  network: "Network",
  filesystem: "Filesystem",
  subprocess: "Subprocess",
  lock: "Lock",
};

/* ------------------------------------------------------------------ *
 * Band "currency" layer
 * ------------------------------------------------------------------ */

/**
 * The 3 defect-backed health buckets. Alert files carry roughly 17x the
 * defect rate of Healthy files on our calibration corpus, so the boundaries
 * are empirically defensible rather than arbitrary. This replaces the legacy
 * ad-hoc 4-band labeling (`critical/poor/fair/good`).
 */
export type HealthBand = "healthy" | "warning" | "alert";

/** Score at or above this is Healthy. */
export const HEALTHY_MIN = 8.0;
/** Score below this is Alert; `[ALERT_MAX, HEALTHY_MIN)` is Warning. */
export const ALERT_MAX = 4.0;

export const HEALTH_BAND_LABEL: Record<HealthBand, string> = {
  healthy: "Healthy",
  warning: "Warning",
  alert: "Alert",
};

/**
 * Pure score -> band mapping. Mirror of `grading.band_for` in core. Prefer the
 * API-provided `band` where available; use this only when deriving locally.
 */
export function bandForScore(score: number): HealthBand {
  if (score < ALERT_MAX) return "alert";
  if (score < HEALTHY_MIN) return "warning";
  return "healthy";
}

export interface HealthBandShare {
  /** Number of files in this band. */
  files: number;
  /** Sum of NLOC across the files in this band. */
  nloc: number;
  /** NLOC-weighted share of the repo in this band, 0-100. */
  pct: number;
}

/**
 * NLOC-weighted distribution of files across the 3 bands. The repo-level
 * "health distribution" surfaced on the dashboard + badge.
 */
export interface HealthDistribution {
  total_files: number;
  total_nloc: number;
  bands: Record<HealthBand, HealthBandShare>;
}

/* ------------------------------------------------------------------ *
 * Defect-accuracy ("does the score find the bugs?") — migrated from
 * packages/ui so the overview response can reference it without ui depending
 * back into web. `packages/ui` re-exports these for component prop typing.
 * ------------------------------------------------------------------ */

export interface DefectAccuracyFile {
  file_path: string;
  score: number;
  recent_fixes: number;
}

export interface DefectAccuracyPoint {
  k: number;
  hits: number;
}

export interface DefectAccuracy {
  k: number;
  hits: number;
  precision: number;
  base_rate: number;
  lift: number | null;
  window_days: number;
  scored_files: number;
  defect_files: number;
  concentration_file_fraction: number;
  concentration_defect_share: number;
  precision_table: DefectAccuracyPoint[];
  flagged_files: DefectAccuracyFile[];
}

/* ------------------------------------------------------------------ *
 * Core file/finding/module rows
 * ------------------------------------------------------------------ */

export interface HealthFileMetric {
  file_path: string;
  score: number;
  max_ccn: number;
  max_nesting: number;
  nloc: number;
  has_test_file: boolean;
  line_coverage_pct: number | null;
  module: string | null;
  duplication_pct?: number | null;
  /**
   * Per-dimension scores from the three-signal split. `score` stays the overall
   * surfaced number (== `defect_score` until a deliberate blend decision).
   * `maintainability_score` is the co-surfaced second signal;
   * `performance_score` is computed but not yet surfaced as its own pillar
   * (`null` on payloads that predate the performance detectors). All optional so
   * older payloads parse unchanged.
   */
  defect_score?: number | null;
  maintainability_score?: number | null;
  performance_score?: number | null;
  /**
   * Open performance-risk findings on this file. The performance lens on the
   * code-health map colors by this count (+ `performance_analyzed`), not by the
   * [9,10]-compressed `performance_score`, so a file with 40 N+1s reads
   * differently from one with 1. Absent on payloads predating the perf pass.
   */
  performance_findings?: number | null;
  /**
   * Whether a performance detector actually ran on this file (its language has a
   * registered perf dialect). `false` = unsupported language, the perf pass never
   * looked — a silent 10.0 — so the map greys the file as "not analyzed" instead
   * of green. High-precision / low-recall: green means "a detector ran and
   * surfaced nothing", never "verified fast". `null`/absent on older payloads.
   */
  performance_analyzed?: boolean | null;
  /**
   * Dominant-cause lead: the biomarker + reason of this file's worst finding, so
   * a low file can headline "the one reason" instead of a wall of markers. Null
   * when the row carries no findings or the payload predates this field.
   */
  primary_biomarker?: string | null;
  primary_reason?: string | null;
  /**
   * Summed (pre-floor) `health_impact` across the file's findings — the score's
   * deduction magnitude. Distinguishes two files that both clamp to `1.0` (a −25
   * from a −9) so they can be ranked by depth. The calibrated `score` is
   * unchanged; this is a display-only secondary distinguisher.
   */
  total_deduction?: number | null;
}

export interface HealthFinding {
  id: string;
  file_path: string;
  biomarker_type: string;
  severity: HealthSeverity;
  function_name: string | null;
  line_start: number | null;
  line_end: number | null;
  health_impact: number;
  reason: string;
  details: Record<string, unknown>;
  status: string;
  /** Matching symbol id when the finding names a function; links to the symbol page. */
  symbol_id?: string | null;
  /**
   * The finding's "home" health dimension (`defect` / `maintainability` /
   * `performance`), used to filter findings by pillar. Optional/`defect` when an
   * older payload omits it.
   */
  dimension?: HealthDimension;
}

export type PerformanceExecutionContext = "production" | "tooling" | "test";
export type PerformanceOpportunityConfidence = "high" | "medium" | "low";

export interface PerformanceOpportunityFix {
  strategy: string;
  safety: "proven" | "advisory";
  rationale: string;
}

export interface PerformanceOpportunityEvidence {
  finding_id: string;
  file_path: string;
  biomarker_type: string;
  function_name: string | null;
  line_start: number | null;
  line_end: number | null;
  reason: string;
  path: string[];
  provenance: string;
}

export interface PerformanceOpportunity {
  opportunity_id: string;
  biomarker_type: string;
  biomarker_types: string[];
  boundary_kind: C4IoKind | null;
  execution_context: PerformanceExecutionContext;
  terminal_sink: string | null;
  shared_path_suffix: string[];
  intervention_symbol: string | null;
  affected_call_sites_total: number;
  affected_files_total: number;
  observations_total: number;
  evidence: PerformanceOpportunityEvidence[];
  evidence_truncated: boolean;
  reliable_entry_reachability: boolean | null;
  provenance: string;
  confidence: PerformanceOpportunityConfidence;
  rank_score: number;
  rank_factors: Record<string, number>;
  fix: PerformanceOpportunityFix | null;
  /** Exact stored match. Never inferred from file, marker, or rank. */
  plan_id: string | null;
  plan_status: "available" | "no_safe_plan" | "not_persisted";
  plan_reason: string;
}

export interface PerformanceOpportunitySummary {
  total: number;
  production_total: number;
  tooling_total: number;
  test_total: number;
  with_plan_total: number;
  without_plan_total: number;
}

export interface PerformanceOpportunityPage extends Paginated<PerformanceOpportunity> {
  summary: PerformanceOpportunitySummary;
}

export interface HealthModuleRow {
  module: string;
  file_count: number;
  nloc: number;
  average_health: number;
  worst_performer_path: string;
  worst_performer_score: number;
}

export interface BiomarkerBreakdownRow {
  biomarker_type: string;
  critical: number;
  high: number;
  medium: number;
  low: number;
  total: number;
}

/* ------------------------------------------------------------------ *
 * Overview
 * ------------------------------------------------------------------ */

export interface HealthOverviewSummary {
  file_count: number;
  average_health: number;
  hotspot_health?: number | null;
  worst_performer_path: string | null;
  worst_performer_score: number | null;
  open_findings: number;
  severity_breakdown?: {
    critical: number;
    high: number;
    medium: number;
    low: number;
  };
  /** Repo-level band derived from `average_health` (added in the band/distribution layer). */
  band?: HealthBand;
  /**
   * NLOC-weighted repo headline for the maintainability pillar (the second
   * surfaced signal). `null`/absent when no file carries a maintainability
   * score. `maintainability_hotspot` is the same average restricted to hotspot
   * files, when available.
   */
  maintainability_average?: number | null;
  maintainability_hotspot?: number | null;
  /**
   * NLOC-weighted repo headline for the performance pillar (the third surfaced
   * signal: static performance RISK, not measured runtime). `null`/absent when
   * no file carries a performance score. `performance_hotspot` is the same
   * average restricted to hotspot files, when available.
   */
  performance_average?: number | null;
  performance_hotspot?: number | null;
  /** Open findings homing under the maintainability / performance pillars — the
   *  per-pillar actionable counts. Absent on payloads predating the split. */
  maintainability_findings?: number;
  performance_findings?: number;
  /** Lowest-scoring file by performance risk, surfaced only when score < 10
   *  (a clean repo returns `null` rather than a misleading "worst" at 10.0). */
  worst_performance_path?: string | null;
  worst_performance_score?: number | null;
}

export interface HealthOverviewResponse {
  summary: HealthOverviewSummary;
  /** NLOC-weighted file distribution across the 3 bands. */
  distribution?: HealthDistribution | null;
  defect_accuracy?: DefectAccuracy | null;
  files: HealthFileMetric[];
  top_findings: HealthFinding[];
  modules?: HealthModuleRow[];
  biomarkers?: BiomarkerBreakdownRow[];
  meta?: {
    last_indexed_at: string | null;
    head_commit: string | null;
    snapshot_count: number;
  };
}

/* ------------------------------------------------------------------ *
 * Files list
 * ------------------------------------------------------------------ */

export interface HealthFilesResponse {
  total: number;
  offset: number;
  limit: number;
  files: HealthFileMetric[];
}

export interface HealthFilesQuery {
  limit?: number;
  offset?: number;
  sort?: string;
  order?: "asc" | "desc";
  search?: string;
  module?: string;
  only_hotspots?: boolean;
  only_untested?: boolean;
  only_failing?: boolean;
  /**
   * `"summary"` returns rows without the optional keys only the file table and
   * drawer read — `duplication_pct`, `defect_score`, and the
   * `primary_biomarker` / `primary_reason` / `total_deduction` lead — and lets
   * the server skip the repo-wide finding read that produces the lead. Measured
   * on the code-health map's 2,000-row request: 1,060,095 B -> 628,014 B.
   * Every omitted key is optional on {@link HealthFileMetric}, so a `summary`
   * row parses as one; ask for `"full"` (the default) if you print any of them.
   */
  fields?: "full" | "summary";
}

/* ------------------------------------------------------------------ *
 * File breakdown (score drill-down)
 * ------------------------------------------------------------------ */

export interface FileBreakdownFinding {
  id: string;
  biomarker_type: string;
  severity: HealthSeverity;
  raw_impact: number;
  applied_impact: number;
  function_name: string | null;
  reason: string;
}

export interface FileBreakdownCategory {
  category: string;
  cap: number;
  raw_deduction: number;
  applied_deduction: number;
  capped: boolean;
  finding_count: number;
  findings: FileBreakdownFinding[];
}

export interface HealthFileBreakdownResponse {
  file_path: string;
  metric: HealthFileMetric | null;
  breakdown: {
    score: number;
    total_deduction: number;
    categories: FileBreakdownCategory[];
  };
  findings: HealthFinding[];
  suggestions: Record<string, string>;
  /** Per-file score trajectory (silent when history is thin). */
  trend?: FileHealthTrend | null;
  /** Process / people / topology signals (null fields read "no signal"). */
  signals?: FileSignals | null;
}

/* ------------------------------------------------------------------ *
 * Per-file signals (process / people / topology)
 * ------------------------------------------------------------------ */

/**
 * The per-file signals we already compute and persist, consolidated into one
 * captioned contract. Every field is `null` when its source row is absent so
 * consumers render an honest "no signal" rather than a misleading zero — a
 * git-tracked file with no bug-fixes reports `prior_defect_count: 0`, whereas
 * a file with no git history reports `null` for the whole process/people group.
 * `change_entropy_pct` is on a 0-100 scale (the stored column is 0-1).
 * Topology degree is `null` when the file is not a graph node.
 */
export interface FileSignals {
  // Process — how the file changes over time.
  prior_defect_count: number | null;
  change_entropy_pct: number | null;
  lines_added_90d: number | null;
  lines_deleted_90d: number | null;
  commit_count_90d: number | null;
  age_days: number | null;
  // People — who owns it recently vs over its whole life.
  primary_owner_name: string | null;
  primary_owner_commit_pct: number | null;
  recent_owner_name: string | null;
  recent_owner_commit_pct: number | null;
  // Topology — how connected it is in the dependency graph.
  in_degree: number | null;
  out_degree: number | null;
  // Defect history — how often this file gets bug-fixed, and where in it.
  // `bug_magnet` is the decayed fix mass past its trigger, so it is a recency
  // claim: any copy that shows it must show `last_fix_at` too.
  // `fix_symbol_counts` maps symbol_id to how many recent fixes landed in it,
  // top few only, already sorted by count.
  bug_magnet: boolean | null;
  last_fix_at: string | null;
  fix_symbol_counts: Record<string, number> | null;
}

/* ------------------------------------------------------------------ *
 * Per-file trajectory
 * ------------------------------------------------------------------ */

/** One file's score at one snapshot. */
export interface FileTrendPoint {
  taken_at: string | null;
  score: number;
  /**
   * `score` with the 1.0 floor undone, so a file too deep to move the visible
   * score still has a line that moves. Equal to `score` unless the snapshot
   * recorded the file's real depth, which only happens once it is on the
   * floor; may go below 0. Optional: the hosted backend does not send it, and
   * neither do rows written before deductions were captured.
   */
  unclamped_score?: number;
}

/**
 * A single file's score-over-time series plus the deltas worth surfacing.
 * `points` is oldest-first and **empty when fewer than two snapshots carry
 * the file** — consumers render a "no history yet" state rather than a
 * misleading single dot. `current`/`previous`/`delta`/`declining` are null/
 * false in that case. `snapshot_count` is the whole repo window size, so a
 * young repo is distinguishable from a file absent in older snapshots.
 */
export interface FileHealthTrend {
  file_path: string;
  points: FileTrendPoint[];
  current: number | null;
  previous: number | null;
  delta: number | null;
  /**
   * Movement in the unclamped score between the last two points. Equal to
   * `delta` whenever the floor is not involved, so it can be read
   * unconditionally. Optional for hosted, which does not send it.
   */
  unclamped_delta?: number | null;
  declining: boolean;
  snapshot_count: number;
}

/* ------------------------------------------------------------------ *
 * Trend
 * ------------------------------------------------------------------ */

export interface HealthTrendResponse {
  history: Array<{
    taken_at: string | null;
    hotspot_health: number;
    average_health: number;
    worst_performer_path: string | null;
    worst_performer_score: number | null;
  }>;
  summary: {
    current_hotspot_health: number;
    current_average_health: number;
    previous_hotspot_health: number | null;
    previous_average_health: number | null;
    hotspot_delta: number | null;
    average_delta: number | null;
  };
  alerts: Array<{
    kind: string;
    metric: string;
    current: number;
    baseline: number | null;
    delta: number;
    message: string;
  }>;
  /** Largest movements first, in either direction, capped server-side. */
  file_deltas: Array<{
    file_path: string;
    before: number;
    after: number;
    delta: number;
  }>;
  /**
   * How many files moved in total, before the cap. Optional: the hosted
   * backend does not send it, so consumers fall back to `file_deltas.length`.
   */
  file_deltas_total?: number;
  snapshot_count: number;
}

/* ------------------------------------------------------------------ *
 * Coverage
 * ------------------------------------------------------------------ */

export interface CoverageFileRow {
  file_path: string;
  source_format: string;
  line_coverage_pct: number;
  branch_coverage_pct: number | null;
  total_coverable_lines: number;
  ingested_at: string | null;
  ingested_commit_sha: string | null;
  covered_lines?: number[];
  health_score?: number;
  nloc?: number;
}

export interface ModuleCoverageRow {
  module: string;
  files: number;
  covered_lines: number;
  total_lines: number;
  line_coverage_pct: number;
}

export interface CoverageSummary {
  file_count: number;
  covered_lines: number;
  total_lines: number;
  line_coverage_pct: number | null;
  branch_coverage_pct: number | null;
  source_format: string | null;
  ingested_at: string | null;
  ingested_commit_sha: string | null;
}

/**
 * Which signal answered "is this tested". `measured` is a coverage report: it
 * records the lines a test really executed. `inferred` is the dependency graph:
 * a test whose calls reach this file, which says control *can* flow there, not
 * that a run did. `none` is the honest unknown.
 *
 * The two are never merged and never averaged. They are different claims, and a
 * reader who cannot tell them apart cannot tell a measured test from a guessed
 * one. `basis` says which one answered; nothing blends them.
 */
export type CoverageBasis = "measured" | "inferred" | "none";

/**
 * Which tier of the graph found a test. `call-graph` means a test's calls reach
 * the file, the stronger claim; `import-graph` means it only imports it.
 */
export type ReachedVia = "call-graph" | "import-graph";

/**
 * One file on the inferred basis. `reached` is the whole of what this basis
 * knows about it: there is no percentage here and there never can be, because
 * reaching is a file-level fact with no line attribution behind it.
 *
 * Which tests reach it is a separate, per-file request — attributing every file
 * at once costs a walk per file, and the list is only ever read one row at a
 * time. See `TestsReachingFile`.
 */
export interface ReachedFileRow {
  file_path: string;
  reached: boolean;
  health_score?: number;
  nloc?: number;
}

/**
 * The graph-inferred test map: counts, never a ratio. `files_reached` and
 * `files_not_reached` are deliberately two counts rather than one fraction —
 * a fraction invites a progress bar, and a progress bar is the coverage
 * percentage this basis is not allowed to claim.
 *
 * `files_total` is the full count whatever `files` carries, so a trimmed list is
 * never read as the whole repo.
 */
export interface InferredTestMap {
  files: ReachedFileRow[];
  files_total: number;
  files_reached: number;
  files_not_reached: number;
  test_file_count: number;
}

/** Which tests reach one file, and which tier found them. */
export interface TestsReachingFile {
  file_path: string;
  basis: CoverageBasis;
  reached: boolean;
  /**
   * Empty when `reached` is false, and capped server-side. Render `total`
   * beside it, never `tests.length` on its own: the cut is alphabetical, so a
   * trimmed list states a cap as if it were the answer.
   */
  tests: string[];
  /** Null when `reached` is false. */
  via: ReachedVia | null;
  /** How many tests the walk found, before the cap trimmed `tests`. */
  total?: number;
  /** Whether `tests` is a trimmed slice of `total`. */
  truncated?: boolean;
}

export interface HealthCoverageResponse {
  summary: CoverageSummary;
  files: CoverageFileRow[];
  /** Capped by the request's `module_limit`, which is independent of `limit`. */
  modules: ModuleCoverageRow[];
  /**
   * How many modules exist, whatever `modules` carries — so a trimmed or
   * declined rollup is never read as the repo having that few. Optional: the
   * hosted backend does not send it yet.
   */
  modules_total?: number;
  /**
   * Which signal answered, when the response determined one. Absent means it did
   * not: an older backend that predates the field, or a caller that passed
   * `include_inferred=false` and so never consulted the graph. That is why a
   * declined response omits it rather than reporting `"none"` — not consulted
   * and nothing to say are different states.
   */
  basis?: CoverageBasis;
  /**
   * Present only when `basis` is `inferred`. `summary`, `files` and `modules`
   * are then empty: measured rows and inferred rows never share an array, so no
   * consumer can render one through the other's code path by accident.
   */
  inferred?: InferredTestMap;
}

/* ------------------------------------------------------------------ *
 * Health work queue (legacy route: /health/refactoring-targets)
 * ------------------------------------------------------------------ */

export interface HealthWorkItem {
  file_path: string;
  score: number;
  nloc: number;
  module?: string | null;
  primary_biomarker: string;
  primary_severity: HealthSeverity;
  primary_reason: string;
  primary_function: string | null;
  primary_line_start: number | null;
  primary_line_end: number | null;
  primary_suggestion?: string;
  primary_finding_id?: string;
  total_impact: number;
  finding_count: number;
  biomarkers: string[];
  effort_bucket: "S" | "M" | "L" | "XL";
  impact_per_effort: number;
  /**
   * No longer served by the OSS `/health/refactoring-targets` route: building it
   * for every file with findings, before the `limit` slice, cost 1.8 MB per
   * request to feed two click-gated consumers. Fetch a file's findings from
   * `GET /health/findings?file_path=` instead. Kept optional because the hosted
   * backend still sends it and a client may hold a cached older payload.
   */
  all_findings?: Array<{
    id: string;
    biomarker_type: string;
    severity: HealthSeverity;
    function_name: string | null;
    health_impact: number;
    reason: string;
    status?: string;
  }>;
}

export interface HealthWorkQueueResponse {
  targets: HealthWorkItem[];
  total: number;
}

export interface HealthWorkQueueQuery {
  limit?: number;
  module?: string;
  biomarker?: string;
  min_severity?: string;
  max_effort?: string;
  sort?: "impact_per_effort" | "total_impact" | "score" | "finding_count";
}

/** @deprecated Use HealthWorkItem; this is a file triage row, not a plan. */
export type RefactoringTarget = HealthWorkItem;
/** @deprecated Use HealthWorkQueueResponse. */
export type RefactoringTargetsResponse = HealthWorkQueueResponse;
/** @deprecated Use HealthWorkQueueQuery. */
export type RefactoringQuery = HealthWorkQueueQuery;

/* ------------------------------------------------------------------ *
 * Churn x complexity quadrant (the "hotspot anatomy" view)
 * ------------------------------------------------------------------ */

/**
 * One file in the churn x complexity plane. `commit_count_90d` is the churn
 * (x) axis, `max_ccn` the complexity (y) axis, `nloc` encodes dot size, and
 * `score` drives dot color via the health band. `churn_percentile` (0-100) is
 * repo-relative tooltip context so a raw count reads sensibly across repos of
 * any size. Only files with recent churn (`commit_count_90d > 0`) are plotted.
 */
export interface ChurnComplexityPoint {
  file_path: string;
  commit_count_90d: number;
  max_ccn: number;
  nloc: number;
  score: number;
  churn_percentile: number;
}

export interface ChurnComplexityResponse {
  points: ChurnComplexityPoint[];
  total: number;
}
