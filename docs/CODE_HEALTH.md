# Code Health

Repowise computes a 1-10 health score for every file in your repo from twenty-five
deterministic markers: McCabe complexity, deep nesting, brain methods,
class cohesion (LCOM4), god classes, clone detection, untested hotspots,
function-level churn, code-age volatility, ownership dispersion, relative churn,
change entropy, co-change scatter, recent defect history, test-quality smells,
and more. **No LLM calls, no cloud requirement.** Pure Python over tree-sitter
and git data, designed to finish in under 30 seconds on a 3,000-file repo.

<div align="center">
<img src="../.github/assets/health-loop.svg" alt="repowise code-health loop: 25 deterministic markers fan into three signals (defect risk, maintainability, performance), the graph and git history locate where risk concentrates, and refactoring intelligence emits concrete plans (Extract Class, Extract Helper, Move Method, Break Cycle, Split File) your agent executes" width="100%" />
</div>

Code health runs as a loop: **measure** every file across three signals,
**locate** where the risk concentrates through the dependency graph and git
history, then **fix** it with a concrete refactoring plan an agent can execute
(see [Refactoring targets](#refactoring-targets) and
[docs/REFACTORING.md](REFACTORING.md)).

Linters check patterns. The health score predicts risk. A linter flags a line
that matches a known-bad shape; the score estimates which files are likely to
harbor the next bug, ranks them, and is calibrated against real defect history
(see [Does the score find the bugs?](#does-the-score-find-the-bugs)). It uses
signals a linter has no access to: churn, ownership, co-change, blast radius,
hotspots. The [performance signal](#performance-static-performance-risk) is the
one part that overlaps a linter, and even it follows the call graph across files,
which a file-local linter cannot.

## Quick start

```bash
repowise init          # full index, populates health tables
repowise health        # KPIs + 20 worst-scoring files + top findings
repowise update        # re-score only changed files on each subsequent run
```

Open `http://localhost:7777/repos/<id>/health` for the dashboard once the
local server is running (`repowise serve`).

## The score

Each file starts at 10.0. Marker findings deduct from the score; deductions
are capped per category so any one category can drive the score down by at
most:

| Category               | Cap   | Markers |
|------------------------|-------|------------|
| Organizational         | −3.5  | developer_congestion, knowledge_loss, hidden_coupling, function_hotspot, code_age_volatility, ownership_risk, churn_risk, change_entropy, co_change_scatter, prior_defect |
| Structural complexity  | −2.5  | brain_method, low_cohesion, god_class, nested_complexity, bumpy_road, complex_conditional |
| Test coverage          | −2.0  | untested_hotspot, coverage_gap |
| Test coverage (cont.)  | −2.0  | coverage_gradient |
| Size & complexity      | −1.5  | complex_method, large_method, primitive_obsession |
| Duplication            | −1.0  | dry_violation |
| Test quality           | −0.5  | large_assertion_block, duplicated_assertion_block |
| Error handling         | −0.5  | error_handling |

Twenty-five markers across the categories above. `function_hotspot` and
`code_age_volatility` are blame-based and sit in the organizational bucket: both
are tier-aware and stay silent on ESSENTIAL-tier repos until the per-line blame
index is built.

Per-marker weight multipliers let the strongest empirical predictors deduct
more than the uniform severity table alone allows. The weights are **calibrated
offline against a defect corpus, not hand-tuned**: each file is scored at the
pre-window commit (T0, no leakage) and a logistic regression with NLOC as an
explicit control fits each marker's defect lift beyond file size. The runtime
stays deterministic; only the learned constants ship. The strongest predictors
are `co_change_scatter`, `change_entropy`, `ownership_risk`, and
`nested_complexity`; widely-firing smells that proved weak under leakage-free
scoring are floored. The full per-marker table lives in the
[architecture doc](architecture/code-health.md#61-calibrated-weight-multipliers),
and the calibration with confidence intervals is in the
[benchmark report](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/BENCHMARK_REPORT.md).

The final score is clamped to `[1.0, 10.0]`. The three repo-level KPIs:

- **Hotspot Health**: NLOC-weighted average over the files classified as
  hotspots by the git layer (high churn percentile plus minimum-activity
  floors), not a fixed top-N slice.
- **Average Health**: NLOC-weighted average over all files.
- **Worst Performer**: single lowest-scoring file.

## Three health signals: defect risk, maintainability, and performance

Repowise surfaces three orthogonal health signals computed from the same
marker stream by one shared scoring kernel: **defect risk** (the calibrated,
overall number), **maintainability**, and **performance**. They are co-equal
views, never blended into one number (the section on the overall score below
explains why).

The score above is the defect-risk signal: it is calibrated against a defect
corpus, the bands are calibrated to it (Alert files carry roughly 17x the defect
rate of Healthy files), and it is the overall number surfaced everywhere. But
not every code smell predicts bugs. A handful of markers fire widely and
matter a lot for how hard code is to read and change, yet proved weak as defect
predictors under leakage-free scoring, so the defect calibration floors them to
0.5 (`low_cohesion`, `brain_method`, `primitive_obsession`, `dry_violation`,
`error_handling`). Floored inside a defect-framed score they do two unhelpful
things at once: they still nudge the number a little (noise against the
calibrated signal) and they get no credit for the real problem they describe
(maintainability).

Repowise therefore computes a second, parallel signal, maintainability, from the
same marker stream:

- The floored smells above deduct at full weight (1.0) in maintainability
  instead of the 0.5 the defect calibration imposes. The defect calibration does
  not apply to a non-defect signal, so the maintainability weights are expert-set
  and tuned only against the maintainability pillar's own per-category caps.
- The structural smells that are genuine defect predictors and core
  maintainability concerns (`god_class`, `large_method`, `nested_complexity`)
  count toward both dimensions.
- Pure defect and organizational predictors (`change_entropy`, `ownership_risk`,
  `co_change_scatter`, and the like) stay out of maintainability entirely.

The two signals are computed by the single shared scoring kernel
(`scoring.score_file`) against independent weight/category/cap tables, and they
never feed back into each other. The overall, surfaced score remains exactly the
defect score (byte-for-byte; a golden test locks this) until a later, deliberate
decision to blend. Maintainability is surfaced alongside it as a co-equal
headline:

- **REST/overview**: `summary.maintainability_average` plus a per-file
  `maintainability_score` on every metric row.
- **MCP `get_health`**: `kpis.maintainability_average` and per-file
  `defect_score` / `maintainability_score` / `performance_score`.
- **CLAUDE.md** and the CLI `status` line print a maintainability headline next
  to defect-risk health.
- Every finding carries a `dimension` (`defect` / `maintainability` /
  `performance`) naming the pillar it homes under, so findings can be filtered
  per signal.

## Performance: static performance risk

The third signal, performance, flags shapes that waste work (code whose
structure does redundant I/O), rather than measured runtime. It is deliberately
high-precision and low-recall: a few real findings the rest of the toolchain can
trust beat a wall of maybes.

Standard linters do not find this class of problem. clippy, ruff's `PERF` rules,
ESLint, and golangci-lint read one function at a time, so a loop in one file
whose database call lives in another is invisible to them. On a 12,000-file
benchmark the standard linters found **0** of the cross-function I/O-in-loop
cases; repowise surfaced 557 findings across the run, about 90 of them spanning
function boundaries, and 98% fell in categories ruff has no rule for. The
findings are ordered by impact rather than raw count (NDCG 0.755 against 0.292
for severity-only). One caveat: the Rust dialect was new when the benchmark ran,
and clippy could not be run end-to-end on the corpus because of a Windows build
wall, so the Rust comparison there is catalogue-level, not a measured
head-to-head. The data is in the
[perf-detection benchmark](https://github.com/repowise-dev/repowise-bench/tree/master/perf-detection).

The detectors (all under one bounded `performance` category cap of 1.0, so the
pillar stays advisory) are:

- **`io_in_loop`**: a database call, network request, filesystem read, or
  subprocess spawn that runs once per loop iteration: the classic N+1. This is
  the moat. Two things make it more than a file-local lint:
  - **Dependency classification.** The loop-nested call is resolved through a
    shared I/O-boundary classifier (`io_kind ∈ {db, network, filesystem,
    subprocess, lock}`) and only fires on a classified execution sink (an
    actual round-trip like `.execute` / awaited HTTP / `subprocess.run`), not a
    query-builder chain or a same-named pure helper.
  - **Call-graph reachability.** The loop and the I/O call need not be in the
    same function. A bounded-depth (≤3 hops) walk over the resolved `calls` graph
    catches the interprocedural case (loop in `A`, sink in a helper `A` calls)
    that no file-local linter can see. Cross-function findings carry their
    resolved `caller -> ... -> sink` path for explainability.
- **`string_concat_in_loop`**: quadratic `+=` string building in a loop.
- **`blocking_sync_in_async`**: a synchronous blocking call inside an `async`
  function, which stalls the whole event loop (mirrors ruff `ASYNC210/230/251`).
- **`resource_construction_in_loop`**: a heavy I/O client or connection
  (`sqlite3.connect` / `httpx.Client` / `boto3.client` / `new PrismaClient` /
  `new HttpClient` / `sql.Open`) constructed every iteration instead of hoisted:
  connection churn, and socket exhaustion for `HttpClient`.
- **`lock_in_loop`**: a mutex acquired on every iteration (`lock.acquire` /
  `mu.Lock` / `synchronized` / `lock(x){}`): a contention site. Activates the
  `lock` I/O-boundary kind.
- **`serial_await_in_loop`**: an awaited I/O round-trip run one at a time in a
  loop where a `gather` / `Promise.all` could fan it out. Advisory: a static
  analyzer cannot prove the iterations are independent, so the finding suggests
  rather than asserts.
- **`membership_test_against_list_in_loop`**: `x in big_list` (or
  `big_list.includes(x)`) inside a loop is O(n·m); a set makes each lookup O(1).
  Fires only when the right operand is provably a list, never a set or dict.
- **`nested_loop_with_io`**: an I/O sink in the inner body of a **nested** loop:
  O(n·m) round-trips, the quadratic cousin of `io_in_loop`. The nesting itself
  raises confidence the finding is real, so it surfaces alongside `io_in_loop`.
- **`blocking_io_under_lock`**: an I/O round-trip reached while a block-scoped
  lock is held (a C# `lock(){}` / Java `synchronized(){}` block, directly or
  through a call). Every other thread blocks for the full I/O wait, a throughput
  killer. The cross-function case reuses the same bounded-reachability engine as
  the N+1 moat, with a `lock -> io` entry set.

Two markers use centrality as a precision gate rather than only a sort key: a
shape that is noisy when flagged everywhere only fires in a hot function (one
with top-quintile call-graph in-degree, or in a churny/hotspot file), computed by
a reusable severity ranker over the same call graph the N+1 pass uses:

- **`hot_path_sync_io`**: a blocking subprocess or filesystem call in a hot,
  request-reachable function, even outside a loop. It generalizes the pillar
  beyond loops: its latency is paid on every call through the function. (DB and
  network are excluded, since both are awaited in async code, and the un-awaited
  calls a static pass sees are result materializers or chained awaits, not
  blocking round-trips; subprocess and filesystem are synchronous by
  construction.) Advisory: a latency signal ranked by centrality, not a defect.
- **`nested_loop_quadratic`**: a data-dependent loop nested inside another
  (O(n²)) in a hot function. The centrality gate makes the list short and
  reviewable (it cut a 13x volume of raw nested loops), but centrality answers
  "is this function important", not "is n large", so it ships advisory and
  informational only, never at a weight that moves the score.

A few markers are language-specific, contributed by that language's dialect (see
below) rather than the shared core:

- **`regex_compile_in_loop`** (Java, Go): a `Pattern.compile` /
  `regexp.MustCompile` recompiled every iteration instead of hoisted. Skipped on
  Python / .NET, which cache compiled patterns; on Go it fires only for a
  string-literal pattern (a dynamic argument may legitimately vary per iteration
  and cannot be hoisted).
- **`defer_in_loop`** (Go): a `defer` inside a loop holds the deferred handle
  until the enclosing function returns, not the iteration: the classic Go file or
  row-handle leak. A pure syntactic shape, very high precision.
- **`goroutine_in_unbounded_loop`** (Go): a `go ...()` spawned per element of a
  `for k, v := range coll` loop, with no concurrency bound: a spawn explosion
  (use a worker pool or bounded `errgroup`). Restricted to the two-variable
  `range` form, which is only legal over a collection (a single-variable
  `for i := range n` is a bounded count loop). Advisory.
- **`list_insert_zero_in_loop`** (Python): `lst.insert(0, x)` each iteration
  shifts the whole list (O(n²)); use `collections.deque.appendleft`. Gated to a
  literal `0` index and to a list not re-created each iteration.
- **`pd_concat_in_loop`** (Python): `pd.concat([acc, chunk])` inside a loop
  copies the whole frame each pass (O(n²)); collect chunks and concat once.
- **`json_parse_in_loop`** (JS/TS): the `JSON.parse(JSON.stringify(x))`
  deep-clone idiom in a loop (use `structuredClone`). Restricted to that idiom;
  parsing a distinct payload each iteration is necessary work, not waste.
  Advisory.
- **`array_spread_in_reduce`** (JS/TS): `arr.reduce((a, x) => [...a, x], [])`
  rebuilds the accumulator every step (O(n²)); push-and-return instead. The
  `.reduce` is itself the loop, so this fires regardless of an enclosing loop.
- **sync-over-async** (C#, via `blocking_sync_in_async`): `.Result` / `.Wait()`
  / `.GetAwaiter().GetResult()` inside an `async` method blocks a thread-pool
  thread. C# is the one non-Python language with real `async`/`await`.

Each performance finding's `details` carry the `boundary_kind` it crosses, a
`cross_function` flag, and the reachability `path` for the cross-function case.
Severity is ranked by centrality (an N+1 in a high-traffic, churny function
outranks one in a leaf), not by raw count.

**Languages.** The performance signal fires on Python, TypeScript/JavaScript,
Java, Go, and C#. Each language is a self-contained `PerfDialect` plugin
(`analysis/health/perf/dialects/`) that owns its callee-extraction grammar, its
execution-sink lexicon, the loop / string / async predicates, and its own marker
list, registered in `PERF_DIALECTS` like the rest of the per-language pipeline.
A language without a dialect emits no perf findings (never a wrong one). The
db/network/filesystem/subprocess lexicons and the per-language precision hazards
(Java `.find`/`.get`, GORM `Find`/`Save`, C# in-memory-vs-`IQueryable` LINQ) are
each handled inside that language's dialect. The verb sets are gated for
precision: distinctive sinks (EF `*Async`, Spring-Data `findBy*`, JDBC
`executeQuery`) fire on name alone, while ambiguous verbs require file-level
db-import evidence. `io_in_loop` is validated across languages on an 11-repo OSS
corpus: Go 96.7%, TypeScript 100%, Python 96.2% hand-labeled precision; the
`blocking_sync_in_async` C# `.Result`/Result-pattern collision and the Go
`*sql.Rows.Scan` cursor FP were caught and fixed by that validation.
`string_concat_in_loop` is validated at 100% (26/26) after a reset-per-iteration
guard (an accumulator re-initialized each iteration is bounded, not O(n²)).
`nested_loop_quadratic` now fires only on a same-collection shape (two nested
loops over the same collection = all-pairs O(n²)) instead of raw nesting depth;
that makes it precision-safe-by-construction but rare, so it stays advisory-only,
as do `blocking_io_under_lock`, `pd_concat_in_loop`, `json_parse_in_loop`, and
`goroutine_in_unbounded_loop` (high-precision by construction, low corpus recall).

**Soundness limits (honest, by design).** Performance is a static signal, so it
under-reports rather than over-reports (these cap recall, not precision): dynamic
dispatch, monkeypatching, and callbacks-as-values produce no `calls` edge and are
invisible; ORM lazy-load N+1 fires on attribute access (no visible call) and is
explicitly out of scope (this includes Hibernate lazy-load N+1, which fires on a
getter, and EF Core navigation-property lazy load), so we catch explicit
repository or query calls in loops, not attribute-triggered lazy loads; chains
longer than three hops from the loop are not followed; and an unmodelled library
is untyped (`None`), so its sinks don't fire. We call this performance RISK, never
measured performance, and never fold it into the defect score. The
commit-agreement precision study and its caveats are published in the
[perf-detection methodology](https://github.com/repowise-dev/repowise-bench/blob/master/perf-detection/METHODOLOGY.md).

Performance surfaces exactly where maintainability does: a `performance_average`
on the overview summary and MCP `kpis`, a per-file `performance_score`, a
Performance KPI card and per-pillar finding filter on the dashboard, the
per-file Health tab and drawer, and a `Performance risk` line in CLAUDE.md and
the CLI `status` summary (each omitted/`null` on indexes built before the
detectors landed). The dimension names are mirrored in `@repowise-dev/types`
(`HEALTH_DIMENSIONS`) with a parity test on each side.

## The overall score: defect, not a blend

The single number repowise surfaces as the headline (the dashboard ring, the
band, the badge, the "does the score find the bugs?" stat) is, and stays, the
defect score. Maintainability and performance are presented as co-equal pillars,
not blended into the headline. This is a deliberate decision, for three reasons:

1. **Band calibration.** The Healthy/Warning/Alert cutoffs are calibrated to the
   defect score (Alert is roughly 17x the defect rate). A blended headline would
   invalidate those boundaries with no recalibration corpus behind the new number.
2. **Honesty of the validation stat.** "Does the score find the bugs?" is a claim
   about the defect pillar; it must stay bound to the number it measures.
3. **Different precision profiles.** Maintainability is expert-set and performance
   is high-precision/low-recall advisory. Neither is a calibrated bug predictor,
   so neither should move the bug-calibrated headline.

A golden test (`tests/unit/health/test_scoring_dimensions`) locks the defect
score byte-for-byte against the pre-split single score, so no pillar can ever
regress it. Introducing a blended overall score would require a written rationale
and a recalibration plan; until then, overall = defect.

## Bands and distribution

On top of the 1-10 number, every score falls into one of three **bands**. These
are the single categorical scheme repowise surfaces (there is deliberately no
letter grade: a letter on top of the number would be a third overlapping scale
with arbitrary cliffs):

| Band | Score | Meaning |
|------|-------|---------|
| **Healthy** | `≥ 8.0` | Low-risk, maintainable. |
| **Warning** | `4.0 – 8.0` | Worth watching; rising complexity or process risk. |
| **Alert** | `< 4.0` | High-risk; concentrates defects. |

The cutoffs are not arbitrary. On our calibration corpus, Alert files carry
roughly 17x the per-file defect rate of Healthy files, so the band boundaries
are empirically defensible. They are defined once in core
(`analysis/health/grading.py`) and mirrored in `@repowise-dev/types` for the UI;
a parity test on each side locks the values.

The **health distribution** is the NLOC-weighted split of the repo across the
three bands: what share of your code (by volume, not file count) is Healthy,
Warning, or Alert. `repowise health` prints it as a one-line summary; the
dashboard renders it as a bar.

```text
Distribution (by code volume): 8% alert (12 files) · 21% warning (88 files) · 71% healthy (410 files)
```

## Badge

`repowise health --badge` prints ready-to-paste Markdown for a README health
badge (a Shields-style color and `N.N/10` badge, no letter). A running Repowise
server (or the hosted app) also serves the badge directly:

```text
GET /api/repos/{repo_id}/health/badge.svg    # self-rendered flat SVG
GET /api/repos/{repo_id}/health/badge.json   # Shields endpoint payload
```

Embed the dynamic form via Shields:

```markdown
![code health](https://img.shields.io/endpoint?url=<SERVER>/api/repos/<REPO_ID>/health/badge.json)
```

## Does the score find the bugs?

The score is only worth anything if the files it flags are the files that
actually break. After an index, repowise checks that against the repo's own
history and prints a one-line callout:

```
Does the score find the bugs? 16/20 lowest-health files had a bug fix in the
last 6 months, 3.3x the 24% baseline (80% vs 24%).
```

It ranks every file by health score, takes the 20 lowest, and counts how many
were touched by a `fix:` commit in the trailing ~180-day window (the same
signal the `prior_defect` marker uses). That precision is contrasted with the
repo-wide base rate (the fraction of all files with a recent fix) to give the
lift. The same number appears on the web `health` and `overview` dashboards,
where it expands into a per-K table (worst 10/20/30), a concentration stat (what
share of recently-fixed files fall in the least-healthy 20%), and the exact
flagged files.

Agents can read the same stat over MCP, so a coding agent can confirm the score
is trustworthy on this repo before acting on it:

```python
# MCP: dashboard mode, the same precision@K / lift block
get_health(include=["accuracy"])
```

It stays silent on repos without enough history to be honest (fewer than 25
scored files, or fewer than 5 recently-fixed files). One caveat it discloses:
`prior_defect` is itself one (down-weighted) input to the score, so this is an
association on the indexed history, not a leakage-free forward prediction.

### Beyond your repo: cross-project validation

The per-repo callout is a quick local check. The score is also validated across
projects, so the number is not tuned to a single codebase. Every file is graded
at a commit that precedes the bug window, so no future information leaks in, then
checked against what actually broke:

- Across 21 repositories, 9 languages, and 2,826 files, the score reaches a mean
  ROC AUC of **0.737** (95% CI 0.683 to 0.787). ROC AUC measures how often the
  score ranks a known-buggy file worse than a clean one: 0.5 is a coin flip, 1.0
  is perfect. It beats raw churn by 0.10 AUC and a prior-defects baseline by 0.117
  AUC (DeLong p < 1e-9).
- On the public PROMISE/jEdit defect dataset, which played no part in
  calibration, the same markers score AUC **0.76 to 0.78**. That held-out
  result is the main evidence the signal is not overfit.
- The limits ship with the wins. Among files of similar size the signal is weak
  (within-size-band AUC near 0.49), so part of the headline is simply that larger
  files carry more risk; and a prior-defects baseline still finds bugs slightly
  more efficiently under a fixed review budget (it wins on Popt by 0.085).

Full methodology, confidence intervals, and the named head-to-head against
CodeScene are in the [benchmark hub](https://github.com/repowise-dev/repowise-bench).

## The markers

**brain_method**: A single function that is simultaneously long, deeply
nested, highly complex, and central to the dependency graph. The strongest
single signal of fragile code. Centrality is judged against the repo's own
dependency density (top-quintile of connected files, with an absolute
hub bar), so it fires on sparse-graph languages too, not just Python.

**low_cohesion**: A class whose methods split into groups that share no
fields and don't call each other (LCOM4 ≥ 2). Measured by the walker's
class-level model; a high value usually means several smaller,
single-responsibility classes are hiding inside one.

**god_class**: A large class (≥ 200 lines, ≥ 15 methods) that also
contains a brain method. Size alone isn't flagged: the brain-method
requirement keeps flat data holders and config tables from firing.

**nested_complexity**: Functions with control-flow nesting ≥ 4 levels.
Hard to read, hard to test, hard to refactor.

**bumpy_road**: Multiple branches stacked at the same depth, usually a
sign the function is doing several jobs that should be split.

**complex_method**: Cyclomatic complexity ≥ 9. Each branch is a path the
test suite has to cover.

**large_method**: Long functions that also carry at least some branching.
A long-but-perfectly-flat body (a big config/data literal, a wall of
sequential assignments) is a layout artefact rather than a complexity smell,
so it is excluded: the trigger is about length-with-substance, not raw line
count.

**primitive_obsession**: Many primitive parameters in one signature. A
dataclass or parameter object would name the inputs. Suppressed in very small
modules (under ~60 non-blank lines), where a wide signature is an idiomatic
config/builder/forwarder rather than a design smell.

**dry_violation**: Cross-file code clones, detected by a native Rabin-Karp
rolling hash over tree-sitter tokens (variable renames don't hide a clone).
Pairs are ranked by co-change so dormant duplicates rank lower than active
ones.

**untested_hotspot**: A hotspot file with low or zero coverage and many
dependents. The textbook "write tests before refactoring" case.

**coverage_gap**: Non-test files with meaningful uncovered surface.
Severity grades along coverage depth.

**coverage_gradient**: A continuous coverage deduction that scales with the
uncovered fraction (`4.0 × (1 − line_coverage_pct/100)`, capped), so files stay
penalised in proportion to how much code is untested rather than only when they
fall below a hard threshold. Fires across the whole 0-100% range for files with
known coverage; silent (no imputation) where coverage was never ingested.

**developer_congestion**: Too many active authors touching the same file.
Usually an ownership problem dressed up as a code problem.

**knowledge_loss**: The primary authors of the file are no longer active
on the project. Refactor while someone still remembers why. Gated on recent
activity: an abandoned-but-stable file is low risk (the survivor effect),
so this only fires while the code is still being changed.

**hidden_coupling**: Files that consistently change in the same commits
without an explicit import or dependency edge between them. Captures
behavioral coupling (shared protocols, parallel config, copy-pasted
constants) that static analysis cannot see. Tier-aware: empty on
ESSENTIAL-tier repos until co-change backfill runs.

**complex_conditional**: Branch / loop guards that combine three or more
boolean operators. Severity grows with the operator count (LOW at 3, MED
at 4, HIGH at 5, CRIT at 6+).

**function_hotspot**: Functions that are both structurally complex and
frequently modified. Per-function modification counts come from a
per-line blame index built once per file (FULL git tier) and shared
with `code_age_volatility`. Fires when a function's distinct-commit
count is at or above the repo-wide p80 AND the function meets a
structural floor (CCN ≥ 10 or max nesting ≥ 3). Tier-aware: returns no
findings on ESSENTIAL-tier repos until `backfill_blame()` runs.

**code_age_volatility**: Functions whose median line age is at least a
year old that are suddenly being modified. Strong defect predictor:
the editor is usually working in unfamiliar territory. Uses the same
per-line blame index: `median_age_days` from per-line author
timestamps, `recent_mod_count` from distinct shas inside the last 30
days. Severity escalates with both axes (CRIT when median age ≥ 2y AND
≥ 5 recent commits). Tier-aware: same ESSENTIAL no-op as
`function_hotspot`.

**ownership_risk**: Long-run ownership dispersion. Counts minor
contributors (authors who each own less than 5% of the file's commits) and
the dominant owner's share. Many drive-by authors with no clear owner is
the single strongest defect correlate in the literature (Bird et al.). Fires
on files with ≥ 5 commits where ≥ 3 contributors are minor or no owner holds
40%. Complements `developer_congestion`, which measures active (90-day)
contention rather than lifetime dispersion.

**churn_risk**: Relative churn: the fraction of a file's lines rewritten in
the last 90 days, normalized by file size. A file whose recent window rewrote
more lines than it contains is structurally unstable regardless of how big it
is. Because the trigger is a ratio to NLOC, it does not simply re-flag large
files. Fires when the file is actively churning (≥ 5 recent commits, top
quartile of repo churn) and relative churn ≥ 1.0.

**change_entropy**: How scattered a file's change history is, adapted from
Hassan's History Complexity Metric. Each commit is treated as a one-period
window whose entropy is `log2(files-touched)`, distributed across its files and
decayed over time. A file repeatedly caught up in wide, scattered commits
scores high; one changed in focused, single-purpose commits stays low even if
it changes often, so this is not a churn proxy. Fires when the file is
actively changing (≥ 3 recent commits) and sits in the top 20% of repo change
entropy. Tier-aware: silent on ESSENTIAL-tier repos (no co-change walk).

**co_change_scatter**: Breadth of coupling. Counts the distinct files a file
co-changes with above the indexer's recording threshold; a high count means
editing it tends to ripple across the codebase (shotgun surgery). This is the
breadth complement to `hidden_coupling`, which flags specific undeclared
coupled pairs. Fires on actively-changing files (≥ 3 recent commits) coupled to
≥ 8 distinct partners. Tier-aware: silent on ESSENTIAL-tier repos.

## Test quality

These two fire only on test files and live in a deliberately small category
(cap −0.5), so a noisy test never dominates its own health score.

**large_assertion_block**: A test that fires 15 or more assertions in one
uninterrupted run. Such a test usually checks several behaviours at once: when
it fails it points at a line, not a cause, and it's brittle to unrelated
changes. Splitting it into focused cases makes failures legible.

**duplicated_assertion_block**: The same run of assertions copy-pasted across
tests. Reuses the Rabin-Karp clone detector and keeps only the clone regions
that overlap an assertion block on a test file. A change to the asserted
behaviour then has to be edited in several places, and usually isn't, so the
copies drift.

**error_handling**: Swallowed-exception and unsafe-unwrap anti-patterns: an
empty or comment-only `catch`/`except` body, a Python catch-all `except:` /
`except Exception:`, Rust `.unwrap()` / `.expect()` / `panic!`-family macros,
and Go's empty `if err != nil {}` or blank-identifier discard of a call's
error. Detection is precision-first: only the unambiguous shapes fire, and an
unsupported language or parse failure yields no signal rather than a guess.
Each occurrence is a LOW finding anchored to its line, and the whole category
is capped at −0.5 per file: this is an advisory maintainability flag (every
linter is expected to surface `except: pass`), deliberately not a calibrated
defect predictor. On the 21-repo benchmark it is AUC-neutral, so it is
excluded from the weight calibration and bounded so it can never move a file's
score by more than half a point.

## Test coverage

Pass coverage reports straight into the analyzer:

```bash
pytest --cov --cov-report=lcov:coverage.lcov
repowise health --coverage coverage.lcov

# Cobertura, Clover, or multiple sources also work:
repowise health \
  --coverage backend/coverage.xml --coverage-format cobertura \
  --coverage frontend/lcov.info
```

Formats are auto-detected: **LCOV**, **Cobertura** XML, **Clover** XML, and a
**normalized JSON** (`repowise-coverage-v1`) keyed by repo-relative path; the
last lets you feed coverage from any runner once it's mapped to one shape:

```json
{ "format": "repowise-coverage-v1",
  "files": { "src/foo.py": { "line_coverage_pct": 87.5,
                             "total_coverable_lines": 40 } } }
```

Coverage data feeds into `untested_hotspot`, `coverage_gap`, and
`coverage_gradient` (a continuous deduction proportional to the uncovered
fraction), and shows up on the `/repos/<id>/health/coverage` dashboard.

## Refactoring targets

```bash
repowise health --refactoring-targets
```

A health score tells you a file is in trouble; a refactoring target names the
specific fix. Repowise emits one structured `RefactoringSuggestion` per
opportunity, computed deterministically during the health pass from data it has
already produced: the call graph, the class cohesion model, the clone pairs, and
git co-change. No re-parse, no LLM, inside the same <30s budget. Five detectors
ship today:

| Type | What it names | Built from |
|------|---------------|------------|
| **Extract Class** | The cohesion groups an incohesive / god class should split into: the exact methods + fields per group. | LCOM4 union-find components; god-class shape confirmed by Lanza-Marinescu (WMC = Σ McCabe, TCC). |
| **Extract Helper** | A clone's exact occurrences and where the shared helper should live. | Rabin-Karp clone pairs (line ranges + token count + co-change); extraction site = community centroid of the files. Transitive clones are clustered into one suggestion, not pairwise nags. |
| **Move Method** | A feature-envy method and the class it actually belongs to. | Jaccard distance of the method's entity set (fields/methods it touches) to each class over the call graph; fires only when a foreign class is clearly nearer than its own. |
| **Break Cycle** | The minimal set of import edges to invert to break a dependency cycle. | A strongly-connected component, then a greedy minimum feedback arc set over the real import edges. |
| **Split File** | The cohesive files an oversized module should decompose into: which symbols move where, plus the import edits in every dependent. | Community detection (Leiden/Louvain) over a weighted intra-file symbol graph, gated on partition **modularity**. Language-agnostic (reads `defines`/`calls` edges); the file-level analog of Extract Class. |

Each suggestion is structured data, not a string: a `plan` (the split groups,
the move target, the cut edges), the `evidence` that justifies it (LCOM4=3, the
clone ranges, the cycle size), the `impact_delta` (the health deduction it
recovers), an `effort_bucket` (`S`/`M`/`L`/`XL`), and a `blast_radius` (the
callers and co-changing files that must move with it). Human-readable text is
rendered from the structure at the edges (CLI / MCP / web); the structure is the
source of truth.

Ranking is graph-aware. Suggestions sort by `impact_delta × call-graph
centrality × blast_radius`, so a plan on a central hub file outranks the same
plan on a leaf, not the churn-only sort other tools use. The default surface
honors a `min_confidence` gate (`low` / `medium` / `high`, default `medium`).

For agentic workflows, the same data is one MCP call away:

```python
get_health(include=["refactoring"])           # ranked structured plans
get_health(targets=["src/api/server.py"])     # one file in detail
get_health(targets=["module:src.api"])        # everything in a module
```

The web **Refactoring** tab renders each plan as a card (split groups as a tree,
move arrow, clone occurrences with line links, file-split groups with their
residual core and import-rewrite list) with a **copy-to-agent** prompt and an
opt-in **Generate code** action that expands a plan into generated code plus a
unified diff. Code generation runs only on an explicit request, never in the
indexing hot path. It is enabled by default and can be turned off with
`refactoring.llm.enabled: false`. Full reference:
**[docs/REFACTORING.md](REFACTORING.md)**.

## Trends

Every health run writes a `HealthSnapshot` row (rolling 50 entries per repo).
Each snapshot stores the repo KPIs **and** a compact `{path: score}` map, so
the history doubles as a per-file record.

Two repo-level alerts run over the history:

- **Declining Health**: current `hotspot_health` is ≥ 0.5 below the
  snapshot 5 runs ago.
- **Predicted Decline**: the three most recent snapshots are each
  strictly below the one before.

Inspect from the CLI:

```bash
repowise health --trend
```

Or from MCP:

```python
get_health(include=["trend"])
```

### Per-file score over time

The same snapshots power a per-file trajectory: a file's score plotted
across runs (CodeScene's signature view). It surfaces on the file's Health
tab and in the health drawer as a sparkline, with a delta vs. the previous
run and a Declining flag (the per-file version of the alerts above:
≥ 0.5 below the run 5 snapshots back, or three consecutive drops).

A trend is silent on thin history: it needs at least two snapshots that
both carry the file, otherwise the UI shows "no score history yet" rather
than a misleading single dot. Gaps (a file absent from some snapshots) are
skipped, not zero-filled.

Fetch it directly:

```bash
# REST: one file's series + current delta + declining flag
GET /api/repos/{repo_id}/health/files/trend?file_path=path/to/file.py
```

```python
# MCP: targeted mode attaches a per-file `trends` block
get_health(targets=["path/to/file.py"])
```

## File signals

Every file carries process, people, and topology signals we already compute
during indexing. They answer "should I worry about this file?" with context
the score alone can't, and they surface together on the file's Health tab and
in the health drawer, grouped, captioned, and silent ("no signal") when the
underlying data is absent rather than imputed.

| Group | Signal | Means |
|-------|--------|-------|
| Process | Prior defects | Bug-fix commits touching this file in the last ~6 months. `0` is a real, reassuring signal. |
| Process | Change scatter | `change_entropy_pct` (0-100): how spread out its edits are across commits. High = chaotic change. |
| Process | 90-day churn | Commits and lines added/deleted in the trailing 90 days. |
| Process | Age | How long the file has existed in git history. |
| People | Primary owner | The all-time top committer and their commit share. |
| People | Recent owner | The top committer in the last 90 days. A different name from the primary owner flags a knowledge handoff. |
| Topology | Dependents | How many files depend on this one (graph in-degree). |
| Topology | Dependencies | How many files this one depends on (graph out-degree). |

These are pure surfacing: no new measurement, no scoring. Fetch them directly:

```bash
# REST: embedded in the file-detail aggregate and the drawer breakdown
GET /api/repos/{repo_id}/files/{path}                 # data.health.signals
GET /api/repos/{repo_id}/health/files/breakdown?file_path=path/to/file.py
```

```python
# MCP: attached to the get_context health block (null fields dropped)
get_context(targets=["path/to/file.py"], include=["health"])

# MCP: also on get_health targeted mode, one `signals` object per metric
get_health(targets=["path/to/file.py"], include=["signals"])
```

## Hotspot anatomy

Two views dissect where risk concentrates, both plotted from data already on
disk (churn from the git indexer, complexity from the walker, blame at function
granularity).

### Churn × complexity

One dot per recently-changed file: the x-axis is its 90-day commit count
(churn), the y-axis is its max cyclomatic complexity, dot size is NLOC, and dot
color is the health band. Dashed guides sit at the repo's median churn and
median complexity, so the tinted top-right corner reads "busier and more
complex than a typical file here", the refactor zone, where volatility and
tangle collide and defects concentrate. It lives on the **Hotspots & churn**
dashboard tab, toggleable with the churn × bus-factor view.

```bash
# REST: repo-level point list (one point per churned file)
GET /api/repos/{repo_id}/health/churn-complexity
```

```python
# MCP: the same point list, dashboard mode
get_health(include=["churn_complexity"])
```

Files with no recent churn are omitted (they have nothing to say on the churn
axis); complexity is never used to filter, so a high-churn, low-complexity file
still shows in the bottom-right ("changes constantly but stays simple").

### Functions by churn

The file's Health tab lists its functions ranked by modification count, with
the 90-day recent-mod count, median age, and blame owner per function, the
same `git_function_blame` rollup the symbol page uses. It promotes per-function
ownership and volatility out of the buried marker cards into a first-class
table, so "which function in this file is the actual hotspot" is one glance away.

## Configuration

Per-file overrides live in `.repowise/health-rules.json`:

```json
{
  "disabled_biomarkers": ["primitive_obsession"],
  "rules": [
    {
      "path": "tests/**/*.py",
      "disabled_biomarkers": ["large_method", "complex_method"]
    },
    {
      "path": "src/legacy/**",
      "disabled_biomarkers": ["dry_violation"]
    }
  ]
}
```

`path` holds an fnmatch-style glob over the repo-relative POSIX path
(`path_glob` and `glob` are accepted aliases).

### Severity overrides and profiles

A team can soften a signal it treats as advisory without disabling it
outright by remapping its severity (typically a demotion). Overrides apply
repo-wide and per-path; an explicit per-path entry wins over the repo-wide one.

```json
{
  "profile": "small-team",
  "severity_overrides": { "complex_method": "low" },
  "rules": [
    { "path": "src/generated/**", "severity_overrides": { "large_method": "low" } }
  ]
}
```

Accepted severity values are `low`, `medium`, `high`, `critical`. The named
`small-team` profile expands to a preset demotion of the process/people and
noisier structural signals a 1-3 person repo can't support; an explicit
`severity_overrides` key always wins over the preset.

Only the severity label is tunable. The per-marker weight multipliers and
the category caps are the calibrated constants the benchmark numbers rest on
and are deliberately not overridable, so a team's local policy never changes
what the published accuracy claims mean. Markers that carry a continuous
deduction (`coverage_gradient`) are unaffected by severity remaps.

## Incremental updates

`repowise update` only re-scores the changed files. Findings and metrics for
unchanged files stay put, no nightly full re-index needed.

## Status one-liner

`repowise status` includes a single-line health summary (the maintainability and
performance pillars append once the index has populated them):

```
Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: payments/processor.ts) · 7.0 (maintainability) · 9.1 (performance)
```

## Comparison

How the code-health signal compares to the tools it is most often placed next
to. The honest dividing line: each of these has a rules or linter engine and a
definitional rating; repowise predicts which files harbor the next bug and
validates that forward in time against a labeled bug corpus. Each tool also has
real strengths repowise does not claim to beat, noted under the table.

| Capability | Repowise | CodeScene | SonarQube | Code Climate / Qlty¹ | Codacy |
|---|---|---|---|---|---|
| Per-file health / maintainability score | ✅ 1-10, 25 markers | ✅ 1-10 Code Health | ⚠️ A-E ratings from rule counts | ✅ A-F grade | ✅ A-F grade |
| Score uses git / behavioral signals (churn, ownership, co-change, hotspots) | ✅ | ✅ its core | ❌ static rules only | ⚠️ churn-vs-complexity, no co-change | ❌ |
| Cross-file / call-graph analysis | ✅ interprocedural call graph | ⚠️ git temporal coupling, not a static call graph | ⚠️ cross-file taint (security only) | ❌ file-local | ❌ file-local |
| Defect-validated score vs a real bug corpus (published numbers) | ✅ mean ROC AUC 0.737, held-out 0.76-0.78 | ⚠️ "Code Red" study (defect density / cycle time), no per-file AUC | ❌ | ❌ | ❌ |
| Static performance risk (N+1 / IO-in-loop across the call graph) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Test-coverage ingestion | ✅ LCOV/Cobertura/Clover/JSON | ✅ JaCoCo/Cobertura/LCov/Clover | ⚠️ imports reports | ✅ | ✅ |
| Untested-hotspot detection (coverage × risk) | ✅ | ✅ | ❌ | ⚠️ coverage beside hotspots, no composite | ❌ |
| Concrete cross-file refactoring plans (Extract Class / Move Method / Split File) | ✅ + opt-in LLM code-gen | ⚠️ AI fixes 5 in-function smells, no cross-file plan | ❌ flags + AI CodeFix | ❌ flags only | ❌ flags + per-issue AI autofix |
| Health trend tracking + declining-health alerts | ✅ | ✅ | ✅ quality gates, new-code focus | ✅ | ✅ quality gates |
| AI-agent / MCP integration | ✅ MCP tools | ✅ Code Health MCP server | ✅ official MCP server | ❌ | ✅ official MCP + Guardrails |
| Security scanning (adjacent axis) | ⚠️ separate security layer | ⚠️ secondary | ✅ strong (cross-file taint) | ❌ | ✅ full SAST/SCA/secrets/IaC |
| License | ✅ AGPL-3.0, free internal use | ⚠️ free OSS; paid per active author | ⚠️ Community free; paid by LOC | ⚠️ free OSS; paid teams | ⚠️ free OSS; paid per dev |

¹ Code Climate Quality was spun out as Qlty Software in November 2024; "Code
Climate" now sells the Velocity engineering-analytics product. This row tracks
the Quality / Qlty capabilities. SonarQube "Security Hotspots" are
static-analysis security flags, not git-churn hotspots.

What each tool does that repowise does not: **SonarQube** has the broadest
security scanning (cross-file taint analysis), the widest language coverage, and
the most widely adopted merge-gate model. **CodeScene** is the most mature
behavioral-analysis product (knowledge maps, off-boarding simulation, 28+
languages) and has the only published empirical defect study in this group (the
"Code Red" correlation study, Tornhill and Borg, TechDebt 2022). **Code Climate /
Qlty** defined the churn-vs-complexity quadrant and pairs with Velocity for DORA
analytics. **Codacy** has the widest security suite of the four and polished PR
automation.

## See also

- [`packages/core/src/repowise/core/analysis/health/README.md`][hr]:
  developer overview of the layer.
- Sub-package READMEs: `complexity/`, `coverage/`, `duplication/`,
  `biomarkers/`.

[hr]: ../packages/core/src/repowise/core/analysis/health/README.md
