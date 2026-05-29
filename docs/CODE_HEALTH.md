# Code Health

Repowise computes a 1–10 health score for every file in your repo from twenty-four
deterministic biomarkers — McCabe complexity, deep nesting, brain methods,
class cohesion (LCOM4), god classes, clone detection, untested hotspots,
function-level churn, code-age volatility,
ownership dispersion, relative churn, change entropy, co-change scatter,
recent defect history, test-quality smells, and more. **No LLM calls, no cloud requirement.** Pure
Python over tree-sitter + git data, designed to finish in under 30 seconds on a
3 000-file repo.

## Quick start

```bash
repowise init          # full index — populates health tables
repowise health        # KPIs + 20 worst-scoring files + top findings
repowise update        # re-score only changed files on each subsequent run
```

Open `http://localhost:7777/repos/<id>/health` for the dashboard once the
local server is running (`repowise serve`).

## The score

Each file starts at 10.0. Biomarker findings deduct from the score; deductions
are capped per category so any one category can drive the score down by at
most:

| Category               | Cap   | Biomarkers |
|------------------------|-------|------------|
| Organizational         | −3.5  | developer_congestion, knowledge_loss, hidden_coupling, function_hotspot, code_age_volatility, ownership_risk, churn_risk, change_entropy, co_change_scatter |
| Structural complexity  | −2.5  | brain_method, low_cohesion, god_class, nested_complexity, bumpy_road, complex_conditional |
| Test coverage          | −2.0  | untested_hotspot, coverage_gap |
| Size & complexity      | −1.5  | complex_method, large_method, primitive_obsession |
| Duplication            | −1.0  | dry_violation |
| Test quality           | −0.5  | large_assertion_block, duplicated_assertion_block |

Twenty-three biomarkers across six categories. `function_hotspot` and
`code_age_volatility` are blame-based and sit in the organizational bucket —
both are tier-aware and stay silent on ESSENTIAL-tier repos until the per-line
blame index is built.

Per-biomarker weight multipliers (see `scoring._BIOMARKER_WEIGHT_MULTIPLIER`)
let the strongest empirical predictors deduct more than the uniform severity
table alone allows. **These weights are calibrated offline against a defect
corpus, not hand-tuned**: each file is scored at the pre-window commit (T0, no
leakage) and an L2-logistic regression — with NLOC as an explicit control — fits
each biomarker's defect lift *beyond file size*. The runtime stays
deterministic; only the learned constants ship. The strongest calibrated
predictors are `co_change_scatter` (1.8), `change_entropy` (1.51),
`ownership_risk` (1.38), and `nested_complexity` (1.34); the remaining
structural complexity/size biomarkers land around 1.1–1.33. Biomarkers that fire widely but
proved weak under leakage-free scoring (`developer_congestion`, `dry_violation`,
`low_cohesion`, `brain_method`, `primitive_obsession`, `bumpy_road`) are floored
to 0.5 — kept as maintainability/parity signals, not disabled — and
`knowledge_loss` stays de-rated at 0.4. Coverage-dependent and rarely-firing
biomarkers (`untested_hotspot` 1.3, `code_age_volatility` 1.1, `churn_risk` 1.2)
keep prior weights the corpus could not fairly measure. The calibration is
reproduced by `local-stash/calibrate_health_weights.py` and documented in
`repowise-bench/health-defect/BENCHMARK_REPORT.md`.

The final score is clamped to `[1.0, 10.0]`. The three repo-level KPIs:

- **Hotspot Health** — NLOC-weighted average over the top-25 % hotspot files.
- **Average Health** — NLOC-weighted average over all files.
- **Worst Performer** — single lowest-scoring file.

## The biomarkers

**brain_method** — A single function that is simultaneously long, deeply
nested, highly complex, and central to the dependency graph. The strongest
single signal of fragile code. Centrality is judged against the repo's own
dependency density (top-quintile of connected files, with an absolute
hub bar), so it fires on sparse-graph languages too — not just Python.

**low_cohesion** — A class whose methods split into groups that share no
fields and don't call each other (LCOM4 ≥ 2). Measured by the walker's
class-level model; a high value usually means several smaller,
single-responsibility classes are hiding inside one.

**god_class** — A large class (≥ 200 lines, ≥ 15 methods) that also
contains a brain method. Size alone isn't flagged — the brain-method
requirement keeps flat data holders and config tables from firing.

