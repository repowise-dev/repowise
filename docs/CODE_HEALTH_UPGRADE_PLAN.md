# Code Health Layer Upgrade Plan

**Goal:** Move the code health layer ahead of CodeScene by adding four new biomarkers
that capture signals the current structural-only set is blind to, and recalibrate the
scoring engine so the strongest empirical predictors (process-aware biomarkers) are
no longer suppressed by category caps.

**Status:** Planning. Implementation will land across multiple PRs.

**Owner context:** Another Claude session is editing concurrently — do not touch
`packages/core/src/repowise/core/analysis/health/**` or
`packages/core/src/repowise/core/ingestion/git_indexer/**` from this session.

---

## 1. Baseline — what we have today

Verified against current source (2026-05-23):

### 1.1 Existing biomarkers (12)
| Category | Biomarkers | Current cap |
|---|---|---|
| `structural_complexity` | brain_method, nested_complexity, bumpy_road | **3.5** |
| `size_and_complexity` | complex_method, large_method, primitive_obsession | **2.0** |
| `duplication` | dry_violation | **1.5** |
| `test_coverage` | untested_hotspot, coverage_gap | **2.0** |
| `organizational` | developer_congestion, knowledge_loss | **1.0** ← suppressed |

Severity → raw deduction: LOW 0.3 · MEDIUM 0.7 · HIGH 1.2 · CRITICAL 2.0.
Scoring is per-category cap with proportional scaling, then clamped to [1.0, 10.0].
Snapshot test (`test_scoring_snapshot.py`) locks every value above — any change
**must** update the snapshot in the same PR.

### 1.2 Data sources available
- **Walker** (`analysis/health/complexity/walker.py`) emits per-function
  `FunctionComplexity{name, start_line, end_line, ccn, max_nesting, cognitive, nloc, bumps, param_count}`.
  It already traverses condition expressions and counts `&&`/`||`/`and`/`or` toward CCN
  — but does **not** report per-condition operator counts.
- **Git metadata** (`ingestion/git_indexer/file_history.py`) is **file-level only**.
  Includes `commit_count_*`, `bus_factor`, `primary_owner_*`, `co_change_partners_json`,
  `temporal_hotspot_score`, `churn_percentile`. **No per-line blame retained.**
- **Co-change** (`ingestion/git_indexer/co_change.py`) computes exponentially-decayed
  pair scores in a single `git log --name-only` pass, persisted bidirectionally in
  each file's `co_change_partners_json`.
- **Graph** (`ingestion/graph.py`) is a NetworkX `DiGraph` with `imports`, `defines`,
  `has_method`, `calls` edge types. Available to `HealthAnalyzer` for `in_degree`,
  `out_degree`, and `has_edge` queries.

### 1.2.1 Tiered git indexing (added by PR #220 — read before designing)

`ingestion/git_indexer/tiers.py` introduced `GitIndexTier`:

- `FULL` — historical behaviour. Runs `git blame` per file and the co-change
  accumulator. Default for normal repos.
- `ESSENTIAL` — skips blame **and** co-change accumulation. Used by the
  fast-path orchestrator for 30k-file repos. `co_change_partners_json` is empty,
  ownership falls back to commit-author. Expensive signals are filled in later
  by `ingestion/git_indexer/backfill.py`.

**Implications for every new biomarker below:**
1. `hidden_coupling` reads `co_change_partners_json`, which is empty on
   ESSENTIAL tier until backfill runs. The detector must no-op cleanly
   (no findings, no errors) rather than misreport. The `commit_count_total`
   noise floor in §2.1 covers this incidentally, but make the empty-partners
   short-circuit explicit and add a test.
2. `function_hotspot` and `code_age_volatility` need per-line blame. The new
   `BlameIndex` infrastructure (PR 4) **must integrate with `backfill.py`**,
   not bypass the tier system. Concretely: extend `GitIndexTier.includes_blame`
   semantics so FULL produces `BlameIndex` alongside ownership, and add a
   `backfill_blame()` entry point that ESSENTIAL repos can run on demand.
   Otherwise large-repo users lose the perf win the tiered indexer was built
   for.
