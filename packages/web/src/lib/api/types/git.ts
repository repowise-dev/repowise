// ---------------------------------------------------------------------------
// Git Intelligence
// ---------------------------------------------------------------------------

export interface GitMetadataResponse {
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
  top_authors: Array<{ name: string; email: string; commit_count: number; pct: number }>;
  significant_commits: Array<{ sha: string; date: string; message: string; author: string }>;
  co_change_partners: Array<{ file_path: string; co_change_count: number }>;
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
}

export interface HotspotResponse {
  file_path: string;
  commit_count_total?: number;
  commit_count_90d: number;
  commit_count_30d: number;
  churn_percentile: number;
  temporal_hotspot_score?: number | null;
  primary_owner: string | null;
  primary_owner_commit_pct?: number | null;
  recent_owner_name?: string | null;
  recent_owner_commit_pct?: number | null;
  is_hotspot: boolean;
  is_stable: boolean;
  bus_factor: number;
  contributor_count: number;
  lines_added_90d: number;
  lines_deleted_90d: number;
  avg_commit_size: number;
  commit_categories: Record<string, number>;
  merge_commit_count_90d?: number;
  commit_count_capped?: boolean;
  age_days?: number;
  last_commit_at?: string | null;
}

export interface OwnershipEntry {
  module_path: string;
  primary_owner: string | null;
  owner_pct: number | null;
  file_count: number;
  is_silo: boolean;
}

export interface GitSummaryResponse {
  total_files: number;
  hotspot_count: number;
  stable_count: number;
  average_churn_percentile: number;
  top_owners: Array<{ name: string; email?: string; file_count: number; pct: number }>;
}
