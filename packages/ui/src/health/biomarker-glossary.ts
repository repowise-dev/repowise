/**
 * Biomarker glossary — single source of truth for the human-readable
 * label, category, and short explanation used in tooltips, info popovers,
 * and grouped views across all three health pages.
 *
 * Keep in sync with ``packages/core/src/repowise/core/analysis/health/scoring.py``
 * (the python ``_BIOMARKER_CATEGORY`` map) and ``biomarkers/registry.py``.
 */

export type BiomarkerCategory =
  | "structural_complexity"
  | "size_and_complexity"
  | "duplication"
  | "test_coverage"
  | "test_coverage_gradient"
  | "test_quality"
  | "error_handling"
  | "performance"
  | "organizational";

export interface BiomarkerInfo {
  label: string;
  category: BiomarkerCategory;
  description: string;
}

export const CATEGORY_LABEL: Record<BiomarkerCategory, string> = {
  structural_complexity: "Structural complexity",
  size_and_complexity: "Size & complexity",
  duplication: "Duplication",
  test_coverage: "Test coverage",
  test_coverage_gradient: "Coverage gradient",
  test_quality: "Test quality",
  error_handling: "Error handling",
  performance: "Performance",
  organizational: "Organizational",
};

export const CATEGORY_CAP: Record<BiomarkerCategory, number> = {
  organizational: 3.5,
  structural_complexity: 2.5,
  test_coverage: 2.0,
  test_coverage_gradient: 2.0,
  size_and_complexity: 1.5,
  duplication: 1.0,
  test_quality: 0.5,
  error_handling: 0.5,
  // One bounded performance category cap (mirrors `_PERFORMANCE_CATEGORY_CAPS`
  // in scoring.py): the whole performance pillar deducts at most 1.0, keeping it
  // advisory.
  performance: 1.0,
};