3. All four new biomarkers should accept a "no signal" outcome and not error
   when git_meta is sparse. Existing biomarkers already do this — match their
   pattern.

### 1.3 Gaps blocking the new biomarkers
| Need | Status | What's missing |
|---|---|---|
| Per-function modification count | ❌ | No diff-to-function attribution and no per-line commit-sha retention |
| Per-line author/timestamp | ❌ | `get_blame_ownership` aggregates and discards lines |
| Per-condition boolean-op count | ❌ | Walker counts toward CCN but doesn't emit per-condition |
| Co-change ↔ graph join | ✅ | Both data sources exist, no biomarker joins them |
| Benchmark / effect-size harness | ❌ | No code; δ numbers cited are from external work |

---

## 2. New biomarkers

Each new biomarker follows the "Adding a new biomarker" recipe verified in code:
1. New file under `analysis/health/biomarkers/<name>.py` implementing the `Biomarker` protocol from `biomarkers/base.py`.
2. Register in `biomarkers/registry.py::_DETECTOR_FACTORIES`.
3. Add to `scoring._BIOMARKER_CATEGORY`.
4. Suggestion template in `suggestions._TEMPLATES`.
5. 3+ tests under `tests/unit/health/test_<name>.py` (2 positive, ≥1 negative, ideally cross-language).
6. Update `analysis/health/biomarkers/README.md` + `docs/CODE_HEALTH.md`.
7. **Update `test_scoring_snapshot.py`** in the same PR.

### 2.1 `hidden_coupling` — Phase 1 (low risk, ship first)

**Detects:** Pairs of files that change together in git history but have no
import/dependency edge between them. Captures behavioral coupling invisible to
static analysis (config conventions, hidden test fixtures, shared protocols).

**Category:** `organizational` (process-aware signal, not structural).

**Algorithm:**
```
for each file F with co_change_partners_json entries:
    partners = parse_co_change_partners(ctx.git_meta)        # already a helper
    total_commits = ctx.git_meta["commit_count_total"]
    for partner_path, co_change_count in partners.items():
        partner_commits = repo_commit_counts.get(partner_path, 0)
        if total_commits < MIN_COMMITS or partner_commits < MIN_COMMITS:
            continue                                          # noisy
        correlation = co_change_count / min(total_commits, partner_commits)
        if correlation < 0.5:
            continue
        if graph.has_edge(F, partner_path, key="imports") \
           or graph.has_edge(partner_path, F, key="imports"):
            continue                                          # explicit dep — fine
        emit finding(severity_from_correlation(correlation),
                     details={"partner": partner_path,
                              "correlation": correlation,
                              "co_change_count": co_change_count})
```

**Engineering notes:**
- `FileContext` already carries the graph reference indirectly via `dependents_count`
  — we need to extend it with a thin `graph_view: HasEdge` (a Protocol, not the full
  `DiGraph`) so biomarkers stay testable without NetworkX in unit tests.
- `repo_commit_counts` needs to be a new optional dict passed through from
  `HealthAnalyzer.analyze` (built once from `git_meta_map`).
- Severity scale: CRITICAL ≥0.8, HIGH ≥0.65, MEDIUM ≥0.5. Floor at 0.5 per the spec.
- Emit a finding on **both** files in the pair (each one is the partner of the other).
- Co-change is already bidirectional in storage, so we naturally dedupe in tests
  by `frozenset({a, b})`.

**Why organizational:** This is a behavioral signal of poor modularity. Should
contribute to that category's expanded cap (§3).

**Tests:**
- Positive: two Python files that share commits in fixture git history, no import edge.
- Positive: TS pair where one file is `__tests__/foo.test.ts` and partner is `bar.ts`
  (test↔production should NOT flag — add filter that ignores test→prod pairs).
