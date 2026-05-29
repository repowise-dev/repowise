# Code Health — Architecture & Internals

Companion to the user-facing [`docs/CODE_HEALTH.md`](../CODE_HEALTH.md). This
document is for contributors: where every piece lives, how data flows from
parsed source to the dashboard, and the extension points for adding
biomarkers, languages, coverage formats, or alerts.

> **TL;DR.** Health analysis is a deterministic, zero-LLM Python pipeline:
> tree-sitter walks every file once → biomarkers vote → scores aggregate per
> category → results land in four SQLAlchemy tables. The MCP server, CLI,
> and Next.js dashboard all read from those tables — no JSON cache, no
> intermediate files, no LLM in the loop.

---

## 1. Layer overview

Code Health is the **fifth intelligence layer** in Repowise, alongside Graph,
Git, Docs, and Decisions. It reads from Graph and Git but never modifies
them. Its only writes are to its own four tables.

```
┌─────────────┐  parsed_files     ┌──────────────────┐
│ Ingestion   │ ────────────────► │                  │
│ (AST + git) │  git_meta_map     │  HealthAnalyzer  │ ──► HealthReport
│             │ ────────────────► │   (engine.py)    │       │
│             │  community_labels │                  │       │
└─────────────┘                   └──────────────────┘       │
                                                             │  delete/upsert
                                                             ▼
                                            ┌─────────────────────────────┐
                                            │ SQLite via SQLAlchemy       │
                                            │  • health_findings          │
                                            │  • health_file_metrics      │
                                            │  • health_snapshots         │
                                            │  • coverage_files           │
                                            └──────────────┬──────────────┘
                                                           │
                          ┌────────────────────────────────┼────────────────────────────────┐
                          ▼                                ▼                                ▼
                     CLI (rich)                  MCP tools (FastMCP)                  Web dashboard
                  health, status,           get_health, get_risk,                   /repos/[id]/health{,
                  health --trend            get_context, get_overview              /coverage,/refactoring-targets}
```

Three architectural rules govern the whole layer:

1. **Zero LLM.** Every biomarker is AST, git, or coverage math.
2. **No JSON caches.** SQLite is the single source of truth; everything
   else reads from it.
3. **No new runtime dependencies.** Pure Python over tree-sitter (already
   in tree). No lizard, no jscpd, no Node.

---

## 2. Where things live

### Python — `packages/core/src/repowise/core/`

```
analysis/health/
├── README.md                       # developer overview (this layer)
├── __init__.py                     # public API: HealthAnalyzer, HealthReport
├── engine.py                       # orchestrator: walker → biomarkers → scorer
├── scoring.py                      # weighted aggregation, category caps, KPIs
├── trends.py                       # snapshot diff, Declining/Predicted alerts
├── suggestions.py                  # deterministic refactoring text per biomarker
├── config.py                       # HealthConfig + .repowise/health-rules.json
├── models.py                       # HealthFindingData, HealthFileMetricData, HealthReport
│
├── complexity/                     # tree-sitter AST walker
│   ├── README.md
│   ├── walker.py                   # CCN, nesting, cognitive, bumps, params, NLOC
│   └── languages.py                # per-language control-flow node-type maps
│
├── coverage/                       # coverage report ingestion
│   ├── README.md
│   ├── model.py                    # CoverageReport, FileCoverage
│   ├── detector.py                 # format auto-detect + test-file heuristic
│   ├── lcov.py                     # LCOV parser (stdlib only)
│   ├── cobertura.py                # Cobertura XML parser
│   └── clover.py                   # Clover XML parser
│
├── duplication/                    # native Rabin-Karp clone detection
│   ├── README.md
│   ├── tokenizer.py                # tree-sitter token stream (ID/LIT normalized)
│   ├── rabin_karp.py               # 64-bit rolling polynomial hash
│   └── detector.py                 # clone-pair build + co-change weighting
│
└── biomarkers/                     # one detector per file
    ├── README.md
    ├── base.py                     # Biomarker Protocol + FileContext + BiomarkerResult
    ├── registry.py                 # detector list + detect_all()
    ├── brain_method.py
    ├── nested_complexity.py
    ├── bumpy_road.py
    ├── complex_method.py
    ├── large_method.py
    ├── primitive_obsession.py
    ├── dry_violation.py
    ├── untested_hotspot.py
    ├── coverage_gap.py
    ├── developer_congestion.py
    ├── knowledge_loss.py
    ├── hidden_coupling.py
    ├── complex_conditional.py
    ├── function_hotspot.py
    ├── code_age_volatility.py
    ├── ownership_risk.py
    ├── churn_risk.py
    ├── change_entropy.py
    └── co_change_scatter.py
```

