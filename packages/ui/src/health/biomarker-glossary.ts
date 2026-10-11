/**
 * Biomarker glossary — the human-readable label, category, and short
 * explanation used in tooltips, info popovers, and grouped views across all
 * three health pages.
 *
 * Keep in sync with ``packages/core/src/repowise/core/analysis/health/scoring.py``
 * (the python ``_BIOMARKER_CATEGORY`` map) and ``biomarkers/registry.py``.
 * Labels come from core (``health/biomarker_labels.py``) through the generated
 * map, so agent prompts and the page name a marker the same way.
 */

import { BIOMARKER_LABELS } from "./generated/biomarker-labels";

export type BiomarkerCategory =
  | "structural_complexity"
  | "size_and_complexity"
  | "duplication"
  | "test_coverage"
  | "test_coverage_gradient"
  | "test_quality"
  | "error_handling"
  | "performance"
  | "organizational"
  | "sql";

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
  sql: "SQL",
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
  // in scoring.py): the whole performance pillar deducts at most 2.0. Fallback
  // only — a server-supplied cap always wins (see score-breakdown.tsx), so this
  // constant matters just for payloads that predate the `cap` field.
  performance: 2.0,
  // SQL smells deduct on the maintainability pillar only, bounded by the `sql`
  // cap in `_MAINTAINABILITY_CATEGORY_CAPS` (scoring.py).
  sql: 2.0,
};

