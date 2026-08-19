/**
 * Data contract for the repo Stats ("By the Numbers") page.
 *
 * Mirrors the payload from `GET /api/repos/{repo_id}/stats/highlights`
 * (packages/server/.../routers/stats.py). Scope is defined by subtraction:
 * only signals no other page in the app already owns. Health scores belong to
 * Code Health, commit volume and categories to Commits, per-person ownership to
 * Contributors, dependencies and communities to Architecture — none of those
 * appear here.
 *
 * Every section is independently built server-side and degrades to null/empty
 * rather than failing the page, so most leaf fields are nullable.
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
  module_count: number;
  total_nloc: number;
  language_count: number;
  languages: StatsLanguage[];
  size_class: StatsSizeClass;
}

/** The repo's founding moment — its opening line, not just a date. */
export interface StatsOrigin {
  first_commit_at: string | null;
  /** Founding author (root commit). Null for older indexes / non-git repos. */
  first_commit_author: string | null;
  /** The root commit's subject. Null until an index captures it. */
  first_commit_subject: string | null;
  last_commit_at: string | null;
  age_days: number | null;
  total_commits: number;
  contributor_count: number;
}

/**
 * Lifetime lines written vs. taken back.
 *
 * Read from repo-level totals captured at index time, never summed from the
 * commit table — that table is bounded to the newest N commits, so summing it
 * would present a windowed figure as a lifetime one. Null when the history was
 * too deep to walk or the index predates the capture.
 */
export interface StatsChurn {
  lines_added: number;
  lines_deleted: number;
  net: number;
  /** Lines deleted per hundred written. The shareable ratio. */
  deleted_per_hundred: number;
}

/**
 * Coding-rhythm heatmap: commit counts by weekday (0=Monday) x hour (0-23).
 *
 * `timezone_mode` says which clock the matrix is drawn in. `author_local` means
 * every commit was shifted by its author's own UTC offset — the honest version
 * of "when do people work". `utc` is the fallback for indexes written before
 * the offset was captured; it resolves itself on the next `repowise update`.
 */
export interface StatsPunchCard {
  /** 7 rows (Mon..Sun) x 24 columns (hours). */
  matrix: number[][];
  /** Single hottest weekday/hour cell, or null when there are no commits. */
  peak: { weekday: number; hour: number; count: number } | null;
  /** Weekday (0=Mon) and hour with the most commits by marginal total. */
  busiest_weekday: number | null;
  peak_hour: number | null;
  total: number;
  timezone_mode: "author_local" | "utc";
}

/** Commit momentum: the 90 days ending at the newest commit vs the 90 before. */
export interface StatsVelocity {
  recent_90d: number;
  prior_90d: number;
  /** Percent change recent-vs-prior. Null when the prior window is empty. */
  pct_change: number | null;
}

/** The time-shape of the work. Nothing else in the app has a clock. */
export interface StatsRhythm {
  punch_card: StatsPunchCard;
  velocity: StatsVelocity;
  busiest_month: { month: string; total: number } | null;
  busiest_day: { date: string; commits: number } | null;
  /** Longest run of consecutive days with at least one commit. */
  longest_streak: { days: number; start: string; end: string } | null;
  /** Distinct calendar days carrying at least one commit. */
  active_days: number;
  /** Median days since each file was last touched, anchored to the newest
   *  commit rather than now, so a stale index doesn't inflate it. */
  code_half_life_days: number | null;
}

/** A contributor's commit-hour habit. Only emitted in author-local mode —
 *  awarding "night owl" off UTC would just name whoever lives furthest east. */
export interface StatsChronotype {
  name: string;
  commits: number;
  peak_hour: number;
  label: "night_owl" | "early_bird" | "daylight";
  night_pct: number;
  early_pct: number;
  /** This person's own 24-hour commit histogram. */
  hour_commits: number[];
  /** This person's own weekday histogram, 0=Monday. Named client-side because
   *  which days count as the weekend is the reader's preference, not the
   *  server's. Older payloads omit both arrays; naming degrades to null. */
  weekday_commits: number[];
}

export interface StatsArrival {
  name: string;
  first_commit_at: string | null;
}

/**
 * Repo-level concentration only. Per-person detail (ownership share, hotspots
 * owned, dead-code burden) is the Contributors page's job.
 */
export interface StatsPeople {
  owner_count: number;
  contributor_count: number;
  single_owner_files: number;
  silo_count: number;
  /** Fewest primary owners who together hold >50% of owned files. 1 means a
   *  single person owns most of the codebase. Null when no ownership data. */
  truck_factor: number | null;
  chronotypes: StatsChronotype[];
  /** Contributors ordered by their first commit — the arrivals timeline. */
  arrivals: StatsArrival[];
}

/**
 * The superlatives. Several are the only headline treatment their underlying
 * signal gets anywhere: max CCN is otherwise a sortable column, and import
 * cycles have no other UI at all.
 */
export interface StatsRecords {
  largest_file?: { path: string; nloc: number };
  /** Highest cyclomatic complexity in any single file. */
  gnarliest_file?: { path: string; max_ccn: number };
  most_complex_symbol?: { name: string; file_path: string; complexity: number };
  most_changed_file?: { path: string; commit_count: number };
  oldest_file?: { path: string; first_commit_at: string | null };
  /** `import_count` present when graph metrics were materialized — the award
   *  is then "most imported"; without it, it degrades to the PageRank pick. */
  most_central_file?: { path: string; pagerank: number; import_count?: number };
  strongest_coupling?: { a: string; b: string; count: number };
  /** Biggest strongly-connected import cycle, and how many exist. */
  largest_cycle?: { files: number; cycle_count: number };
  /** What fraction of this codebase is async, and what fraction is documented. */
  symbol_shape?: {
    total: number;
    async_count: number;
    async_pct: number;
    documented_count: number;
    documented_pct: number;
  };
  /** Largest non-initial commit by churn (added + deleted lines). */
  biggest_commit?: {
    sha: string;
    subject: string;
    lines_changed: number;
    files_changed: number;
  };
  /** Non-initial commit touching the most files. */
  widest_commit?: {
    sha: string;
    subject: string;
    files_changed: number;
    lines_changed: number;
  };
}

export interface StatsRepo {
  id: string;
  name: string;
}

export interface StatsHighlights {
  repo: StatsRepo;
  scale: StatsScale;
  origin: StatsOrigin;
  /** Null when lifetime churn could not be captured. */
  churn: StatsChurn | null;
  rhythm: StatsRhythm;
  people: StatsPeople;
  records: StatsRecords;
}
