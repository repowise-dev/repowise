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
  co_authors: OwnerCoAuthor[];
  commit_categories: Record<string, number>;
}
