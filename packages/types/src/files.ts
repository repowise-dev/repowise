/**
 * File-detail contract — the aggregate behind the canonical file entity
 * page (`GET /api/repos/{id}/files/{path}`). Mirrors the server's
 * `routers/files.py` response shape.
 */

import type { CoChangePartner, FileAuthor, Hotspot, SignificantCommit } from "./git.js";
import type { FileHealthTrend } from "./health.js";

export interface FileWikiPageRef {
  id: string;
  title: string;
  summary: string;
  content: string;
  freshness_status: string;
  confidence: number;
  human_notes: string | null;
  updated_at: string | null;
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
