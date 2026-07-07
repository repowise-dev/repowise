# Code Health analysis layer

Fifth intelligence layer alongside Graph, Git, Docs, and Decisions. Computes a
per-file health score (1.0–10.0) from twenty-six deterministic markers,
ingests test-coverage data, tracks repo-level KPIs over time, and surfaces
refactoring targets ranked by impact-per-effort.

**Zero LLM calls.** Pure Python over tree-sitter + git data. Designed to finish
in under 30 s on a 3 000-file repo (see `tests/integration/test_health_perf_benchmark.py`).

## Public API

```python
from repowise.core.analysis.health import HealthAnalyzer, HealthReport

analyzer = HealthAnalyzer(
    graph,
    git_meta_map=git_meta_map,
    parsed_files=parsed_files,
    coverage_map=coverage_map,   # optional, see coverage/README.md
    module_map=module_map,       # optional, file_path → community label
)

report = analyzer.analyze(config=None)
# or, for repos large enough to benefit from parallel parsing:
report = await analyzer.analyze_async()

# Incremental — only the files in `changed_files` produce findings/metrics.
# Duplication still runs cross-file so unchanged clone partners are honoured.
report = analyzer.analyze(changed_files={"a.py", "b.py"})
```

### Returned shapes

- `report.findings` — `list[HealthFindingData]`. One row per marker hit.
- `report.metrics` — `list[HealthFileMetricData]`. One row per analyzed file.
- `report.kpis` — `{"hotspot_health", "average_health", "worst_performer_path",
  "worst_performer_score", "file_count"}`. Skipped on incremental runs.

## Persistence

Three SQLAlchemy tables back the layer (Alembic-managed, no JSON files):

| Table | Purpose | Writer |
|-------|---------|--------|
| `health_findings` | One row per marker hit. Lifecycle: `open` → `acknowledged` / `resolved` / `false_positive`. | `save_health_findings` / `upsert_health_findings` |
| `health_file_metrics` | Per-file aggregate + final score. | `save_health_metrics` / `upsert_health_metrics` |
| `health_snapshots` | KPI history (50-row rolling window). | `save_health_snapshot` |
| `coverage_files` | Per-file coverage (line + branch). | `save_coverage_files` |

`save_*` variants replace the whole repo (init path). `upsert_*` variants only
touch the rows for a given file-path set — used by `repowise update` so
unchanged files keep their findings across incremental runs.

## Trends

`trends.py` is pure logic over an in-memory snapshot list:

- **Declining Health** alert: current is ≥ 0.5 points below the snapshot
  N-5 entries back (constants `DECLINE_THRESHOLD`, `DECLINE_LOOKBACK`).
- **Predicted Decline** alert: the three most recent snapshots are each
  strictly below the one before.

Use `diff_snapshots(history)` for a `TrendSummary`, or `recent_kpis(history,
limit=10)` for the CLI / dashboard table.

Per-file trajectory (same snapshots, the `{path: score}` map):

- `file_score_series(history, path)` — oldest-first `FileTrendPoint`s,
  skipping snapshots missing the file; `[]` below two points (silent on thin
  history). Reused verbatim by the PR bot's in-comment sparkline.
- `file_trend(history, path)` — wraps the series with `current` / `previous`
  / `delta` and a `declining` flag (per-file mirror of the alerts above).

## Per-file signals

`signals.py` is the same kind of pure, state-free join: it consolidates the
per-file signals we *already* compute and persist (git history + graph
topology) into one captioned contract — no recompute, no new measurement.

- `file_signals(git_meta, degrees)` returns a `FileSignals` grouped as
  **Process** (`prior_defect_count`, `change_entropy_pct`, 90-day line churn,
  `age_days`), **People** (recent vs all-time owner + commit share), and
  **Topology** (`in_degree` / `out_degree`).
- Honesty rule: a field is `None` ("no signal") only when its *source row* is
  absent — never imputed. A git-tracked file with zero bug-fixes reports `0`
  (a real, reassuring signal); a file with no git history reports `None` for
  the whole process/people group. `change_entropy_pct` is normalized 0-1 → 0-100
  to match the hotspot API contract.

Mirrored as `FileSignals` in `@repowise-dev/types/health`; surfaced in the
dashboard drawer, the file-page Health tab, the `/health/files/breakdown` +
`files/{path}` endpoints, and the MCP `get_context(include=["health"])` block.

