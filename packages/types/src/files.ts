/**
 * File-detail contract — the aggregate behind the canonical file entity
 * page (`GET /api/repos/{id}/files/{path}`). Mirrors the server's
 * `routers/files.py` response shape.
 */

import type { CoChangePartner, FileAuthor, Hotspot, SignificantCommit } from "./git.js";
import type { FileHealthTrend, FileSignals, HealthDimension } from "./health.js";

export interface FileWikiPageRef {
  id: string;
  title: string;
  summary: string;
  content: string;
  freshness_status: string;
  confidence: number;
  human_notes: string | null;
  updated_at: string | null;
  /**
   * Deterministic coverage-tail pages are template-generated (zero LLM).
   * `is_deterministic` drives the "Auto" badge in the file docs tab;
   * `doc_tier` (2/3) mirrors `metadata.doc_tier`. Optional so payloads that
   * predate the fields still type-check.
   */
  is_deterministic?: boolean;
  doc_tier?: number | null;
}

export interface FileHealthFinding {
  id: string;
  file_path: string;
  biomarker_type: string;
  severity: string;
  function_name: string | null;
  line_start: number | null;
  line_end: number | null;
  health_impact: number;
  reason: string;
  details: Record<string, unknown>;
  status: string;
  /**
   * The finding's "home" health dimension (`defect` / `maintainability` /
   * `performance`), so the file page can tag findings by pillar. Optional /
   * `defect` when an older payload omits it.
   */
  dimension?: HealthDimension;
}

export interface FileHealthMetric {
  file_path: string;
  score: number;
  max_ccn: number;
  max_nesting: number;
  nloc: number;
  has_test_file: boolean;
  line_coverage_pct: number | null;
  module: string | null;
  duplication_pct: number | null;
  /**
   * Per-dimension scores from the three-signal split. `score` stays the overall
   * surfaced number (== `defect_score`); `maintainability_score` and
   * `performance_score` are the co-surfaced pillars. All optional/nullable so
   * older payloads parse unchanged and unmeasured pillars read "—".
   */
  defect_score?: number | null;
  maintainability_score?: number | null;
  performance_score?: number | null;
  /**
   * Dominant-cause lead (worst finding's biomarker + reason) and the summed
   * pre-floor `health_impact`. Additive display signals: the lead headlines the
   * "one reason", `total_deduction` ranks two files that both floor at `1.0`.
   * Null on clean files or payloads that predate these fields.
   */
  primary_biomarker?: string | null;
  primary_reason?: string | null;
  total_deduction?: number | null;
}

export interface FileScoreBreakdownFinding {
  id: string;
  biomarker_type: string;
  severity: string;
  raw_impact: number;
  applied_impact: number;
  function_name: string | null;
  reason: string;
}

export interface FileScoreBreakdownCategory {
  category: string;
  cap: number;
  raw_deduction: number;
  applied_deduction: number;
  capped: boolean;
  finding_count: number;
  findings: FileScoreBreakdownFinding[];
}

export interface FileScoreBreakdown {
  score: number;
  total_deduction: number;
  categories: FileScoreBreakdownCategory[];
}

export interface FileDetailHealth {
  metric: FileHealthMetric | null;
  breakdown: FileScoreBreakdown | null;
  findings: FileHealthFinding[];
  /** Per-file score trajectory; `points` empty when history is thin. */
  trend: FileHealthTrend | null;
  /** Process / people / topology signals; null fields read "no signal". */
  signals: FileSignals | null;
}

export interface FileAgentProvenance {
  agent_commit_count: number;
  agent_authored_pct: number | null;
  tier_counts: Record<string, number>;
}

/** Hotspot row + history extras the detail endpoint joins in. */
export interface FileDetailGit extends Hotspot {
  significant_commits: SignificantCommit[];
  top_authors: FileAuthor[];
  co_change_partners: CoChangePartner[];
  agent: FileAgentProvenance;
  first_commit_at: string | null;
  /** `symbol_id` -> counted fixes that landed in it, over the same window as
   *  `prior_defect_count`. Approximate: symbol spans are current-tree while
   *  each fix's line ranges are numbered on its own parent commit. A symbol
   *  with no fixes is absent from the map; the whole map is empty on an index
   *  that predates the fix rollup. */
  fix_symbol_counts?: Record<string, number>;
}

export interface FileDetailCoverage {
  line_coverage_pct: number;
  branch_coverage_pct: number | null;
  total_coverable_lines: number;
  covered_lines: number[];
  source_format: string;
  ingested_at: string | null;
  ingested_commit_sha: string | null;
}

export interface FileGraphNeighbor {
  node_id: string;
  node_type: string;
  language: string | null;
  edge_type: string;
  imported_names: string[];
}

export interface FileDetailGraph {
  language: string;
  is_entry_point: boolean;
  is_test: boolean;
  symbol_count: number;
  pagerank: number;
  pagerank_percentile: number;
  in_degree: number;
  out_degree: number;
  community_id: number;
  community_label: string | null;
  dependents: FileGraphNeighbor[];
  dependencies: FileGraphNeighbor[];
}

export interface FileSymbolSlim {
  symbol_id: string;
  name: string;
  kind: string;
  signature: string;
  start_line: number;
  end_line: number;
  visibility: string;
  complexity_estimate: number;
  is_async: boolean;
}

export interface FunctionBlameRow {
  symbol_id: string;
  function_name: string;
  start_line: number;
  end_line: number;
  line_count: number;
  mod_count: number;
  recent_mod_count: number;
  median_author_time: number | null;
  owner_name: string | null;
  owner_email: string | null;
  owner_line_pct: number | null;
}

export interface GoverningDecisionRef {
  id: string;
  title: string;
  status: string;
}

export interface FileDeadCodeFinding {
  id: string;
  kind: string;
  symbol_name: string | null;
  confidence: number;
  reason: string;
  lines: number;
  safe_to_delete: boolean;
}

/**
 * One slim row in the browsable Files index (`GET /api/repos/{id}/files`).
 * Everything the treemap + ranked table need without the per-file aggregate's
 * weight. `pagerank_pct` / `churn_pct` are 0-100 percentiles ranked over the
 * whole repo; scores are 0-10; nullable fields read "—" when unmeasured.
 */
export interface FileRow {
  file_path: string;
  language: string;
  loc: number | null;
  symbol_count: number;
  pagerank_pct: number;
  in_degree: number;
  out_degree: number;
  defect_score: number | null;
  maintainability_score: number | null;
  performance_score: number | null;
  churn_pct: number | null;
  commit_count: number | null;
  last_commit_at: string | null;
  coverage_pct: number | null;
  is_test: boolean;
  is_entry_point: boolean;
  community_id: number;
}

export interface FileLanguageCount {
  language: string;
  count: number;
}

export interface FilesIndexResponse {
  files: FileRow[];
  total: number;
  languages: FileLanguageCount[];
}

export interface FileDetailResponse {
  file_path: string;
  wiki_page: FileWikiPageRef | null;
  health: FileDetailHealth;
  git: FileDetailGit | null;
  coverage: FileDetailCoverage | null;
  graph: FileDetailGraph | null;
  symbols: FileSymbolSlim[];
  function_blame: FunctionBlameRow[];
  governing_decisions: GoverningDecisionRef[];
  dead_code: FileDeadCodeFinding[];
}