- Negative: explicit `imports` edge present.
- Negative: low total commit count (under noise floor).

### 2.2 `complex_conditional` — Phase 1

**Detects:** Boolean expressions inside `if` / `for` / `while` / ternary / case
guards with ≥3 boolean operators (`&&`, `||`, `and`, `or`, `not`).

**Category:** `structural_complexity`.

**Walker change:** Extend `FunctionComplexity` with:
```python
@dataclass
class ConditionComplexity:
    line: int
    operator_count: int
    enclosing_construct: str    # "if" | "while" | "for" | "ternary" | "case"

@dataclass
class FunctionComplexity:
    ...                          # existing fields untouched
    complex_conditions: list[ConditionComplexity]
```
Walker already calls `_is_boolean_operator` while computing CCN. Add a sibling
helper `_count_boolean_ops_in_condition(node)` that walks the condition subtree
and counts hits. Wire it into the existing branch/loop visit sites so we get the
right enclosing construct without a second AST pass.

**Per-language coverage:** Already supported via `LanguageNodeMap.branch_kinds`
and `loop_kinds`. The condition subtree differs per language — use named children
where tree-sitter exposes them (`condition` field on Python `if_statement`,
`condition` on TS `if_statement`, etc.). Where named fields aren't available
(Go/Java/Rust have different shapes), the spec is: count boolean ops anywhere in
the construct's direct children **except** the body/consequence/alternative.
Document the carve-out in `complexity/languages.py`.

**Detector:** Iterate `ctx.function_metrics[fn].complex_conditions`, emit one
finding per condition with `operator_count >= 3`. Severity:
3 ops MEDIUM · 4–5 HIGH · 6+ CRITICAL.

**Tests:** Per-language fixtures for Python, TS, Go, Java, Rust — each with one
3-op, one 6-op, and one 2-op (negative) condition. The walker test
(`test_complexity_walker.py`) gets new assertions on the new field.

### 2.3 `function_hotspot` — Phase 2 (requires git-layer work)

**Detects:** Functions that are both structurally complex AND frequently modified.
File-level hotspot misses the case where one file has a stable scaffold + one
hot, ugly function.

**Category:** `organizational`.

**Blocker:** No per-function churn today. Two implementation options — pick (A):

**Option A (recommended) — blame-based, line-range proxy:**
- New helper `ingestion/git_indexer/function_blame.py::blame_function_commits(
    repo, path, start_line, end_line) -> set[str]` running
  `git blame --line-porcelain -L start,end -- path` once per file, parsing the
  output once, and indexing line → sha. Caller projects each function's line
  range into the set of distinct shas touching those lines.
- Add `function_modification_counts: dict[function_name, int]` to the per-file
  `git_meta`. Computed lazily — only when health analysis is enabled, only on
  files the walker found functions in (skip vendor / generated).
- Cost concern: blame is the slowest git operation. Three mitigations:
  1. Skip files where `commit_count_total < 5` (no signal possible).
  2. Skip files over a size cap (reuse `_MAX_BLAME_SIZE_BYTES` already in
     `file_history.py`).
  3. Run blame in parallel via the existing `ThreadPoolExecutor` pattern in
     `git_indexer/__init__.py`.
- Stale-on-rename risk: blame follows renames by default, which is fine; line
  ranges from the walker are HEAD-relative, which matches blame's HEAD ranges.

**Option B — diff parsing (rejected):** Walking every commit's diff and mapping
hunks to AST nodes is precise but O(commits × files × parse_time). Defer unless
benchmark shows Option A misses too many real cases.

**Detector:** Computes per-repo p80 over `function_modification_counts`, flags
functions with `mod_count >= p80 AND (ccn >= 10 OR max_nesting >= 3)`.
Severity by combined Z-score; CRITICAL when both axes are top-decile.

**Tests:** Fixture repo with synthetic git history. Use existing
`tests/conftest.py` git-history builder pattern (if present — verify) or create
one if missing.

