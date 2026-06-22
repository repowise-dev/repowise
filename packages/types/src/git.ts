/**
 * Canonical git-intelligence types — hotspots, ownership, bus factor, summary.
 *
 * Canonical source: engine `PipelineResult.git_metadata` (per-file) and
 * the rollups produced by `packages/web` UI. Downstream pipelines that emit
 * a different key (e.g. `path` instead of `file_path`) must rename via an
 * adapter before passing data to components.
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
  /** Hassan change entropy (History Complexity Metric) — decay-weighted commit scatter. */
  change_entropy?: number;
  /** Repo-wide percentile rank of change_entropy, 0–100. */
  change_entropy_pct?: number;
  /** Bug-fix commits touching this file in the trailing defect window. */
  prior_defect_count?: number;
  /** The file's path before its most recent rename, if any. */
  original_path?: string | null;
  /** True when the per-file commit-history cap was hit during indexing. */
  commit_count_capped?: boolean;
}

export interface Hotspot {
  file_path: string;
  commit_count_total?: number;
  commit_count_90d: number;
  commit_count_30d: number;
  churn_percentile: number;
  temporal_hotspot_score?: number | null;
  primary_owner: string | null;
  /** Share of all-time commits attributable to the primary owner, 0–1. */
  primary_owner_commit_pct?: number | null;
  /** Top author in the last 90 days; may differ from primary_owner on legacy code. */
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
  /** True when the per-file commit-history cap was hit during indexing — i.e. older history exists but was not analysed. */
  commit_count_capped?: boolean;
  age_days?: number;
  last_commit_at?: string | null;
  /** Hassan change entropy (History Complexity Metric) — decay-weighted commit scatter. */
  change_entropy?: number;
  /** Repo-wide percentile rank of change_entropy, 0–100. */
  change_entropy_pct?: number;
  /** Bug-fix commits touching this file in the trailing defect window. */
  prior_defect_count?: number;
  /** The file's path before its most recent rename, if any. */
  original_path?: string | null;
}

/** Repo-relative review priority derived from the score's percentile within
 * its own repo (terciles) — portable, unlike the absolute calibration band. */
export type ReviewPriority = "low" | "moderate" | "high";

export interface Commit {
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
  /** Raw 0–10 change-risk score from the calibrated model (stored). */
  change_risk_score: number | null;
  /** Absolute calibration band — kept for transparency, but skews high on
   * repos with large typical commits; prefer {@link review_priority}. */
  change_risk_level: ReviewPriority | null;
  /** Where this commit's score sits within its repo's distribution, 0–100. */
  risk_percentile: number;
  /** Repo-relative review priority (the portable ranking signal). */
  review_priority: ReviewPriority;
  /** Label of the dominant risk driver, so rows explain themselves without
   * opening the detail sheet. Null when the commit was never risk-scored. */
  top_driver?: string | null;
  /** Author's cumulative prior-commit count at the time of the commit.
   * Low values flag a new-to-this-repo contributor. */
  author_experience?: number | null;
  /** Coding-agent attribution (deterministic local-git channels).
   * Null/undefined for human-authored commits. */
  agent_name?: string | null;
  /** 1 = near-autonomous bot, 2 = human-driven agent, 3 = assisted/co-authored. */
  agent_autonomy_tier?: number | null;
  agent_confidence?: string | null;
}

/** One feature's signed contribution to a commit's change-risk logit. */
export interface RiskDriver {
  feature: string;
  value: number | null;
  /** Signed push on the logit; positive raises risk, negative lowers it. */
  contribution: number;
  label: string;
}

export interface CommitDetail extends Commit {
  /** Per-feature breakdown, strongest contribution first. */
  drivers: RiskDriver[];
  /** Which attribution channel identified the agent (e.g. git footer). */
  agent_channel?: string | null;
}

/** One month of agent-vs-human commit volume. */
export interface AgentTrendBucket {
  month: string; // "YYYY-MM"
  total_commits: number;
  agent_commits: number;
  agent_pct: number; // 0-100
  tier_counts: Record<string, number>;
}

/** Monthly agent-share trend across the indexed commit window. */
export interface AgentTrend {
  buckets: AgentTrendBucket[];
  total_commits: number;
  agent_commits: number;
  agent_pct: number; // 0-100
  agent_names: { name: string; count: number }[];
}

/** Canonical commit-category labels for the Code Evolution timeline. */
export type CommitCategory =
  | "feature"
  | "fix"
  | "refactor"
  | "docs"
  | "test"
  | "deps"
  | "chore"
  | "other";

/** One time bucket of commit-category counts. */
export interface CommitEvolutionBucket {
  period: string; // "YYYY-MM" (monthly) or "YYYY-Wnn" (weekly)
  start: string; // ISO date of the bucket's first day
  total: number;
  counts: Partial<Record<CommitCategory, number>>;
}

/** Commit-category mix over time — the repo's development "story arc". */
export interface CommitEvolution {
  buckets: CommitEvolutionBucket[];
  categories: CommitCategory[]; // present across the window, canonical order
  totals: Partial<Record<CommitCategory, number>>;
  total_commits: number;
  granularity: "month" | "week";
  first_commit_at: string | null;
  last_commit_at: string | null;
}

/** Repo-wide commit aggregates (over all commits, not the loaded page). */
export interface CommitStats {
  total_commits: number;
  high_priority_count: number;
  fix_commit_count: number;
  agent_commit_count: number;
  avg_entropy: number;
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
