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

export interface StatsActivity {
  total_commits: number;
  agent_commits: number;
  agent_pct: number;
  fix_commits: number;
  fix_pct: number;
  contributor_count: number;
  first_commit_at: string | null;
  last_commit_at: string | null;
  age_days: number | null;
  busiest_month: StatsMonthlyBucket | null;
  monthly: StatsMonthlyBucket[];
  agent_names: StatsAgentName[];
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
  most_central_file?: { path: string; pagerank: number };
  strongest_coupling?: { a: string; b: string; count: number };
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
  superlatives: StatsSuperlatives;
}
