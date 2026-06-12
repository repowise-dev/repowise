/**
 * Owner / contributor profile types — engineering-leader view of who does
 * what in the codebase. Mirrors the Pydantic models served by
 * `/api/repos/{id}/owners` and `/api/repos/{id}/owners/{key}`.
 */

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
  /** Share of this module's files owned by this person (0–1). */
  dominant_pct: number;
}

export interface OwnerFileEntry {
  file_path: string;
  commit_count_90d: number;
  /** 0–100 percentile rank. */
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
  /** Shared-files / total-files-touched (0–1). */
  co_change_strength: number;
}

export interface OwnerProfile {
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
  /** Estimated, not exact — see Pydantic doc. */
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
  /** Agent activity on this person's owned files; null when none attributed. */
  agent_collab?: OwnerAgentCollab | null;
}

/** How much coding-agent activity lands on the files this person owns. */
export interface OwnerAgentCollab {
  files_with_agent_commits: number;
  agent_commit_count: number;
  /** Commit-weighted agent share across owned files, 0–100; null when no
   * owned file has a provenance-aware rollup yet. */
  agent_share_pct: number | null;
  tier_counts: Record<string, number>;
}