export const BIOMARKER_GLOSSARY: Record<string, BiomarkerInfo> = {
  brain_method: {
    label: BIOMARKER_LABELS.brain_method,
    category: "structural_complexity",
    description:
      "A function that knows too much — high cyclomatic complexity, many parameters, and deep nesting all at once. Hard to test, easy to break.",
  },
  nested_complexity: {
    label: BIOMARKER_LABELS.nested_complexity,
    category: "structural_complexity",
    description:
      "Deeply nested control flow (≥4 levels). Cognitive load grows non-linearly with nesting; flatten with early returns or extracted helpers.",
  },
  bumpy_road: {
    label: BIOMARKER_LABELS.bumpy_road,
    category: "structural_complexity",
    description:
      "A function with multiple shallow complexity bumps stitched together. No single block is bad, but the whole reads as a sequence of mini-functions.",
  },
  complex_method: {
    label: BIOMARKER_LABELS.complex_method,
    category: "size_and_complexity",
    description:
      "Cyclomatic complexity above the language threshold. Many independent paths through one function.",
  },
  large_method: {
    label: BIOMARKER_LABELS.large_method,
    category: "size_and_complexity",
    description:
      "A function with too many non-comment lines of code. Even simple logic gets hard to hold in your head past a point.",
  },
  primitive_obsession: {
    label: BIOMARKER_LABELS.primitive_obsession,
    category: "size_and_complexity",
    description:
      "A function that takes many parameters, where a value object would often carry the same data. Calls become positional and easy to mismatch. Test cases are skipped: their parameters are injected fixtures.",
  },
  dry_violation: {
    label: BIOMARKER_LABELS.dry_violation,
    category: "duplication",
    description:
      "Code blocks duplicated across files. Ranked by co-change frequency — clones that move together are most worth consolidating.",
  },
  untested_hotspot: {
    label: BIOMARKER_LABELS.untested_hotspot,
    category: "test_coverage",
    description:
      "High-churn, centrally depended-on file with no paired test file and low coverage. The riskiest place to leave untested.",
  },
  coverage_gap: {
    label: BIOMARKER_LABELS.coverage_gap,
    category: "test_coverage",
    description:
      "Specific uncovered lines in a file. Surfaced when a coverage report has been ingested.",
  },
  coverage_gradient: {
    label: BIOMARKER_LABELS.coverage_gradient,
    category: "test_coverage_gradient",
    description:
      "A continuous coverage penalty proportional to the uncovered fraction — keeps the score sensitive to coverage even on well-tested files where the binary gates never fire.",
  },
  developer_congestion: {
    label: BIOMARKER_LABELS.developer_congestion,
    category: "organizational",
    description:
      "Multiple authors editing the same file frequently — a coordination cost signal. Often points to an unclear module boundary.",
  },
  knowledge_loss: {
    label: BIOMARKER_LABELS.knowledge_loss,
    category: "organizational",
    description:
      "Files whose primary author has reduced or stopped contributing — a bus-factor warning.",
  },
  hidden_coupling: {
    label: BIOMARKER_LABELS.hidden_coupling,
    category: "organizational",
    description:
      "Two files co-change in git history but have no explicit import between them. The implicit contract is invisible at the source level, so changes slip out of sync and break in production. Advisory: it costs this file no points.",
  },
  complex_conditional: {
    label: BIOMARKER_LABELS.complex_conditional,
    category: "structural_complexity",
    description:
      "A boolean expression stitching three or more operators together. Compound conditions like these usually encode two policies fighting for one line and are easy to misread under pressure.",
  },
  function_hotspot: {
    label: BIOMARKER_LABELS.function_hotspot,
    category: "organizational",
    description:
      "A single function concentrating an outsized share of the file's churn while carrying real structural complexity. Defects accumulate where modification frequency and complexity collide.",
  },
  code_age_volatility: {
    label: BIOMARKER_LABELS.code_age_volatility,
    category: "organizational",
    description:
      "A long-stable function (median line age ≥ 1 year) that has suddenly started moving again. This edit profile is one of the strongest empirical predictors of regressions.",
  },
  low_cohesion: {
    label: BIOMARKER_LABELS.low_cohesion,
    category: "structural_complexity",
    description:
      "A class whose methods split into multiple disconnected groups (LCOM4 > 1). The groups share a namespace but not a responsibility — usually two classes living in one.",
  },
  god_class: {
    label: BIOMARKER_LABELS.god_class,
    category: "structural_complexity",
    description:
      "A very large class with many methods including at least one brain method. It accumulates responsibilities until every change routes through it.",
  },
  ownership_risk: {
    label: BIOMARKER_LABELS.ownership_risk,
    category: "organizational",
    description:
      "Many minor contributors with no dominant owner. Fragmented ownership is a calibrated defect predictor — nobody holds the full picture of the file.",
  },
  churn_risk: {
    label: BIOMARKER_LABELS.churn_risk,
    category: "organizational",
    description:
      "Lines added and deleted at a rate far above the repo norm for the file's size. Relative churn is a classic defect-density predictor.",
  },
  change_entropy: {
    label: BIOMARKER_LABELS.change_entropy,
    category: "organizational",
    description:
      "Changes scattered across many unrelated commits rather than focused work. High entropy in the change history is a strong history-based fault predictor.",
  },
  co_change_scatter: {
    label: BIOMARKER_LABELS.co_change_scatter,
    category: "organizational",
    description:
      "Editing this file tends to ripple across many other files in the same commits (shotgun surgery). The strongest calibrated predictor in the score.",
  },
  prior_defect: {
    label: BIOMARKER_LABELS.prior_defect,
    category: "organizational",
    description:
      "Bug-fix commits touched this file repeatedly in the recent window. Recent defect history is the most cost-effective predictor of further defects.",
  },
  large_assertion_block: {
    label: BIOMARKER_LABELS.large_assertion_block,
    category: "test_quality",
    description:
      "A test function running a long unbroken run of assertions. When one fails, the rest never execute — split into focused cases.",
  },
  assertion_free_test: {
    label: BIOMARKER_LABELS.assertion_free_test,
    category: "test_quality",
    description:
      "A test case that runs the code under test and then checks nothing, so it passes whatever that code does. A mock verification counts as a check, so does a `throw` the author wrote by hand, and so does handing the check to a helper this test calls, in this file or, when the call graph resolves the call, in another one. Advisory: it costs this file no points.",
  },
  mock_saturated_test: {
    label: BIOMARKER_LABELS.mock_saturated_test,
    category: "test_quality",
    description:
      "A test whose mock setup dwarfs what it checks, so it mostly verifies the collaboration the test itself wired up. Advisory: it costs this file no points.",
  },
  duplicated_assertion_block: {
    label: BIOMARKER_LABELS.duplicated_assertion_block,
    category: "test_quality",
    description:
      "An assertion block copy-pasted across test files. Behaviour changes now require synchronized edits, and drift produces misleading green runs.",
  },
  error_handling: {
    label: BIOMARKER_LABELS.error_handling,
    category: "error_handling",
    description:
      "Swallowed exceptions, bare excepts, unsafe unwraps, or discarded error returns. An advisory maintainability flag — failures here vanish silently.",
  },
  ungoverned_hotspot: {
    label: BIOMARKER_LABELS.ungoverned_hotspot,
    category: "organizational",
    description:
      "A churn hotspot with no governing architectural decision on record. High-traffic code evolving without documented intent.",
  },
  stale_governance: {
    label: BIOMARKER_LABELS.stale_governance,
    category: "organizational",
    description:
      "The architectural decision governing this file has gone stale — the code has moved on since the decision was last confirmed.",
  },
  contradictory_decision: {
    label: BIOMARKER_LABELS.contradictory_decision,
    category: "organizational",
    description:
      "Two governing decisions on record contradict each other. The file is caught between conflicting documented intents.",
  },
  io_in_loop: {
    label: BIOMARKER_LABELS.io_in_loop,
    category: "performance",
    description:
      "A database call, network request, filesystem read, or subprocess spawn that runs once per loop iteration. On a database boundary this is the classic N+1 query; elsewhere it is an I/O call inside a loop. Detected across function boundaries via the call graph, resolved to a classified I/O boundary. A static performance RISK (high precision, low recall), not measured runtime.",
  },
  string_concat_in_loop: {
    label: BIOMARKER_LABELS.string_concat_in_loop,
    category: "performance",
    description:
      "A string built by repeated += inside a loop, which is quadratic in many runtimes (each concat copies the whole accumulated string). Use a buffer + join for linear cost.",
  },
  blocking_sync_in_async: {
    label: BIOMARKER_LABELS.blocking_sync_in_async,
    category: "performance",
    description:
      "A synchronous blocking call (time.sleep, requests.get, subprocess.run) inside an async function blocks the whole event loop, stalling every other coroutine. Mirrors ruff's ASYNC210/230/251.",
  },
  regex_compile_in_loop: {
    label: BIOMARKER_LABELS.regex_compile_in_loop,
    category: "performance",
    description:
      "A regex with a static pattern compiled every loop iteration (Pattern.compile, regexp.MustCompile, Regex::new) instead of once. Compilation dominates matching, so recompiling a constant pattern is wasted work. Fires only where the language does not cache compiled patterns (Java, Go, Rust). Hoist the compile outside the loop.",
  },
  defer_in_loop: {
    label: BIOMARKER_LABELS.defer_in_loop,
    category: "performance",
    description:
      "A Go `defer` inside a loop runs when the enclosing function returns, not at the end of the iteration, so a resource opened-and-deferred each iteration stays held until the function exits — the classic file-handle / *sql.Rows leak. Close it in the loop body, or wrap the body in its own function so the defer fires per iteration.",
  },
  resource_construction_in_loop: {
    label: BIOMARKER_LABELS.resource_construction_in_loop,
    category: "performance",
    description:
      "A heavy I/O client or connection (sqlite3.connect, httpx.Client, boto3.client, new PrismaClient, sql.Open) constructed every loop iteration instead of once. Opens a fresh connection/pool per iteration — connection churn and, for HttpClient, socket exhaustion. Hoist and reuse a single instance.",
  },
  lock_in_loop: {
    label: BIOMARKER_LABELS.lock_in_loop,
    category: "performance",
    description:
      "A mutex or lock acquired on every loop iteration (lock.acquire, mu.Lock, synchronized, lock(x){}). Serializes the loop body and concentrates contention. Hoist the lock outside the loop or batch the critical section.",
  },
  serial_await_in_loop: {
    label: BIOMARKER_LABELS.serial_await_in_loop,
    category: "performance",
    description:
      "An awaited I/O round-trip run one-at-a-time inside a loop. When the iterations are independent, fan them out with gather / Promise.all / Task.WhenAll for concurrent execution. Advisory: independence may be unproven, and against a database or network client a fan-out also needs a bound on concurrency.",
  },
  unbounded_read_reduced_in_memory: {
    label: BIOMARKER_LABELS.unbounded_read_reduced_in_memory,
    category: "performance",
    description:
      "A database read with no limit/range/single bound, run once, whose result a loop then dedups down to one row per key (setdefault, a seen-set, a not-in guard). The table can grow without bound while the code still pays to transfer and decode every row. Move the selection into the query: DISTINCT ON, a window function, or a view.",
  },
  lazy_load_in_loop: {
    label: BIOMARKER_LABELS.lazy_load_in_loop,
    category: "performance",
    description:
      "A relationship declared lazy (no selectinload / joinedload, no select_related / prefetch_related) read on every iteration of a loop over its parent rows, one query per row. Load the relationship with the rows instead. Advisory: whether every iteration reaches the access, and whether another layer already loaded it, is not proven.",
  },
  membership_test_against_list_in_loop: {
    label: BIOMARKER_LABELS.membership_test_against_list_in_loop,
    category: "performance",
    description:
      "Testing `x in big_list` (or big_list.includes(x)) inside a loop is O(n·m); a set makes each lookup O(1), turning the loop linear. Only fires when the right operand is provably a list, never a set or dict.",
  },
  nested_loop_with_io: {
    label: BIOMARKER_LABELS.nested_loop_with_io,
    category: "performance",
    description:
      "A database / network / filesystem / subprocess call in the inner body of a nested loop — O(n·m) round-trips, the quadratic cousin of I/O-in-loop. The nesting raises confidence it is real, so it surfaces alongside io_in_loop. Batch the inner query or restructure the loops.",
  },
  hot_path_sync_io: {
    label: BIOMARKER_LABELS.hot_path_sync_io,
    category: "performance",
    description:
      "A blocking subprocess or filesystem call in one of the repo's most-called functions, even outside a loop. Every call through the function waits for it. Advisory: a latency signal ranked by call-graph centrality, not proof of a request path, and not always a defect. Tests, tooling, examples, and generated or vendored code are not flagged.",
  },
  blocking_io_under_lock: {
    label: BIOMARKER_LABELS.blocking_io_under_lock,
    category: "performance",
    description:
      "A database / network / filesystem / subprocess round-trip reached while a lock is held (a C# lock(){} or Java synchronized(){} block, directly or through a call). Every other thread blocks for the full I/O wait. Do the I/O outside the critical section and take the lock only to mutate shared state.",
  },
  nested_loop_quadratic: {
    label: BIOMARKER_LABELS.nested_loop_quadratic,
    category: "performance",
    description:
      "A data-dependent loop nested inside another (O(n^2)) in a hot, central function. Advisory / informational — surfaced only where centrality ranking says it is worth a look; check the inner bound or use a set/map lookup if it is a search.",
  },
  sql_high_complexity: {
    label: BIOMARKER_LABELS.sql_high_complexity,
    category: "sql",
    description:
      "A stored procedure or function with high cyclomatic complexity, counted from the decision keywords (IF / WHEN / WHILE / LOOP and boolean operators) in its body. Procedural SQL this branchy is hard to test and usually hides business logic that belongs in the application layer.",
  },
  sql_select_star: {
    label: BIOMARKER_LABELS.sql_select_star,
    category: "sql",
    description:
      "A bare * projection inside a view, materialized view, or routine. When the source table gains a column the relation silently changes shape, breaking downstream consumers at a distance. Ad-hoc scripts are not flagged.",
  },
  sql_update_delete_without_where: {
    label: BIOMARKER_LABELS.sql_update_delete_without_where,
    category: "sql",
    description:
      "A checked-in UPDATE or DELETE with no WHERE clause touches every row in the table. Sometimes intentional (seed resets), always worth a reviewer's attention.",
  },
  sql_cartesian_join: {
    label: BIOMARKER_LABELS.sql_cartesian_join,
    category: "performance",
    description:
      "A comma-join (FROM a, b) with no join predicate anywhere in the statement produces the full cross product: O(n·m) rows. An explicit CROSS JOIN states intent and is not flagged; a comma-join with a WHERE clause is old-style join syntax and is not flagged either.",
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

export type BiomarkerDimension = "defect" | "maintainability" | "performance" | "advisory";

/**
 * Biomarkers that home to the non-scoring `advisory` dimension. They measure
 * something real that no defect corpus labels, or that showed no defect signal
 * when tested, so they never deduct and the chip has to say so rather than
 * borrowing the defect pillar's label. Mirror of
 * ``_ADVISORY_HOME`` in core's `scoring.py`.
 */
export const ADVISORY_HOME_BIOMARKERS: ReadonlySet<string> = new Set([
  "assertion_free_test",
  "mock_saturated_test",
  "hidden_coupling",
]);

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
  "sql_high_complexity",
  "sql_select_star",
  "sql_update_delete_without_where",
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
  "regex_compile_in_loop",
  "defer_in_loop",
  "resource_construction_in_loop",
  "lock_in_loop",
  "serial_await_in_loop",
  "membership_test_against_list_in_loop",
  "nested_loop_with_io",
  "nested_loop_quadratic",
  "hot_path_sync_io",
  "blocking_io_under_lock",
  "list_insert_zero_in_loop",
  "pd_concat_in_loop",
  "pandas_iterrows_in_loop",
  "json_parse_in_loop",
  "array_spread_in_reduce",
  "goroutine_in_unbounded_loop",
  "sql_cartesian_join",
  "unbounded_read_reduced_in_memory",
  "lazy_load_in_loop",
]);