export const BIOMARKER_GLOSSARY: Record<string, BiomarkerInfo> = {
  brain_method: {
    label: "Brain method",
    category: "structural_complexity",
    description:
      "A function that knows too much — high cyclomatic complexity, many parameters, and deep nesting all at once. Hard to test, easy to break.",
  },
  nested_complexity: {
    label: "Nested complexity",
    category: "structural_complexity",
    description:
      "Deeply nested control flow (≥4 levels). Cognitive load grows non-linearly with nesting; flatten with early returns or extracted helpers.",
  },
  bumpy_road: {
    label: "Bumpy road",
    category: "structural_complexity",
    description:
      "A function with multiple shallow complexity bumps stitched together. No single block is bad, but the whole reads as a sequence of mini-functions.",
  },
  complex_method: {
    label: "Complex method",
    category: "size_and_complexity",
    description:
      "Cyclomatic complexity above the language threshold. Many independent paths through one function.",
  },
  large_method: {
    label: "Large method",
    category: "size_and_complexity",
    description:
      "A function with too many non-comment lines of code. Even simple logic gets hard to hold in your head past a point.",
  },
  primitive_obsession: {
    label: "Primitive obsession",
    category: "size_and_complexity",
    description:
      "Many primitive parameters where a domain object would carry the same data. Calls become positional and easy to mismatch.",
  },
  dry_violation: {
    label: "DRY violation",
    category: "duplication",
    description:
      "Code blocks duplicated across files. Ranked by co-change frequency — clones that move together are most worth consolidating.",
  },
  untested_hotspot: {
    label: "Untested hotspot",
    category: "test_coverage",
    description:
      "High-churn, centrally depended-on file with no paired test file and low coverage. The riskiest place to leave untested.",
  },
  coverage_gap: {
    label: "Coverage gap",
    category: "test_coverage",
    description:
      "Specific uncovered lines in a file. Surfaced when a coverage report has been ingested.",
  },
  coverage_gradient: {
    label: "Coverage gradient",
    category: "test_coverage_gradient",
    description:
      "A continuous coverage penalty proportional to the uncovered fraction — keeps the score sensitive to coverage even on well-tested files where the binary gates never fire.",
  },
  developer_congestion: {
    label: "Developer congestion",
    category: "organizational",
    description:
      "Multiple authors editing the same file frequently — a coordination cost signal. Often points to an unclear module boundary.",
  },
  knowledge_loss: {
    label: "Knowledge loss",
    category: "organizational",
    description:
      "Files whose primary author has reduced or stopped contributing — a bus-factor warning.",
  },
  hidden_coupling: {
    label: "Hidden coupling",
    category: "organizational",
    description:
      "Two files co-change in git history but have no explicit import between them. The implicit contract is invisible at the source level, so changes slip out of sync and break in production.",
  },
  complex_conditional: {
    label: "Complex conditional",
    category: "structural_complexity",
    description:
      "A boolean expression stitching three or more operators together. Compound conditions like these usually encode two policies fighting for one line and are easy to misread under pressure.",
  },
  function_hotspot: {
    label: "Function hotspot",
    category: "organizational",
    description:
      "A single function concentrating an outsized share of the file's churn while carrying real structural complexity. Defects accumulate where modification frequency and complexity collide.",
  },
  code_age_volatility: {
    label: "Code age volatility",
    category: "organizational",
    description:
      "A long-stable function (median line age ≥ 1 year) that has suddenly started moving again. This edit profile is one of the strongest empirical predictors of regressions.",
  },
  low_cohesion: {
    label: "Low cohesion",
    category: "structural_complexity",
    description:
      "A class whose methods split into multiple disconnected groups (LCOM4 > 1). The groups share a namespace but not a responsibility — usually two classes living in one.",
  },
  god_class: {
    label: "God class",
    category: "structural_complexity",
    description:
      "A very large class with many methods including at least one brain method. It accumulates responsibilities until every change routes through it.",
  },
  ownership_risk: {
    label: "Ownership risk",
    category: "organizational",
    description:
      "Many minor contributors with no dominant owner. Fragmented ownership is a calibrated defect predictor — nobody holds the full picture of the file.",
  },
  churn_risk: {
    label: "Churn risk",
    category: "organizational",
    description:
      "Lines added and deleted at a rate far above the repo norm for the file's size. Relative churn is a classic defect-density predictor.",
  },
  change_entropy: {
    label: "Change entropy",
    category: "organizational",
    description:
      "Changes scattered across many unrelated commits rather than focused work. High entropy in the change history is a strong history-based fault predictor.",
  },
  co_change_scatter: {
    label: "Co-change scatter",
    category: "organizational",
    description:
      "Editing this file tends to ripple across many other files in the same commits (shotgun surgery). The strongest calibrated predictor in the score.",
  },
  prior_defect: {
    label: "Prior defects",
    category: "organizational",
    description:
      "Bug-fix commits touched this file repeatedly in the recent window. Recent defect history is the most cost-effective predictor of further defects.",
  },
  large_assertion_block: {
    label: "Large assertion block",
    category: "test_quality",
    description:
      "A test function running a long unbroken run of assertions. When one fails, the rest never execute — split into focused cases.",
  },
  duplicated_assertion_block: {
    label: "Duplicated assertions",
    category: "test_quality",
    description:
      "An assertion block copy-pasted across test files. Behaviour changes now require synchronized edits, and drift produces misleading green runs.",
  },
  error_handling: {
    label: "Error handling",
    category: "error_handling",
    description:
      "Swallowed exceptions, bare excepts, unsafe unwraps, or discarded error returns. An advisory maintainability flag — failures here vanish silently.",
  },
  ungoverned_hotspot: {
    label: "Ungoverned hotspot",
    category: "organizational",
    description:
      "A churn hotspot with no governing architectural decision on record. High-traffic code evolving without documented intent.",
  },
  stale_governance: {
    label: "Stale governance",
    category: "organizational",
    description:
      "The architectural decision governing this file has gone stale — the code has moved on since the decision was last confirmed.",
  },
  contradictory_decision: {
    label: "Contradictory decision",
    category: "organizational",
    description:
      "Two governing decisions on record contradict each other. The file is caught between conflicting documented intents.",
  },
  io_in_loop: {
    label: "I/O in loop",
    category: "performance",
    description:
      "A database call, network request, filesystem read, or subprocess spawn that runs once per loop iteration — the classic N+1. Detected across function boundaries via the call graph, resolved to a classified I/O boundary. A static performance RISK (high precision, low recall), not measured runtime.",
  },
  string_concat_in_loop: {
    label: "String concat in loop",
    category: "performance",
    description:
      "A string built by repeated += inside a loop, which is quadratic in many runtimes (each concat copies the whole accumulated string). Use a buffer + join for linear cost.",
  },
  blocking_sync_in_async: {
    label: "Blocking call in async",
    category: "performance",
    description:
      "A synchronous blocking call (time.sleep, requests.get, subprocess.run) inside an async function blocks the whole event loop, stalling every other coroutine. Mirrors ruff's ASYNC210/230/251.",
  },
  resource_construction_in_loop: {
    label: "Resource built in loop",
    category: "performance",
    description:
      "A heavy I/O client or connection (sqlite3.connect, httpx.Client, boto3.client, new PrismaClient, sql.Open) constructed every loop iteration instead of once. Opens a fresh connection/pool per iteration — connection churn and, for HttpClient, socket exhaustion. Hoist and reuse a single instance.",
  },
  lock_in_loop: {
    label: "Lock in loop",
    category: "performance",
    description:
      "A mutex or lock acquired on every loop iteration (lock.acquire, mu.Lock, synchronized, lock(x){}). Serializes the loop body and concentrates contention. Hoist the lock outside the loop or batch the critical section.",
  },
  serial_await_in_loop: {
    label: "Serial await in loop",
    category: "performance",
    description:
      "An awaited I/O round-trip run one-at-a-time inside a loop. When the iterations are independent, fan them out with gather / Promise.all / Task.WhenAll for concurrent execution. Advisory — a static analyzer cannot prove the iterations are independent.",
  },
  membership_test_against_list_in_loop: {
    label: "List membership in loop",
    category: "performance",
    description:
      "Testing `x in big_list` (or big_list.includes(x)) inside a loop is O(n·m); a set makes each lookup O(1), turning the loop linear. Only fires when the right operand is provably a list, never a set or dict.",
  },
  nested_loop_with_io: {
    label: "I/O in nested loop",
    category: "performance",
    description:
      "A database / network / filesystem / subprocess call in the inner body of a nested loop — O(n·m) round-trips, the quadratic cousin of I/O-in-loop. The nesting raises confidence it is real, so it surfaces alongside io_in_loop. Batch the inner query or restructure the loops.",
  },
  hot_path_sync_io: {
    label: "Blocking I/O on a hot path",
    category: "performance",
    description:
      "A blocking subprocess or filesystem call in a hot, request-reachable function (top call-graph centrality or a churny file), even outside a loop. Its latency is paid on every call through the function. Advisory — a latency signal ranked by centrality, not always a defect.",
  },
  blocking_io_under_lock: {
    label: "Blocking I/O under a lock",
    category: "performance",
    description:
      "A database / network / filesystem / subprocess round-trip reached while a lock is held (a C# lock(){} or Java synchronized(){} block, directly or through a call). Every other thread blocks for the full I/O wait. Do the I/O outside the critical section and take the lock only to mutate shared state.",
  },
  nested_loop_quadratic: {
    label: "Quadratic nested loop",
    category: "performance",
    description:
      "A data-dependent loop nested inside another (O(n^2)) in a hot, central function. Advisory / informational — surfaced only where centrality ranking says it is worth a look; check the inner bound or use a set/map lookup if it is a search.",
  },
};