### Persistence

```
core/persistence/
├── models.py                       # HealthFinding, HealthFileMetric, HealthSnapshot, CoverageFile
└── crud.py                         # save_/upsert_/get_ health functions
core/alembic/versions/
└── 000X_health_tables.py           # migration that created the four tables
```

### Pipeline wiring

```
core/pipeline/
├── orchestrator.py                 # _run_health_analysis() — builds module_map, runs analyzer
└── persist.py                      # persist_pipeline_result() — writes findings/metrics/snapshot
```

### CLI

```
cli/src/repowise/cli/commands/
├── health_cmd.py                   # repowise health [--trend|--coverage|--refactoring-targets|--module]
├── status_cmd.py                   # `Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: ...)`
└── update_cmd.py                   # incremental path: HealthAnalyzer.analyze(changed_files=...)
```

### Server — MCP + REST

```
server/src/repowise/server/
├── mcp_server/
│   ├── tool_health.py              # @mcp.tool get_health(targets, include, repo, limit)
│   ├── tool_risk.py                # enriched: health_score, top_biomarkers, coverage_pct
│   ├── tool_context.py             # include=["health"]: score, top 2 biomarkers, suggestion
│   └── tool_overview.py            # code_health block with KPIs
└── routers/
    └── code_health.py              # /api/repos/{id}/health/{overview,files,coverage,
                                    # refactoring-targets,modules,findings}
```

### Web dashboard

```
packages/ui/src/health/             # shared React components (used by web + future hosted frontend)
├── kpi-cards.tsx
├── file-table.tsx
├── biomarker-list.tsx
├── coverage-bar.tsx
├── module-coverage-list.tsx
├── untested-hotspot-warning.tsx
├── refactoring-card.tsx
├── refactoring-target-list.tsx
├── health-badge.tsx
└── module-rollup-list.tsx

packages/web/src/app/repos/[id]/health/
├── page.tsx                        # KPIs + lowest-scoring files + per-module rollup
├── coverage/page.tsx               # /health/coverage view
└── refactoring-targets/page.tsx    # /health/refactoring-targets view

packages/web/src/components/health/
└── health-risks-panel.tsx          # sidecar panel on Hotspots/Ownership/Graph pages
```

### Tests

```
tests/unit/health/                  # 99+ tests
├── test_complexity_walker.py       # per-language CCN/nesting assertions
├── test_biomarkers.py
├── test_structural_biomarkers.py   # bumpy_road, large_method, primitive_obsession
├── test_coverage_biomarkers.py     # untested_hotspot, coverage_gap
├── test_organizational_biomarkers.py
├── test_dry_violation.py
├── test_duplication.py             # tokenizer, hash, detector
├── test_coverage_parsers.py        # LCOV/Cobertura/Clover
├── test_scoring.py                 # category caps, clamping
├── test_scoring_snapshot.py        # stability snapshot — locks caps + deductions
├── test_health_config.py           # .repowise/health-rules.json
├── test_trends.py                  # diff_snapshots, declining/predicted alerts
└── test_suggestions.py

tests/integration/
├── test_health_coverage_integration.py
└── test_health_perf_benchmark.py   # 30 s budget on 3,000-file synthetic repo (slow)
```