/**
 * A biomarker's home dimension for display / filtering. Prefer a finding's
 * server-provided `dimension` field where available; this is the fallback when
 * only the biomarker type is known (e.g. a glossary entry).
 */
export function biomarkerDimension(name: string): BiomarkerDimension {
  if (ADVISORY_HOME_BIOMARKERS.has(name)) return "advisory";
  if (PERFORMANCE_HOME_BIOMARKERS.has(name)) return "performance";
  if (MAINTAINABILITY_HOME_BIOMARKERS.has(name)) return "maintainability";
  return "defect";
}

/**
 * A finding's home pillar, preferring the server's `dimension` over the
 * glossary fallback. One owner on purpose: this narrowing was written out three
 * times (the biomarker list, the file health tab, the file drawer) and every
 * copy listed the dimensions by hand, so a fourth dimension would have rendered
 * under the defect pillar's label in all three until each was found.
 */
export function asBiomarkerDimension(
  dimension: string | null | undefined,
  biomarkerType: string,
): BiomarkerDimension {
  // ``Object.hasOwn``, not a property probe: an indexed lookup reaches the
  // prototype chain, so a server dimension of "constructor" or "toString" would
  // pass and then render an undefined chip class.
  if (dimension && Object.hasOwn(DIMENSION_LABEL, dimension)) {
    return dimension as BiomarkerDimension;
  }
  return biomarkerDimension(biomarkerType);
}