**nested_complexity** — Functions with control-flow nesting ≥ 4 levels.
Hard to read, hard to test, hard to refactor.

**bumpy_road** — Multiple branches stacked at the same depth — usually a
sign the function is doing several jobs that should be split.

**complex_method** — Cyclomatic complexity ≥ 9. Each branch is a path the
test suite has to cover.

**large_method** — Long functions that also carry at least some branching.
A long-but-perfectly-flat body (a big config/data literal, a wall of
sequential assignments) is a layout artefact rather than a complexity smell,
so it is excluded — the trigger is about length-with-substance, not raw line
count.

**primitive_obsession** — Many primitive parameters in one signature. A
dataclass or parameter object would name the inputs.

**dry_violation** — Cross-file code clones, detected by a native Rabin–Karp
rolling hash over tree-sitter tokens (variable renames don't hide a clone).
Pairs are ranked by co-change so dormant duplicates rank lower than active
ones.

**untested_hotspot** — A hotspot file with low or zero coverage and many
dependents. The textbook "write tests before refactoring" case.

**coverage_gap** — Non-test files with meaningful uncovered surface.
Severity grades along coverage depth.

**developer_congestion** — Too many active authors touching the same file.
Usually an ownership problem dressed up as a code problem.

**knowledge_loss** — The primary authors of the file are no longer active
on the project. Refactor while someone still remembers why. Gated on recent
activity — an abandoned-but-stable file is low risk (the survivor effect),
so this only fires while the code is still being changed.

**hidden_coupling** — Files that consistently change in the same commits
without an explicit import or dependency edge between them. Captures
behavioral coupling (shared protocols, parallel config, copy-pasted
constants) that static analysis cannot see. Tier-aware: empty on
ESSENTIAL-tier repos until co-change backfill runs.

**complex_conditional** — Branch / loop guards that combine three or more
boolean operators. Severity grows with the operator count (LOW at 3, MED
at 4, HIGH at 5, CRIT at 6+).

**function_hotspot** — Functions that are both structurally complex and
frequently modified. Per-function modification counts come from a
per-line blame index built once per file (FULL git tier) and shared
with `code_age_volatility`. Fires when a function's distinct-commit
count is at or above the repo-wide p80 AND the function meets a
structural floor (CCN ≥ 10 or max nesting ≥ 3). Tier-aware: returns no
findings on ESSENTIAL-tier repos until `backfill_blame()` runs.

**code_age_volatility** — Functions whose median line age is at least a
year old that are suddenly being modified. Strong defect predictor:
the editor is usually working in unfamiliar territory. Uses the same
per-line blame index — `median_age_days` from per-line author
timestamps, `recent_mod_count` from distinct shas inside the last 30
days. Severity escalates with both axes (CRIT when median age ≥ 2y AND
≥ 5 recent commits). Tier-aware: same ESSENTIAL no-op as
`function_hotspot`.

**ownership_risk** — Long-run ownership dispersion. Counts *minor
contributors* — authors who each own less than 5% of the file's commits —
and the dominant owner's share. Many drive-by authors with no clear owner is
the single strongest defect correlate in the literature (Bird et al.). Fires
on files with ≥ 5 commits where ≥ 3 contributors are minor or no owner holds
40%. Complements `developer_congestion`, which measures *active* (90-day)
contention rather than lifetime dispersion.

**churn_risk** — Relative churn: the fraction of a file's lines rewritten in
the last 90 days, normalized by file size. A file whose recent window rewrote
more lines than it contains is structurally unstable regardless of how big it
is. Because the trigger is a ratio to NLOC, it does not simply re-flag large
files. Fires when the file is actively churning (≥ 5 recent commits, top
quartile of repo churn) and relative churn ≥ 1.0.

**change_entropy** — How scattered a file's change history is, adapted from
Hassan's History Complexity Metric. Each commit is treated as a one-period
window whose entropy is `log2(files-touched)`, distributed across its files and
decayed over time. A file repeatedly caught up in wide, scattered commits
scores high; one changed in focused, single-purpose commits stays low even if
it changes often — so this is *not* a churn proxy. Fires when the file is
actively changing (≥ 3 recent commits) and sits in the top 20% of repo change
entropy. Tier-aware: silent on ESSENTIAL-tier repos (no co-change walk).

**co_change_scatter** — Breadth of coupling. Counts the distinct files a file
co-changes with above the indexer's recording threshold; a high count means
editing it tends to ripple across the codebase (shotgun surgery). This is the
breadth complement to `hidden_coupling`, which flags *specific* undeclared
coupled pairs. Fires on actively-changing files (≥ 3 recent commits) coupled to
≥ 8 distinct partners. Tier-aware: silent on ESSENTIAL-tier repos.

## Test quality

These two fire **only on test files** and live in a deliberately small
category (cap −0.5), so a noisy test never dominates its own health score.

**large_assertion_block** — A test that fires 15 or more assertions in one
uninterrupted run. Such a test usually checks several behaviours at once: when
it fails it points at a line, not a cause, and it's brittle to unrelated
changes. Splitting it into focused cases makes failures legible.

**duplicated_assertion_block** — The same run of assertions copy-pasted across
tests. Reuses the Rabin–Karp clone detector and keeps only the clone regions
that overlap an assertion block on a test file. A change to the asserted
behaviour then has to be edited in several places — and usually isn't, so the
copies drift.

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
**normalized JSON** (`repowise-coverage-v1`) keyed by repo-relative path — the
last lets you feed coverage from any runner once it's mapped to one shape:

```json
{ "format": "repowise-coverage-v1",
  "files": { "src/foo.py": { "line_coverage_pct": 87.5,
                             "total_coverable_lines": 40 } } }
```

Coverage data feeds into `untested_hotspot` and `coverage_gap`, and shows up
on the `/repos/<id>/health/coverage` dashboard.

## Refactoring targets

```bash
repowise health --refactoring-targets
```

Ranks candidates by `total_impact / effort_bucket` so the biggest wins for
the least work surface first. Each row carries a deterministic, rule-based
suggestion (`"Split this function. It carries high cyclomatic complexity..."`).

For agentic workflows, the same data is one MCP call away:

```python
get_health(include=["refactoring"])           # dashboard + suggestions
get_health(targets=["src/api/server.py"])     # one file in detail
get_health(targets=["module:src.api"])        # everything in a module
```

## Trends

Every health run writes a `HealthSnapshot` row (rolling 50 entries per repo).
Two alerts run over the history:

- **Declining Health** — current `hotspot_health` is ≥ 0.5 below the
  snapshot 5 runs ago.
- **Predicted Decline** — the three most recent snapshots are each
  strictly below the one before.

Inspect from the CLI:

```bash
repowise health --trend
```

Or from MCP:

```python
get_health(include=["trend"])
```

## Configuration

Per-file overrides live in `.repowise/health-rules.json`:

```json
{
  "disabled_biomarkers": ["primitive_obsession"],
  "rules": [
    {
      "glob": "tests/**/*.py",
      "disabled_biomarkers": ["large_method", "complex_method"]
    },
    {
      "glob": "src/legacy/**",
      "disabled_biomarkers": ["dry_violation"]
    }
  ]
}
```

## Incremental updates

`repowise update` only re-scores the changed files. Findings and metrics for
unchanged files stay put — no nightly full re-index needed.

## Status one-liner

`repowise status` includes a single-line health summary:

```
Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: payments/processor.ts)
```

## Comparison

| Feature                          | Repowise | CodeScene | DeepSource | Sourcery |
|----------------------------------|:--:|:--:|:--:|:--:|
| Code health score (1–10)         | ✅ 24 biomarkers | ✅ 25–30 | ❌ | ❌ |
| Brain Method detection           | ✅ | ✅ | ❌ | ❌ |
| Low cohesion (LCOM4) / god class  | ✅ | ✅ | ❌ | ❌ |
| Test coverage intelligence       | ✅ LCOV/Cobertura/Clover/JSON | ❌ | ❌ | ❌ |
| Untested hotspot detection       | ✅ coverage × hotspot | ❌ | ❌ | ❌ |
| DRY violation detection          | ✅ native (no npm) | ✅ | ❌ | ❌ |
| Health trend tracking            | ✅ | ✅ | ❌ | ❌ |
| Declining health alerts          | ✅ | ✅ | ❌ | ❌ |
| Refactoring recommendations      | ✅ deterministic | ✅ | ❌ | ❌ |
| Free for internal use            | ✅ AGPL-3.0 | ❌ $15–30/author | ✅ public repos | ❌ |

## See also

- [`packages/core/src/repowise/core/analysis/health/README.md`][hr] —
  developer overview of the layer.
- Sub-package READMEs: `complexity/`, `coverage/`, `duplication/`,
  `biomarkers/`.

[hr]: ../packages/core/src/repowise/core/analysis/health/README.md
