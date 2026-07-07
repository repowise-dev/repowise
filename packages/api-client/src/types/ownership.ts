// ---------------------------------------------------------------------------
// Owner / contributor profile
// ---------------------------------------------------------------------------

export interface OwnerListEntry {
  key: string;
  name: string;
  email: string | null;
  files_owned: number;
  hotspots_owned: number;
  silo_modules: number;
  dead_code_files_owned: number;
  dead_code_lines_owned: number;
  commit_count_90d: number;
  last_commit_at: string | null;
  bus_factor_risk_files: number;
}

export interface OwnerModuleRollup {
  module_path: string;
  file_count: number;
  hotspot_count: number;
  dominant_pct: number;
}

export interface OwnerFileEntry {
  file_path: string;
  commit_count_90d: number;
  churn_percentile: number;
  bus_factor: number;
  is_hotspot: boolean;
  last_commit_at: string | null;
  primary_owner_commit_pct: number | null;
}

export interface OwnerCoAuthor {
  name: string;
  email: string | null;
  shared_files: number;
  co_change_strength: number;
}

export interface OwnerProfileResponse {
  key: string;
  name: string;
  email: string | null;
  files_owned: number;
  hotspots_owned: number;
  silo_modules: number;
  dead_code_files_owned: number;
  dead_code_lines_owned: number;
  commit_count_90d: number;
  last_commit_at: string | null;
  first_commit_at: string | null;
  bus_factor_risk_files: number;
  lines_added_90d_est: number;
  lines_deleted_90d_est: number;
  modules: OwnerModuleRollup[];
  top_files: OwnerFileEntry[];
  /** Uncapped count behind the top_files slice (for "+N more"). */
  files_touched_total?: number;
  co_authors: OwnerCoAuthor[];
  /** Uncapped count behind the co_authors slice (for "+N more"). */
  co_authors_total?: number;
  commit_categories: Record<string, number>;
  /** Agent activity on owned files; null when nothing attributed. */
  agent_collab?: OwnerAgentCollab | null;
}

export interface OwnerAgentCollab {
  files_with_agent_commits: number;
  agent_commit_count: number;
  agent_share_pct: number | null;
  tier_counts: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Module health
// ---------------------------------------------------------------------------

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
  health_score: number;
}

export interface ModuleHealthDetail extends ModuleHealthSummary {
  owners: ModuleHealthOwner[];
  top_hotspots: string[];
  governing_decisions: string[];
  contributor_count: number;
}

// ---------------------------------------------------------------------------
// Reviewer suggestions
// ---------------------------------------------------------------------------

export interface ReviewerSuggestion {
  name: string;
  email: string | null;
  score: number;
  recent_commits: number;
  owned_paths: string[];
  co_change_paths: string[];
  reasons: string[];
}

export interface ReviewerSuggestionsResponse {
  paths: string[];
  suggestions: ReviewerSuggestion[];
}
