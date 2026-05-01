/**
 * Canonical git-intelligence types — hotspots, ownership, bus factor, summary.
 *
 * Canonical source: OSS engine `PipelineResult.git_metadata` (per-file) and
 * the rollups produced by `packages/web` UI. Hosted backend's `HotspotEntry`
 * uses `path` instead of `file_path`; hosted-side adapters rename.
 */

export interface FileAuthor {
  name: string;
  email: string;
  commit_count: number;
  pct: number;
}

export interface SignificantCommit {
  sha: string;
  date: string;
  message: string;
  author: string;
}

export interface CoChangePartner {
  file_path: string;
  co_change_count: number;
}

export interface GitMetadata {
  file_path: string;
  commit_count_total: number;
  commit_count_90d: number;
  commit_count_30d: number;
  first_commit_at: string | null;
  last_commit_at: string | null;
  primary_owner_name: string | null;
  primary_owner_email: string | null;
  primary_owner_commit_pct: number | null;
  recent_owner_name: string | null;
  recent_owner_commit_pct: number | null;
  top_authors: FileAuthor[];
  significant_commits: SignificantCommit[];
  co_change_partners: CoChangePartner[];
  is_hotspot: boolean;
  is_stable: boolean;
  churn_percentile: number;
  age_days: number;
  bus_factor: number;
  contributor_count: number;
  lines_added_90d: number;
  lines_deleted_90d: number;
  avg_commit_size: number;
  commit_categories: Record<string, number>;
  merge_commit_count_90d: number;
  test_gap?: boolean | null;
  /** v0.2.0+ — combined recency × churn × ownership score. Order hotspots by this when present. */
  temporal_hotspot_score?: number | null;
}

export interface Hotspot {
  file_path: string;
  commit_count_90d: number;
  commit_count_30d: number;
  churn_percentile: number;
  temporal_hotspot_score?: number | null;
  primary_owner: string | null;
  is_hotspot: boolean;
  is_stable: boolean;
  bus_factor: number;
  contributor_count: number;
  lines_added_90d: number;
  lines_deleted_90d: number;
  avg_commit_size: number;
  commit_categories: Record<string, number>;
}

export interface OwnershipEntry {
  module_path: string;
  primary_owner: string | null;
  owner_pct: number | null;
  file_count: number;
  is_silo: boolean;
}

export interface TopOwner {
  name: string;
  email?: string;
  file_count: number;
  pct: number;
}

export interface GitSummary {
  total_files: number;
  hotspot_count: number;
  stable_count: number;
  average_churn_percentile: number;
  top_owners: TopOwner[];
}

export interface BusFactorEntry {
  path: string;
  bus_factor: number;
  top_author: string;
  top_author_pct: number;
  contributor_count: number;
}

export interface BusFactor {
  files: BusFactorEntry[];
  overall_bus_factor: number;
}