## Churn × complexity

`churn_complexity.py` is the same pure, state-free join in service of the
"hotspot anatomy" scatter:

- `churn_complexity_points(metrics, git_meta_by_path)` returns one
  `ChurnComplexityPoint` per recently-changed file — `commit_count_90d` (churn
  axis), `max_ccn` (complexity axis), `nloc` (dot size), `score` (dot color via
  band), `churn_percentile` (tooltip context) — sorted by the churn × complexity
  "danger product" so a capped caller keeps the worst offenders.
- Honesty rule: a file is omitted when it has no recent churn (nothing to plot
  on the x-axis); complexity is **never** a filter, so a high-churn, simple file
  is kept as a valid bottom-right signal.

Mirrored as `ChurnComplexityPoint` in `@repowise-dev/types/health`; served by
`GET /health/churn-complexity` and rendered by `ChurnComplexityQuadrant` on the
Hotspots & churn dashboard tab.

## Refactoring suggestions

`suggestions.suggestion_for(biomarker_type)` returns the canonical, static
text used by both the MCP `get_health(include=["refactoring"])` response and
the dashboard's `RefactoringCard`. Templates live in `suggestions.py` —
adding a new marker means adding a new `_TEMPLATES` entry.

## Module rollups

`HealthFileMetric.module` is populated from graph community labels by the
orchestrator (falls back to the top-level directory). The MCP tool
(`tool_health.py`) and the API endpoint (`routers/code_health.py`) both
expose NLOC-weighted module aggregates and accept `module:foo` targets.

## Sub-packages

- `complexity/` — tree-sitter AST walker. CCN, max nesting, cognitive,
  parameter count, bumps. Single AST pass per file. Writes
  `Symbol.complexity_estimate` as a side effect.
- `coverage/` — LCOV / Cobertura / Clover parsers + test-file heuristic.
- `duplication/` — Rabin–Karp over tree-sitter tokens. Co-change correlation
  via `git_meta_map[path]["co_change_partners_json"]`.
- `biomarkers/` — one detector per file. Implements the `Biomarker`
  Protocol from `biomarkers/base.py`. Twenty-six registered (see
  `biomarkers/registry.py` and `biomarkers/README.md` for the full list),
  plus three governance findings written by a separate additive pass.
- `grading.py` — the presentation "currency" layer over the score: the 3
  defect-backed bands (`band_for` — Alert `<4` / Warning `4–8` / Healthy `≥8`)
  and the NLOC-weighted `distribution`. Single source of truth for the cutoffs
  (mirrored in `@repowise-dev/types/health`). No letter grade — see
  `docs/architecture/code-health.md` §20.

Each sub-package has its own `README.md` covering inputs, outputs, and
extension points.

## Extension points

- **New marker.** Drop a file under `biomarkers/`, implement
  `Biomarker.detect(ctx) -> list[BiomarkerResult]`, register in
  `biomarkers/registry.py`, add a suggestion in `suggestions.py`, add the
  category mapping in `scoring._BIOMARKER_CATEGORY`.
- **New complexity language.** Add a `LanguageNodeMap` entry to
  `complexity/languages.py`. No new `.scm` files needed.
- **New coverage format.** Drop a parser under `coverage/` and register it
  with `coverage/detector.py`.
- **Per-file overrides.** Users write `.repowise/health-rules.json`. See
  `config.HealthConfig` and `tests/unit/health/test_health_config.py`.

## Performance

`HealthAnalyzer.analyze_async()` parallelises the per-file work via
`asyncio.gather` + `asyncio.to_thread`. Tree-sitter releases the GIL during
parsing, so this scales on single-process Python. The orchestrator picks the
parallel path automatically when `len(parsed_files) >= 500`.

## Where to look in the codebase

- CLI: `packages/cli/src/repowise/cli/commands/health_cmd.py`,
  `status_cmd.py`, `update_cmd.py`.
- MCP tools: `packages/server/src/repowise/server/mcp_server/tool_health.py`
  + enrichments in `tool_risk.py`, `tool_context.py`, `tool_overview.py`.
- API: `packages/server/src/repowise/server/routers/code_health.py`.
- UI primitives: `packages/ui/src/health/`. Web routes:
  `packages/web/src/app/repos/[id]/health/`.
- CLAUDE.md template stanza: `packages/core/src/repowise/core/generation/templates/claude_md.j2`.
