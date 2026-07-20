/**
 * Data contract for the repo Stats ("By the Numbers") page.
 *
 * Mirrors the payload from `GET /api/repos/{repo_id}/stats/highlights`
 * (packages/server/.../routers/stats.py). Every section is independently
 * built server-side and degrades to null/empty rather than failing the page,
 * so most leaf fields are nullable.
 */

export interface StatsSizeClass {
  name: string;
  blurb: string;
  nloc: number;
}

export interface StatsLanguage {
  language: string;
  file_count: number;
}

export interface StatsScale {
  file_count: number;
  symbol_count: number;
  entry_point_count: number;
  module_count: number;
  total_nloc: number;
  language_count: number;
  languages: StatsLanguage[];
  size_class: StatsSizeClass;
}

export interface StatsMonthlyBucket {
  month: string;
  total: number;
  agent: number;
}

export interface StatsAgentName {
  name: string;
  count: number;
}

/** Coding-rhythm heatmap: commit counts by weekday (0=Monday) x hour (0-23,
 *  in the stored UTC), with the human-readable hooks the hero renders. */
export interface StatsPunchCard {
  /** 7 rows (Mon..Sun) x 24 columns (hours). */
  matrix: number[][];
  /** Single hottest weekday/hour cell, or null when there are no commits. */
  peak: { weekday: number; hour: number; count: number } | null;
  /** Weekday (0=Mon) and hour with the most commits by marginal total. */
  busiest_weekday: number | null;
  peak_hour: number | null;
  total: number;
}

/** Commit momentum: the 90 days ending at the newest commit vs the 90 before. */
export interface StatsVelocity {
  recent_90d: number;
  prior_90d: number;
  /** Percent change recent-vs-prior. Null when the prior window is empty. */
  pct_change: number | null;
}

/** Calibrated just-in-time change-risk tally across the sampled commits. */
export interface StatsChangeRiskMix {
  low: number;
  moderate: number;
  high: number;
}

export interface StatsActivity {
  total_commits: number;
  agent_commits: number;
  agent_pct: number;
  fix_commits: number;
  fix_pct: number;
  contributor_count: number;
  first_commit_at: string | null;
  /** Founding author (root commit). Null for older indexes / non-git repos. */
  first_commit_author: string | null;
  last_commit_at: string | null;
  age_days: number | null;
  busiest_month: StatsMonthlyBucket | null;
  monthly: StatsMonthlyBucket[];
  agent_names: StatsAgentName[];
  punch_card: StatsPunchCard;
  velocity: StatsVelocity;
  change_risk_mix: StatsChangeRiskMix;
}

export interface StatsOwner {
  name: string;
  file_count: number;
  pct: number;
}

export interface StatsPeople {
  owner_count: number;
  top_owners: StatsOwner[];
  single_owner_files: number;
  silo_count: number;
  /** Fewest primary owners who together hold >50% of owned files. 1 means a
   *  single person owns most of the codebase. Null when no ownership data. */
  truck_factor: number | null;
}

export interface StatsSeverityBreakdown {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

export interface StatsDefectAccuracy {
  k: number;
  hits: number;
  precision: number;
  base_rate: number;
  lift: number | null;
  window_days: number;
  scored_files: number;
  defect_files: number;
}

export interface StatsDistributionBand {
  files: number;
  nloc: number;
  pct: number;
}

export interface StatsDistribution {
  total_files: number;
  total_nloc: number;
  bands: {
    healthy: StatsDistributionBand;
    warning: StatsDistributionBand;
    alert: StatsDistributionBand;
  };
}

export interface StatsDeadCode {
  total_findings: number;
  deletable_lines: number;
}

export interface StatsQuality {
  average_health: number | null;
  maintainability_average: number | null;
  performance_average: number | null;
  worst_performer_path: string | null;
  worst_performer_score: number | null;
  open_findings: number;
  severity_breakdown: StatsSeverityBreakdown;
  defect_accuracy: StatsDefectAccuracy | null;
  distribution: StatsDistribution | null;
  doc_coverage_pct: number;
  page_count: number;
  test_coverage_pct: number | null;
  dead_code: StatsDeadCode;
}

export interface StatsKnowledge {
  decision_count: number;
  active_decision_count: number;
}

export interface StatsSuperlatives {
  largest_file?: { path: string; nloc: number };
  most_complex_symbol?: { name: string; file_path: string; complexity: number };
  most_changed_file?: { path: string; commit_count: number };
  oldest_file?: { path: string; first_commit_at: string | null };
  /** `import_count` present when graph metrics were materialized — the award
   *  is then "most imported"; without it, it degrades to the PageRank pick. */
  most_central_file?: { path: string; pagerank: number; import_count?: number };
  strongest_coupling?: { a: string; b: string; count: number };
  /** Largest non-initial commit by churn (added + deleted lines). */
  biggest_commit?: {
    sha: string;
    subject: string;
    lines_changed: number;
    files_changed: number;
  };
  /** Longest run of consecutive days with at least one commit (UTC dates). */
  longest_streak?: { days: number; start: string; end: string };
}

export interface StatsEcosystem {
  name: string;
  count: number;
}

export interface StatsDependencies {
  total: number;
  runtime: number;
  dev: number;
  ecosystems: StatsEcosystem[];
}

/** Import-graph structure: dependency cycles and natural communities, read
 *  from the materialized graph snapshot (no rebuild). */
export interface StatsGraph {
  /** Strongly-connected components with >1 member — circular import clusters. */
  cycle_clusters: number;
  files_in_cycles: number;
  largest_cycle: number;
  community_count: number;
}

/** The knowledge base's own build cost — the wiki bragging about itself. */
export interface StatsBuild {
  page_count: number;
  total_tokens: number;
  cost_usd: number;
  llm_operations: number;
}

export interface StatsRepo {
  id: string;
  name: string;
  default_branch: string;
  head_commit: string | null;
}

export interface StatsHighlights {
  repo: StatsRepo;
  scale: StatsScale;
  activity: StatsActivity;
  people: StatsPeople;
  quality: StatsQuality;
  knowledge: StatsKnowledge;
  /** Optional so older payloads (hosted exporters) degrade gracefully. */
  dependencies?: StatsDependencies | null;
  /** Optional so older payloads (hosted exporters) degrade gracefully. */
  graph?: StatsGraph | null;
  /** Optional so older payloads (hosted exporters) degrade gracefully. */
  build?: StatsBuild | null;
  superlatives: StatsSuperlatives;
}
