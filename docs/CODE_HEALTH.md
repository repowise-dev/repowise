# Code Health

Repowise computes a 1–10 health score for every file in your repo from twelve
deterministic biomarkers — McCabe complexity, deep nesting, brain methods,
clone detection, untested hotspots, organizational risk, and more. **No LLM
calls, no cloud requirement.** Pure Python over tree-sitter + git data,
designed to finish in under 30 seconds on a 3 000-file repo.

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
| Structural complexity  | −3.5  | brain_method, nested_complexity, bumpy_road |
| Size & complexity      | −2.0  | complex_method, large_method, primitive_obsession |
| Duplication            | −1.5  | dry_violation |
| Test coverage          | −2.0  | untested_hotspot, coverage_gap |
| Organizational         | −1.0  | developer_congestion, knowledge_loss |

The final score is clamped to `[1.0, 10.0]`. The three repo-level KPIs:

- **Hotspot Health** — NLOC-weighted average over the top-25 % hotspot files.
- **Average Health** — NLOC-weighted average over all files.
- **Worst Performer** — single lowest-scoring file.

## The 12 biomarkers

**brain_method** — A single function that is simultaneously long, deeply
nested, highly complex, and central to the dependency graph. The strongest
single signal of fragile code.

**nested_complexity** — Functions with control-flow nesting ≥ 4 levels.
Hard to read, hard to test, hard to refactor.

**bumpy_road** — Multiple branches stacked at the same depth — usually a
sign the function is doing several jobs that should be split.

**complex_method** — Cyclomatic complexity ≥ 9. Each branch is a path the
test suite has to cover.

**large_method** — Functions that exceed the NLOC threshold. Length on its
own is not always a bug, so this is a milder signal.

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
on the project. Refactor while someone still remembers why.

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
| Code health score (1–10)         | ✅ 12 biomarkers | ✅ 25–30 | ❌ | ❌ |
| Brain Method detection           | ✅ | ✅ | ❌ | ❌ |
| Test coverage intelligence       | ✅ LCOV/Cobertura/Clover | ❌ | ❌ | ❌ |
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