**If we cannot ship A in this iteration:** Document the contract, ship the
detector with a feature flag that emits nothing, and add a regression test that
asserts zero findings when `function_modification_counts` is absent. This keeps
the API stable for the follow-up PR.

### 2.4 `code_age_volatility` — Phase 2 (requires per-line timestamps)

**Detects:** Dormant code suddenly being modified. Strong signal that someone is
working in unfamiliar territory — the highest-risk edit profile.

**Category:** `organizational`.

**Blocker:** No per-line author_time today. Reuses the blame infrastructure from
§2.3 — `git blame --line-porcelain` exposes `author-time <unix_ts>` per line.

**Algorithm:**
```
median_line_age_days = median(now - author_time for line in function)
recent_mod_count    = count of distinct shas touching function in last 30d
if median_line_age_days >= 365 and recent_mod_count >= 2:
    severity = CRITICAL if median_age >= 730 and recent_mod >= 5 else HIGH/MEDIUM
    emit(function, median_age_days, recent_mod_count)
```

**Engineering notes:**
- Cache the blame parse from §2.3 — both biomarkers consume the same raw output.
  Add `BlameIndex` dataclass: `{line_no: (sha, author_time)}` materialised once
  per file and passed into both detectors via `FileContext`.
- The 30-day window crosses the `commit_count_30d` field already in
  `git_meta` — reuse it as a sanity guard (skip if the file has no recent commits).

**Fallback if per-line timestamps prove infeasible** (storage or perf concern):
Approximate with file-level `first_commit_at` + `commit_count_30d` + LOC
distribution. Document explicitly that this is a coarse proxy and note it on
the finding (`details: {"approximation": "file_level_age"}`).

**Tests:** Synthetic git history with old + recent commits in the same file.

---

## 3. Scoring recalibration

The current weights were eyeballed pre-evidence. Cited benchmark δ values on
FastAPI / Pydantic / Django:

| Biomarker | δ (effect size, Cliff's) | Implication |
|---|---|---|
| developer_congestion | **+0.78** (Django) | Strongest single predictor; cap is starving it |
| untested_hotspot | strong positive | Process × structure interaction |
| knowledge_loss | **negative** in 2/3 repos | OSS legacy gets handed off because it works — *deprioritise* |
| brain_method, complex_method | moderate | Keep |
| nested_complexity | moderate | Keep |

### 3.1 New `CATEGORY_CAPS` (proposed)

```python
CATEGORY_CAPS: dict[str, float] = {
    "organizational":         3.5,   # was 1.0 — was the worst miscalibration
    "structural_complexity":  2.5,   # was 3.5
    "test_coverage":          2.0,   # unchanged
    "size_and_complexity":    1.5,   # was 2.0
    "duplication":            1.0,   # was 1.5
}
```
Total uncapped headroom: 10.5 (was 10.0) — keeps the 1–10 clamp meaningful.

### 3.2 New `_SEVERITY_DEDUCTION` (proposed)

The flat severity table treats all biomarkers as equally severe; with the
recalibration we should let predictive power per biomarker matter. Two options:

**Option A (light):** Keep the global severity table but add a
`_BIOMARKER_WEIGHT_MULTIPLIER` dict applied before category capping:
```python
_BIOMARKER_WEIGHT_MULTIPLIER: dict[str, float] = {
    "developer_congestion": 1.5,    # δ=0.78 leader
    "untested_hotspot":     1.3,
    "function_hotspot":     1.2,
    "hidden_coupling":      1.0,
    "knowledge_loss":       0.4,    # went negative in 2/3 — keep but de-rate
    # all others: 1.0 default
}
```
Per-finding raw deduction = severity_deduction * multiplier. Easy to tune,
backward-compatible.

**Option B (heavier):** Replace the flat severity table with a per-biomarker
(severity → deduction) matrix. More precise but more knobs to tune without
evidence. **Recommend A** for this iteration.

### 3.3 `_BIOMARKER_CATEGORY` additions

```python
"hidden_coupling":      "organizational",
"complex_conditional":  "structural_complexity",
"function_hotspot":     "organizational",
"code_age_volatility":  "organizational",
```

### 3.4 Snapshot test update

`test_scoring_snapshot.py` must change in the same PR. Two fixture scores
recomputed by hand and committed alongside. Consider adding a third fixture
that exercises the new biomarkers so regressions are caught.

### 3.5 Knowledge_loss treatment

Don't delete it — the negative δ comes from OSS bias (stable legacy is handed
off because it works). Two paths:
1. Drop multiplier to 0.4 (above) so it still surfaces in findings list but
   barely moves the score.
