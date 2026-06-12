/**
 * Module-health types. Mirrors `/api/repos/{id}/modules/health`.
 *
 * A "module" is the top-level path prefix in the existing OwnershipEntry
 * convention — typically a top-level directory.
 */

export interface ModuleHealthOwner {
  name: string;
  email: string | null;
  file_count: number;
  pct: number;
}

export interface ModuleHealthSummary {
  module_path: string;
  file_count: number;
  symbol_count: number;
  hotspot_count: number;
  dead_code_count: number;
  dead_code_lines: number;
  avg_churn_percentile: number;
  median_bus_factor: number;
  min_bus_factor: number;
  primary_owner: string | null;
  primary_owner_pct: number;
  is_silo: boolean;
  decision_count: number;
  doc_coverage_pct: number;
  /** 0–100 — higher is healthier. */
  health_score: number;
}

export interface ModuleGoverningDecision {
  id: string;
  title: string;
  status: string;
}

export interface ModuleHealthDetail extends ModuleHealthSummary {
  owners: ModuleHealthOwner[];
  top_hotspots: string[];
  /** Title-bearing refs from current backends; bare ids from older ones. */
  governing_decisions: (ModuleGoverningDecision | string)[];
  contributor_count: number;
}

export interface ReviewerSuggestion {
  name: string;
  email: string | null;
  score: number;
  recent_commits: number;
  owned_paths: string[];
  co_change_paths: string[];
  reasons: string[];
}