export function biomarkerInfo(name: string): BiomarkerInfo {
  return (
    BIOMARKER_GLOSSARY[name] ?? {
      label: name.replace(/_/g, " "),
      category: "size_and_complexity",
      description: "",
    }
  );
}

export function biomarkerLabel(name: string): string {
  return biomarkerInfo(name).label;
}

/* ------------------------------------------------------------------ *
 * Health dimensions: which pillar a biomarker "homes" under
 * ------------------------------------------------------------------ */

export type BiomarkerDimension = "defect" | "maintainability" | "performance";

/**
 * The biomarkers whose "home" pillar is maintainability: the smells the defect
 * calibration floors because they don't predict bugs, given a proper home here.
 * Mirror of ``_MAINTAINABILITY_HOME`` in
 * ``packages/core/src/repowise/core/analysis/health/scoring.py``. Every other
 * biomarker (including the structural duals that count toward both dimensions)
 * homes under defect, its primary calibrated role.
 *
 * The server stamps each finding's authoritative ``dimension`` from the same
 * Python source; this set is only the client-side fallback for payloads that
 * omit it, so the two can never disagree on a fresh response.
 */
export const MAINTAINABILITY_HOME_BIOMARKERS: ReadonlySet<string> = new Set([
  "low_cohesion",
  "brain_method",
  "primitive_obsession",
  "dry_violation",
  "error_handling",
]);

/**
 * The biomarkers whose "home" pillar is performance: static performance RISK
 * detectors (I/O-in-loop / N+1, string-concat-in-loop, blocking-sync-in-async).
 * Mirror of ``_PERFORMANCE_HOME`` in ``scoring.py``. Same fallback-only role as
 * the maintainability set above — the server stamps the authoritative dimension.
 */
export const PERFORMANCE_HOME_BIOMARKERS: ReadonlySet<string> = new Set([
  "io_in_loop",
  "string_concat_in_loop",
  "blocking_sync_in_async",
  "resource_construction_in_loop",
  "lock_in_loop",
  "serial_await_in_loop",
  "membership_test_against_list_in_loop",
  "nested_loop_with_io",
  "nested_loop_quadratic",
  "hot_path_sync_io",
  "blocking_io_under_lock",
]);

/**
 * A biomarker's home dimension for display / filtering. Prefer a finding's
 * server-provided `dimension` field where available; this is the fallback when
 * only the biomarker type is known (e.g. a glossary entry).
 */
export function biomarkerDimension(name: string): BiomarkerDimension {
  if (PERFORMANCE_HOME_BIOMARKERS.has(name)) return "performance";
  if (MAINTAINABILITY_HOME_BIOMARKERS.has(name)) return "maintainability";
  return "defect";
}

export const DIMENSION_LABEL: Record<BiomarkerDimension, string> = {
  defect: "Defect risk",
  maintainability: "Maintainability",
  performance: "Performance",
};

/** Tailwind chip classes per pillar, matching the surrounding chip palette. */
export const DIMENSION_CHIP: Record<BiomarkerDimension, string> = {
  defect: "bg-[var(--color-accent-primary)]/10 text-[var(--color-accent-primary)]",
  maintainability: "bg-[var(--color-accent-secondary)]/10 text-[var(--color-accent-secondary)]",
  performance: "bg-[var(--color-info)]/10 text-[var(--color-info)]",
};
