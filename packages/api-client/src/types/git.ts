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
  change_entropy?: number;
  change_entropy_pct?: number;
  prior_defect_count?: number;
  /** `symbol_id` -> counted fixes that landed in it, same window as
   *  `prior_defect_count`. Approximate: symbol spans are current-tree while
   *  each fix's ranges are numbered on its own parent commit. */
  fix_symbol_counts?: Record<string, number>;
  bug_magnet?: boolean;
  last_fix_at?: string | null;
  temporal_hotspot_score?: number | null;
  commit_count_capped?: boolean;
  original_path?: string | null;
  test_gap?: boolean | null;
  // Agent-provenance rollup (deterministic local-git channels). The pct is
  // null/absent for indexes built before the provenance-aware walk.
  agent_commit_count?: number;
  agent_authored_pct?: number | null;
  agent_tier_counts?: Record<string, number>;
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
  change_entropy?: number;
  change_entropy_pct?: number;
  prior_defect_count?: number;
  original_path?: string | null;
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

export type ReviewPriority = "low" | "moderate" | "high";

export interface CommitResponse {
  sha: string;
  short_sha: string;
  author_name: string;
  author_email: string;
  committed_at: string | null;
  subject: string;
  lines_added: number;
  lines_deleted: number;
  files_changed: number;
  dirs_changed: number;
  subsystems_changed: number;
  entropy: number;
  is_fix: boolean;
  change_risk_score: number | null;
  change_risk_level: ReviewPriority | null;
  risk_percentile: number;
  review_priority: ReviewPriority;
  /** Label of the dominant risk driver; null when never risk-scored. */
  top_driver?: string | null;
  /** Author's cumulative prior-commit count at commit time. */
  author_experience?: number | null;
  /** Commits by this author across the indexed history (identities folded). */
  author_commit_count?: number | null;
  /** Coding-agent attribution; null for human-authored commits. */
  agent_name?: string | null;
  /** 1 = near-autonomous bot, 2 = human-driven agent, 3 = assisted. */
  agent_autonomy_tier?: number | null;
  agent_confidence?: string | null;
}

export interface RiskDriverResponse {
  feature: string;
  value: number | null;
  contribution: number;
  label: string;
}

export interface CommitDetailResponse extends CommitResponse {
  drivers: RiskDriverResponse[];
  agent_channel?: string | null;
}

export interface AgentTrendBucket {
  month: string;
  total_commits: number;
  agent_commits: number;
  agent_pct: number;
  tier_counts: Record<string, number>;
}

export interface AgentTrend {
  buckets: AgentTrendBucket[];
  total_commits: number;
  agent_commits: number;
  agent_pct: number;
  agent_names: { name: string; count: number }[];
}

export type CommitCategory =
  | "feature"
  | "fix"
  | "refactor"
  | "docs"
  | "test"
  | "deps"
  | "chore"
  | "other";

export interface CommitEvolutionBucket {
  period: string;
  start: string;
  total: number;
  counts: Partial<Record<CommitCategory, number>>;
}

export interface CommitEvolution {
  buckets: CommitEvolutionBucket[];
  categories: CommitCategory[];
  totals: Partial<Record<CommitCategory, number>>;
  total_commits: number;
  granularity: "month" | "week";
  first_commit_at: string | null;
  last_commit_at: string | null;
}

/** One bin of the repo's raw change-risk score distribution. */
export interface RiskHistogramBucket {
  /** Bin lower bound on the 0-10 raw score axis (inclusive). */
  start: number;
  /** Bin upper bound (exclusive, except the final bin). */
  end: number;
  count: number;
}

/** Repo-wide commit aggregates (computed over all commits, not the loaded page). */
export interface CommitStats {
  total_commits: number;
  high_priority_count: number;
  fix_commit_count: number;
  agent_commit_count: number;
  avg_entropy: number;
  /** Binned on the raw score, not the percentile — percentile ranks are
   * uniform by construction, so only the raw axis has a shape to draw. */
  risk_histogram?: RiskHistogramBucket[];
  /** Raw score at the low/moderate tercile boundary. */
  moderate_cut?: number | null;
  /** Raw score at the moderate/high boundary — the review-priority line. */
  high_cut?: number | null;
}