export const DIMENSION_LABEL: Record<BiomarkerDimension, string> = {
  defect: "Code health",
  maintainability: "Maintainability",
  performance: "Performance",
  advisory: "Advisory",
};

/** Tailwind chip classes per pillar, matching the surrounding chip palette. */
export const DIMENSION_CHIP: Record<BiomarkerDimension, string> = {
  defect: "bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]",
  maintainability: "bg-[var(--color-accent-secondary)]/10 text-[var(--color-accent-secondary)]",
  performance: "bg-[var(--color-info)]/10 text-[var(--color-info)]",
  // Deliberately the most muted chip in the palette: an advisory finding costs
  // the file nothing and must not read as urgent beside ones that do.
  advisory: "bg-[var(--color-text-muted)]/10 text-[var(--color-text-muted)]",
};

/**
 * The category whose evidence is git history rather than the file's code.
 * Mirrors `HISTORY_CATEGORY` / `split_by_origin` in core's health scoring:
 * the two must agree, or a finding excluded from the score's structural half
 * still reads as something to go and fix.
 */
export const HISTORY_CATEGORY: BiomarkerCategory = "organizational";

export function isHistoryBiomarker(name: string): boolean {
  return biomarkerInfo(name).category === HISTORY_CATEGORY;
}

/**
 * Partition findings into the ones a reader can act on by editing the file and
 * the ones they cannot. The TS mirror of core's `split_by_origin`.
 */