---

## 3. The pipeline (init path)

`repowise init` runs `run_pipeline()` in `core/pipeline/orchestrator.py`.
Health analysis is a phase in that orchestrator, called between
`_run_dead_code_analysis()` and `_run_decision_extraction()`:

```python
# orchestrator.py — simplified
async def _run_health_analysis(graph_builder, git_meta_map, parsed_files, *, repo_path, progress):
    # 1. Build a {file_path → community label} map from the graph.
    #    Used to populate HealthFileMetric.module so module rollups
    #    aren't NULL.
    cd = graph_builder.community_detection()
    ci = graph_builder.community_info()
    module_map = {nid: ci[cid].label for nid, cid in cd.items() if ci.get(cid)}

    # 2. Load per-file override rules from .repowise/health-rules.json
    #    (empty / no-op when the file doesn't exist).
    cfg = HealthConfig.load(repo_path)
    analyzer_config = cfg.to_analyzer_config([pf.file_info.path for pf in parsed_files])

    # 3. Construct the analyzer with all inputs it needs.
    analyzer = HealthAnalyzer(
        graph_builder.graph(),       # NetworkX DiGraph for dependents
        git_meta_map=git_meta_map,   # hotspot bit, owners, co-change, bus factor
        parsed_files=parsed_files,   # ParsedFile objects from the AST phase
        module_map=module_map,       # community labels
    )

    # 4. Pick sync vs parallel based on repo size. Tree-sitter releases the
    #    GIL during parsing, so asyncio.gather + asyncio.to_thread gives
    #    real wall-clock speedup on large repos.
    if len(parsed_files) >= 500:
        report = await analyzer.analyze_async(analyzer_config)
    else:
        report = await asyncio.to_thread(analyzer.analyze, analyzer_config)

    return report                    # carried on PipelineResult.health_report
```

Then `core/pipeline/persist.py` writes everything in one session:

```python
# persist.py — simplified
if result.health_report:
    hr = result.health_report
    await save_health_metrics(session, repo_id, hr.metrics)
    if hr.findings:
        await save_health_findings(session, repo_id, hr.findings)
    # Snapshot for trend tracking — rolling 50-row window per repo.
    await save_health_snapshot(
        session,
        repo_id,
        hotspot_health=hr.kpis["hotspot_health"],
        average_health=hr.kpis["average_health"],
        worst_performer_path=hr.kpis["worst_performer_path"],
        worst_performer_score=hr.kpis["worst_performer_score"],
        per_file_scores={m.file_path: round(m.score, 2) for m in hr.metrics},
    )
```

---

## 4. Inside `HealthAnalyzer.analyze()`

Single pass over the parsed file list. For each file:

```python
# engine.py — pseudocode
1. _walk(pf):
   source = read_bytes(pf.file_info.abs_path)
   walk_file_complexity(language, source) → list[FunctionComplexity]
   # Each FunctionComplexity carries: name, line range, nloc, ccn,
   # max_nesting, cognitive, bumps, param_count.

2. _populate_symbol_complexity(pf, fc_list):
   # Side effect: write max(ccn) into Symbol.complexity_estimate so
   # the ContextAssembler symbol ranker benefits even when callers
   # don't query the health tables directly.

3. _evaluate_file(pf, fc_list, ...):
   # Build a FileContext with:
   #   - nloc, has_test_file, module
   #   - function_metrics: dict[symbol_name → FunctionComplexity]
   #   - git_meta: per-file dict (hotspot, owners, bus factor, ...)
   #   - dependents_count: in_degree on the graph
   #   - line_coverage_pct, branch_coverage_pct, covered_lines (when ingested)
   #   - clones, duplication_pct (from cross-file detect_clones())
   results = detect_all(ctx, disabled=file_disabled)
   score, deductions = score_file(results)
   findings = attach_impacts(results, deductions)
   metric = HealthFileMetricData(...)
```

After the loop:

```python
hotspot_paths = {p for p, meta in git_meta_map.items() if meta.get("is_hotspot")}
kpis = compute_kpis(metrics, hotspot_paths)
return HealthReport(findings=..., metrics=..., kpis=kpis)
```

Duplication runs **once up-front** (cross-file by nature). Each `FileContext`
gets a slice of the global clone report. The `dry_violation` biomarker
reads `ctx.clones` to rank pairs by co-change frequency from
`git_meta_map[path]["co_change_partners_json"]` — active clones rank
higher than dormant ones.

---

## 5. The 19 biomarkers and their categories

Each biomarker is a stateless class implementing the `Biomarker` Protocol
from `biomarkers/base.py`:

```python
class Biomarker(Protocol):
    name: str       # "brain_method", "nested_complexity", ...
    category: str   # see scoring.CATEGORY_CAPS
    def detect(self, ctx: FileContext) -> list[BiomarkerResult]: ...
```

| Category               | Cap  | Biomarkers |
|------------------------|------|------------|
| Organizational         | −3.5 | developer_congestion, knowledge_loss, hidden_coupling, function_hotspot, code_age_volatility, ownership_risk, churn_risk, change_entropy, co_change_scatter |
| Structural complexity  | −2.5 | brain_method, nested_complexity, bumpy_road, complex_conditional |
| Test coverage          | −2.0 | untested_hotspot, coverage_gap |
| Size & complexity      | −1.5 | complex_method, large_method, primitive_obsession |
| Duplication            | −1.0 | dry_violation |