2. Add a per-repo override config so enterprise users (where attrition is
   actually a risk) can raise it back. Out of scope for this PR — note as
   stretch.

---

## 4. Implementation order

Sequenced to ship value early and keep PRs reviewable:

1. **PR 1 — Recalibration only.** Change `CATEGORY_CAPS`, add
   `_BIOMARKER_WEIGHT_MULTIPLIER`, update snapshot. Zero new code paths. Easy to
   revert if downstream consumers (web UI, MCP) surface trouble.
2. **PR 2 — `hidden_coupling`.** Pure join over data we already persist. Adds
   `graph_view` to `FileContext`. Single new biomarker, no walker change.
3. **PR 3 — Walker extension + `complex_conditional`.** Adds
   `ConditionComplexity` to walker output, per-language carve-outs, biomarker
   + tests. Touches the walker — most risk of regression — so isolate it.
4. **PR 4 — Blame index infrastructure** (`function_blame.py` +
   `BlameIndex` plumbed into `FileContext`). No new findings yet. Performance
   test (the 30s budget) is the gate; if we blow it, walk back parallelism
   choices. **Must integrate with `git_indexer/tiers.py` and `backfill.py`** —
   FULL tier produces the index inline, ESSENTIAL tier defers to a new
   `backfill_blame()` entry point. Do not bypass the tier system.
5. **PR 5 — `function_hotspot`** consuming the index from PR 4.
6. **PR 6 — `code_age_volatility`** consuming the same index.
7. **PR 7 — Benchmark harness** (see §5).

PR 1 is independent and unblocked. PRs 2 and 3 are independent of each other.
PRs 4-6 are a chain. PR 7 can land any time after PR 1.

---

## 5. Stretch / supporting work

### 5.1 Benchmark harness (high priority — codify what's currently ad-hoc)
Add `tools/health-bench/` (separate package, not core). Inputs: cloned repo +
labelled bug-fix commit list. Outputs: per-biomarker Cliff's δ between
"files touched in bug-fix commits" and "all other files". Cache results in a
CSV checked into the harness so weight changes can be A/B'd. Pre-load
FastAPI / Pydantic / Django labels so the cited numbers become reproducible.

Without this, every future weight change is back to eyeballing. With it, the
recalibration in §3 becomes evidence-backed and we get a regression signal
when biomarker logic drifts.

### 5.2 Cross-repo (workspace mode) hidden coupling
Today `hidden_coupling` is intra-repo. In workspace mode (repowise indexes
multiple repos at once), the same join across repos catches "secret API
contracts" — repo A and repo B coordinating without an explicit shared schema.
Higher false-positive rate; defer until §5.1 is in place to measure.