export function splitByOrigin<T extends { biomarker_type: string }>(
  findings: T[],
): { codeShape: T[]; history: T[] } {
  const codeShape: T[] = [];
  const history: T[] = [];
  for (const finding of findings) {
    (isHistoryBiomarker(finding.biomarker_type) ? history : codeShape).push(finding);
  }
  return { codeShape, history };
}

/**
 * History findings are watch items, not work items, so they take a neutral
 * chip. The pillar colours mark where work belongs; painting a signal nobody
 * can act on in the same ink sends a reader to edit a file over its commit log.
 */
export const HISTORY_CHIP =
  "bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]";

export const HISTORY_LABEL = "Watch";

/**
 * History-category markers that are still work: writing or updating a
 * decision clears them. Mirrors core `GOVERNANCE_BIOMARKERS`.
 */
const GOVERNANCE_BIOMARKERS: ReadonlySet<string> = new Set([
  "ungoverned_hotspot",
  "stale_governance",
  "contradictory_decision",
]);

/** A marker nothing in the repository can clear: context for a reviewer. */
export function isWatchOnlyBiomarker(name: string): boolean {
  return isHistoryBiomarker(name) && !GOVERNANCE_BIOMARKERS.has(name);
}

export const HISTORY_EXPLAINER =
  "Measured from this file's git history, not its code. Editing the file will not clear it.";