`ownership_risk` (long-run minor-contributor dispersion, Bird et al.) and
`churn_risk` (size-normalized relative churn, Nagappan-Ball) are git-only
process signals computed from `top_authors_json` / `lines_added_90d` /
`churn_percentile` — fields the git indexer already produces. `change_entropy`
(Hassan's History Complexity Metric) and `co_change_scatter` (breadth of
co-change coupling, D'Ambros) are likewise git-only and read the
`change_entropy` / `change_entropy_pct` fields (see §5.1) and
`co_change_partners_json`. `knowledge_loss` is activity-gated so
abandoned-but-stable files (the survivor effect) no longer fire.

### 5.1 Change-entropy git-layer fields

`change_entropy` is computed during the **single** FULL-tier co-change walk
(`ingestion/git_indexer/co_change.py::compute_co_changes_and_entropy`) — no
extra `git log` subprocess. For each commit touching a set of tracked files
`F` (with `2 ≤ |F| ≤ 30`; wider commits are dropped as noise, Hassan's filter),
the commit's entropy is `log2(|F|)`, distributed uniformly (`1/|F|` per file)
and decayed with the same τ=180d half-life as co-change. The decay-weighted sum
per file is `git_meta["change_entropy"]`. `enrich.compute_percentiles` then
derives `change_entropy_pct` by ranking **only files with positive entropy**
(zero-entropy files — the ESSENTIAL tier, or files only ever changed alone —
keep pct 0.0 so the biomarker stays silent). Both fields are persisted on
`git_metadata` (migration `0025`) and the additive-reconcile path back-fills
them on legacy DBs.

`biomarkers/registry.py` is an **explicit list**, not auto-discovery —
keeps the registration order deterministic and lets tests inject extras
via `registered_biomarkers(extra=...)`.

---

## 6. Scoring (`scoring.py`)

Every file starts at **10.0**. Each finding contributes a per-severity
deduction (`low=0.3, medium=0.7, high=1.2, critical=2.0`). Deductions are
**capped per category** — so even ten critical structural findings can
drive structural complexity down by at most 3.5 points, not 20.

```python
# scoring.py — score_file()
1. Group findings by category.
2. Sum raw deductions per category.
3. If sum ≤ cap, accept; else scale every per-finding deduction
   proportionally so the total equals the cap. This keeps the UI
   "this finding cost you X points" honest after capping.
4. Clamp the final score to [1.0, 10.0].
```

The per-finding scaled deduction lands on `HealthFinding.health_impact`
via `attach_impacts()` — that's what the dashboard's "−2.0" badge shows.

Snapshot tests in `tests/unit/health/test_scoring_snapshot.py` lock the
category caps, severity deductions, biomarker→category mapping, and two
known-fixture scores. A retune intentionally requires updating the
snapshot in the same PR.

---

## 7. KPIs

Three repo-level numbers, computed in `compute_kpis()`:

- **Hotspot Health** — NLOC-weighted average over files where
  `git_meta_map[path]["is_hotspot"]` is true.
- **Average Health** — NLOC-weighted average over all files.
- **Worst Performer** — lowest-scoring file + its score.

These flow into `HealthSnapshot` rows (rolling 50 per repo) and feed the
CLI status one-liner, the `get_overview()` MCP block, and the dashboard
KPI cards.

---

## 8. Trends (`trends.py`)

State-free — callers pass an oldest-first list of snapshot rows. Two
alerts:

- **Declining Health** — current is ≥ `DECLINE_THRESHOLD` (default 0.5)
  below the snapshot `DECLINE_LOOKBACK` (5) positions back. Fires on the
  6th+ snapshot.
- **Predicted Decline** — the three most recent snapshots are each
  strictly below the one before. Magnitude is not required; direction is
  the signal.

`recent_kpis(history, limit=10)` returns a newest-first serialised view
for the CLI table and MCP `get_health(include=["trend"])` response.

---

## 9. Incremental analysis — the `repowise update` path

Full re-analysis would be wasteful on commit-sized diffs. `HealthAnalyzer`
accepts `changed_files`:

```python
# engine.py
def analyze(self, config=None, *, changed_files=None):
    # 1. Duplication still runs full-repo (a changed file's clone
    #    partner may be unchanged).
    # 2. The per-file loop skips files not in changed_files.
    # 3. KPIs are NOT recomputed on incremental runs — they'd be biased
    #    by the subset. The dashboard recomputes from the merged DB rows.
```

`update_cmd.py` builds the changed-files set from
`change_detector.get_changed_files()`, runs the analyzer, and calls a
helper that uses the **upsert** variants (`upsert_health_findings`,
`upsert_health_metrics`) so unchanged files keep their existing rows:

```python
async def _persist_partial_health(session, repo_id, report):
    changed_paths = sorted({m.file_path for m in report.metrics})
    await upsert_health_metrics(session, repo_id, report.metrics)
    await upsert_health_findings(session, repo_id, report.findings, file_paths=changed_paths)
```

The full-init writers (`save_health_findings`, `save_health_metrics`)
still use delete-then-insert — simpler, and the cost is amortised across
the whole `repowise init`.

---

## 10. Persistence schema

Four tables, all in the repo's `.repowise/wiki.db`. Foreign-keyed to
`repositories.id` with `ON DELETE CASCADE`.

### `health_findings`

One row per biomarker hit. Lifecycle: `open → acknowledged | resolved |
false_positive` (matches Dead Code). Bulk-deleted-and-rewritten on full
init; selectively upserted on `repowise update`.

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `repository_id` | FK |
| `file_path` | indexed |
| `biomarker_type` | `brain_method`, `nested_complexity`, ... |
| `severity` | `low` / `medium` / `high` / `critical` |
| `function_name` | nullable for file-level findings |
| `line_start`, `line_end` | nullable |
| `details_json` | per-biomarker evidence (CCN values, clone span, etc.) |
| `health_impact` | per-finding scaled deduction |
| `reason` | one-line summary string |
| `status` | lifecycle |
| `created_at`, `updated_at` | datetime |

### `health_file_metrics`

One row per file (unique on `(repository_id, file_path)`). Read directly
by the dashboard's file table.

| Column | Notes |
|---|---|
| `score` | 1.0–10.0 final |
| `max_ccn`, `max_nesting`, `nloc` | aggregate function metrics |
| `duplication_pct` | percent of NLOC covered by clones; nullable |
| `has_test_file` | paired or heuristic |
| `line_coverage_pct`, `branch_coverage_pct` | nullable |
| `module` | community label from graph; falls back to top-level dir |
| `updated_at` | datetime |

### `health_snapshots`

KPI + per-file score history. Rolling delete on insert keeps the latest
50 per repo (`HEALTH_SNAPSHOT_RETENTION` in `crud.py`).

### `coverage_files`

Per-file coverage, overwritten on every `--coverage` run. Carries the
explicit `covered_lines_json` array so the `coverage_gap` biomarker can
flag the exact uncovered surface, not just the percent.

---

## 11. CLI surface

`packages/cli/src/repowise/cli/commands/health_cmd.py`. Mirrors the
dead-code command's Click structure.

```bash
repowise health                            # KPIs + lowest-scoring files + findings
repowise health --file path/to/x.py        # deep-dive one file
repowise health --module packages/server   # restrict to a directory prefix
repowise health --refactoring-targets      # ranked by impact / effort
repowise health --trend                    # last 10 snapshots + active alerts
repowise health --coverage coverage.lcov   # ingest coverage; can repeat
repowise health --coverage-format cobertura
repowise health --format json | jq ...
repowise health --safe-only                # confidence ≥ 0.8 only (placeholder)
```

`repowise status` queries the same tables for a one-line summary:

```
Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: packages/server/.../app.py)
```

`repowise update` is unchanged from the user's perspective — health is
silently re-scored for changed files only.

---

## 12. MCP surface

### `get_health(targets?, include?, repo?, limit?)`

Defined in `tool_health.py`. Modes:

- **Dashboard mode** (`targets=None`) — returns repo-level KPIs +
  `worst_files` (top N lowest-scoring) + `top_findings` + a per-module
  `modules` rollup.
- **Targeted mode** (`targets=[...]`) — returns full `metrics` +
  `findings` for the listed paths. Targets prefixed `module:foo` expand
  to the file set in that module.

`include` flags layer richer data:

| Flag | Adds |
|---|---|
| `"biomarkers"` | full findings list (already present in target mode) |
| `"coverage"` | per-file coverage rows + summary |
| `"refactoring"` | deterministic `suggestion` text on every finding |
| `"trend"` | snapshot diff + alerts + last 10 KPI rows |

### Enrichments on existing tools

- `get_risk(targets)` — each per-target row carries `health_score`,
  `top_biomarkers`, `coverage_pct`, `branch_coverage_pct`.
- `get_context(targets, include=["health"])` — per-file `score`,
  `max_ccn`, `max_nesting`, `nloc`, `module`, `duplication_pct`, top
  2 biomarkers (each with a `suggestion` string), and coverage block.
- `get_overview()` — adds a `code_health` block: avg, hotspot, worst
  performer, open finding count.

Every response carries the standard `_meta` envelope via `build_meta()`.

---

## 13. REST surface

`packages/server/src/repowise/server/routers/code_health.py`. All under
`/api/repos/{repo_id}/health/`:

| Route | Returns |
|---|---|
| `GET /overview` | summary + lowest-scoring files + top findings + module rollup |
| `GET /files` | per-file metrics |
| `GET /findings` | findings list (filterable by biomarker_type, severity, file_path) |
| `GET /coverage` | coverage summary + per-file rows |
| `POST /coverage` | ingest a coverage report (used by some CI integrations) |
| `GET /refactoring-targets` | ranked by `total_impact / effort_bucket` |
| `GET /modules` | NLOC-weighted module rollup table |

Auth is the standard `verify_api_key` dependency from
`server/deps.py`.

---

## 14. Web dashboard

Three routes under `/repos/[id]/health/`:

| Route | What it shows |
|---|---|
| `/health` | KPI cards, lowest-scoring file table, top findings, **per-module rollup** (added in Phase 4) |
| `/health/coverage` | Coverage summary, untested-hotspot warnings, module-level bars, per-file drill-down |
| `/health/refactoring-targets` | Cards sorted by impact-per-effort, each with severity, biomarker, score, NLOC, effort bucket, **deterministic suggestion** |

Plus a sidecar `HealthRisksPanel` on the Hotspots, Ownership, and Graph
pages — surfaces the lowest-scoring files inline without touching the
shared table/graph components.

All visual primitives live in `packages/ui/src/health/` so the hosted
`frontend/` repo (separate git checkout) can reuse them — port is mostly
data fetching + auth.

---

## 15. CLAUDE.md integration

The auto-generated `CLAUDE.md` includes a `## Code health` section when
the health tables are populated. The block is intentionally short —
filter rules in `core/generation/editor_files/data.py`:

- Score ≤ 5.0 **and** file is a hotspot
- Any Brain Method in a file with > 10 dependents
- Any Untested Hotspot
- DRY violations > 70 % similarity
- Declining trend (> 1.0 drop in last 5 snapshots)

Everything else is filtered out so the CLAUDE.md doesn't drown a fresh
agent in noise. The Jinja stanza lives in
`core/generation/templates/claude_md.j2`.

---

## 16. Configuration — `.repowise/health-rules.json`

User-authored (the **only** JSON file in the layer). Loaded by
`HealthConfig.load(repo_path)`:

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

`to_analyzer_config(file_paths)` resolves globs to per-file disabled
sets, which the engine honors in `_evaluate_file()`.

---

## 17. Performance

Plan §4 P4.6 targets **< 30 s on a 3,000-file synthetic repo**. The
parallel path in `HealthAnalyzer.analyze_async()` parallelises tree-sitter
parsing across worker threads (`asyncio.gather` + `asyncio.to_thread`).
tree-sitter releases the GIL on parse, so this scales on single-process
CPython.

The orchestrator chooses the parallel path automatically when
`len(parsed_files) >= 500`. The benchmark lives at
`tests/integration/test_health_perf_benchmark.py` and is marked `slow`
(opt-in via `pytest -m slow` or `make health-bench`).

Other perf notes:

- **Duplication is O(total_tokens).** Bucket walk is near-linear on
  repos with low duplication.
- **Walker re-parses files** because `ParsedFile` doesn't retain a
  tree-sitter `Tree` across the ingestion boundary. Acceptable (~1 ms
  per file); switching to a shared parse cache is a Phase 5 stretch.
- **No N² loops in scoring.** Category aggregation is O(findings).

---

## 18. Testing

| Suite | What it locks |
|---|---|
| `tests/unit/health/test_complexity_walker.py` | Per-language CCN, nesting, cognitive assertions on handcrafted fixtures |
| `tests/unit/health/test_<biomarker>.py` | Each biomarker — positive in two languages + one negative |
| `tests/unit/health/test_duplication.py` | Tokenizer normalization, rolling-hash determinism, co-change weighting |
| `tests/unit/health/test_coverage_parsers.py` | LCOV / Cobertura / Clover happy paths + edge cases |
| `tests/unit/health/test_scoring.py` | Deduction caps, clamping, KPI math |
| `tests/unit/health/test_scoring_snapshot.py` | **Stability guard** — caps, severity table, biomarker→category mapping, two known fixture scores |
| `tests/unit/health/test_trends.py` | Declining + predicted alerts, ordering |
| `tests/unit/health/test_suggestions.py` | Suggestion strings keyed correctly |
| `tests/unit/health/test_health_config.py` | `.repowise/health-rules.json` parsing + glob matching |
| `tests/integration/test_health_coverage_integration.py` | End-to-end LCOV → analyzer → coverage_gap fires |
| `tests/integration/test_health_perf_benchmark.py` | 30 s budget on 3,000 synthetic files (`-m slow`) |

99 unit tests + 2 integration tests at time of writing. Run with
`make health-check`.

---

## 19. Extension points

### Add a biomarker

1. New file under `biomarkers/` implementing the `Biomarker` Protocol.
2. Append to `_DETECTOR_FACTORIES` in `biomarkers/registry.py`.
3. Add the biomarker→category mapping in
   `scoring._BIOMARKER_CATEGORY`.
4. Add a suggestion template in `suggestions._TEMPLATES`.
5. Add at least three test cases (two positive in different languages,
   one negative).
6. Update `biomarkers/README.md`'s "Registered v1 detectors" list.

### Add a language to the complexity walker

Add one `LanguageNodeMap` entry to `complexity/languages.py` mapping the
language's tree-sitter control-flow node-type names to abstract `BRANCH`
/ `LOOP` / `TRY` / `BOOLEAN_OP` categories. Add a fixture under
`tests/fixtures/lang_samples/<lang>/`. **No `.scm` files needed** — those
are owned by the ingestion parser.

### Add a coverage format

Drop a parser under `coverage/` returning a `CoverageReport`. Route to it
from `coverage/detector.parse`. Stdlib-only (no extra XML libraries).

### Add a per-file override

Users — not contributors — author `.repowise/health-rules.json`. To add
a new override key (beyond `disabled_biomarkers`), extend
`HealthConfig` and thread it through `to_analyzer_config()` →
`engine._evaluate_file()`.

---

## 20. Where the layer deliberately stops

A short list of things the v1 layer **does not** do, by design. Future
phases may revisit; the constraints kept v1 shippable.

- **No LLM-generated suggestions.** `suggestions.py` is static
  templates. An optional LLM mode is Phase 5, gated behind an explicit
  flag.
- **No symbol-level scoring.** Score lives at the file granularity to
  match how engineers think about refactor units. Symbol-level CCN
  still feeds the file score via `function_metrics`.
- **No `complexity_estimate` propagation backfill.** The walker writes
  the field as a side effect during the current run; old indexes don't
  get touched until a re-index.
- **No PR-mode delta in v1.** `get_risk(changed_files=...)` returns the
  current health score, not before/after. Phase 5.
- **No predictive ML.** `Predicted Decline` is a 3-snapshot direction
  check, not a model. Phase 5.

---

## 21. Quick lookup — where do I edit X?

| I want to... | Edit... |
|---|---|
| Tweak a category cap | `scoring.CATEGORY_CAPS` (snapshot test will fail — update it) |
| Tweak a severity deduction | `scoring._SEVERITY_DEDUCTION` (ditto) |
| Add a new biomarker | `biomarkers/*.py`, `registry.py`, `scoring.py`, `suggestions.py` |
| Change the suggestion text for a biomarker | `suggestions._TEMPLATES` |
| Adjust the trend-alert threshold | `trends.DECLINE_THRESHOLD` / `DECLINE_LOOKBACK` |
| Change snapshot retention | `crud.HEALTH_SNAPSHOT_RETENTION` |
| Add a new MCP `include` flag | `tool_health.py` — append handling near the existing `"coverage"` / `"refactoring"` branches |
| Add a new REST route | `routers/code_health.py` — auth is wired at the router level |
| Add a new dashboard view | new file under `packages/web/src/app/repos/[id]/health/`, primitives under `packages/ui/src/health/` |
| Add a CLI flag | `packages/cli/src/repowise/cli/commands/health_cmd.py` |
| Wire the analyzer into a new entry point | call `HealthAnalyzer.analyze()` directly; persist via the upsert variants if your caller is incremental |

---

## See also

- [`docs/CODE_HEALTH.md`](../CODE_HEALTH.md) — user-facing guide.
- [`packages/core/src/repowise/core/analysis/health/README.md`](../../packages/core/src/repowise/core/analysis/health/README.md) — developer overview at the layer root.
- Sub-package READMEs under `complexity/`, `coverage/`, `duplication/`, `biomarkers/`.
- [`docs/architecture/graph-algorithms.md`](./graph-algorithms.md) — the graph layer health depends on.