### 5.3 Bottleneck biomarker
High fan-in (graph in_degree top decile) × high churn × low coverage = the
files most likely to ship a regression that breaks many callers. All three
inputs already exist. One small detector, one new entry. Probably belongs in
`structural_complexity` (it's a centrality signal).

### 5.4 Module-level scoring rollup
Today scoring is file-level. CodeScene's killer feature is "code health by
sub-system." We already have `HealthFileMetricData.module` from graph
community detection — just don't aggregate. Add a `HealthModuleMetric` table
+ rollup pass in the engine. Worth it: most architectural conversations
happen at module granularity.

### 5.5 Predicted decline — replace direction-check with linear regression
Today's trend signal is a 3-snapshot direction check. Linear regression over
the rolling 50 snapshots (already retained) gives a calibrated "weeks to
threshold" estimate. Single function in `analysis/health/trends.py`.

### 5.6 Walker condition-depth metric (already partially landed)
Once `complex_conditional` ships, `nested_complexity` can optionally include
condition depth (`if (a && (b || c))` reads as nested even if formatted flat).
Defer — measure first.

### 5.7 Per-repo weight overrides
Move `CATEGORY_CAPS` and `_BIOMARKER_WEIGHT_MULTIPLIER` into a YAML loadable
from `.repowise.yml`. Lets teams (esp. enterprise) tune to their context
without forking. Touches docs/CLI surface — separate PR after the harness.

### 5.8 Surface `_meta.stale_warning` on health endpoints
The MCP layer already has a stale-warning envelope. The health REST/MCP
endpoints should set the same field when the index commit lags HEAD by N
commits — health findings get noisier than overview findings as code drifts.

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Blame perf regression breaks the 30s budget on 3k-file repos | PR 4 ships index-only with a hard perf test before any detector consumes it. Parallel + size-gated. |
| Walker changes regress CCN/nesting on existing fixtures | Add condition extraction as an additive pass; existing `_recurse` logic untouched. Snapshot test on walker output. |
| Snapshot test becomes a chore, gets blindly updated | Require PR description to include before/after fixture scores for any scoring change. Add a CI comment that diffs the snapshot. |
| `hidden_coupling` floods findings on monorepos with shared config | Noise floor: skip pairs where either file's `commit_count_total < 5`. Skip test↔production pairs explicitly. Cap findings per file (top 3 partners). |
| Knowledge_loss multiplier of 0.4 silently breaks enterprise use case | Document explicitly in `docs/CODE_HEALTH.md` that knowledge_loss is OSS-calibrated; flag per-repo overrides as the enterprise path. |
| Recalibration changes existing customer scores significantly | This is the point — but call it out in release notes as a one-time recalibration with a link to the methodology. Provide a flag to opt back into old weights for one release. |

---

## 7. What this plan does NOT do

- No LLM-generated suggestions (Phase 5 territory).
- No Java/Kotlin/Scala/Ruby walker support — handled separately.
- No coverage data ingestion changes.
- No UI changes — the new biomarkers will appear automatically once
  `_BIOMARKER_CATEGORY` is updated, but bespoke UI affordances (e.g., a
  hidden-coupling visualization) are out of scope.
- No backward-compat shim for old weights — see risk row 6 for the opt-back
  flag if needed.

---

## 8. Open questions

1. **Co-change correlation denominator.** Spec says ">50% co-change." I've
   proposed `co_change_count / min(total_A, total_B)`. Alternative is
   `co_change_count / max(total_A, total_B)` (stricter) or Jaccard
   (`co_change / |A ∪ B|`). Pick before PR 2. Recommend `min` — captures
   "every time the smaller-churn file moves, the other one does too."
2. **Blame parallelism scope.** Per-file blame in a thread pool is straightforward,
   but on Windows the existing indexer doesn't parallelise git ops (FS locks).
   Verify before PR 4 by checking `git_indexer/__init__.py`'s current pool usage.
3. **Should `function_hotspot` and `code_age_volatility` ship before the harness?**
   Yes — we have the spec, the data is real, and we can re-tune later. But the
   harness should land **before** any further recalibration so we're not eyeballing
   twice.
4. **Severity threshold for `complex_conditional` at exactly 3.** Spec says ≥3.
   3 ops covers `a && b && c` which is often readable. Consider bumping to LOW
   severity at 3, MEDIUM at 4, HIGH at 5, CRITICAL at 6. Decide during PR 3.
