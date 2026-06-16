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
├── grading.py                      # 3 defect-backed bands + NLOC-weighted distribution
├── defect_accuracy.py              # "does the score find the bugs?" self-validation
├── trends.py                       # snapshot diff, Declining/Predicted alerts, per-file score series
├── signals.py                      # per-file process/people/topology join (surfacing-only)
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
│   ├── clover.py                   # Clover XML parser
│   └── repowise_json.py            # normalized repowise-coverage-v1 JSON parser
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
    ├── low_cohesion.py
    ├── god_class.py
    ├── nested_complexity.py
    ├── bumpy_road.py
    ├── complex_method.py
    ├── large_method.py
    ├── primitive_obsession.py
    ├── dry_violation.py
    ├── untested_hotspot.py
    ├── coverage_gap.py
    ├── coverage_gradient.py
    ├── developer_congestion.py
    ├── knowledge_loss.py
    ├── hidden_coupling.py
    ├── complex_conditional.py
    ├── function_hotspot.py
    ├── code_age_volatility.py
    ├── ownership_risk.py
    ├── churn_risk.py
    ├── change_entropy.py
    ├── co_change_scatter.py
    ├── prior_defect.py
    ├── large_assertion_block.py
    ├── duplicated_assertion_block.py
    └── error_handling.py
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
├── health-badge.tsx               # score pill, colored by the 3 health bands
├── health-distribution-bar.tsx    # NLOC-weighted Alert/Warning/Healthy split
├── trend-chart.tsx                # repo KPI history (3 series)
├── file-trend-chart.tsx           # single file's score-over-time + delta + declining flag
├── sparkline.tsx                  # compact inline series (drawer trend)
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
├── test_coverage_parsers.py        # LCOV/Cobertura/Clover/JSON
├── test_scoring.py                 # category caps, clamping
├── test_scoring_snapshot.py        # stability snapshot — locks caps + deductions
├── test_health_config.py           # .repowise/health-rules.json
├── test_trends.py                  # diff_snapshots, declining/predicted alerts
├── test_signals.py                 # file_signals join + no-signal/normalization
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
   walk_file(language, source) → FileComplexity(functions, classes)
   # Each FunctionComplexity carries: name, line range, nloc, ccn,
   # max_nesting, cognitive, bumps, param_count.
   # Each ClassComplexity carries: name, line range, method_count,
   # total_nloc, methods, lcom4, max_method_ccn, field_count.

2. _populate_symbol_complexity(pf, fcx.functions):
   # Side effect: write max(ccn) into Symbol.complexity_estimate so
   # the ContextAssembler symbol ranker benefits even when callers
   # don't query the health tables directly.

3. _evaluate_file(pf, fcx, ...):
   # Build a FileContext with:
   #   - nloc, has_test_file, module
   #   - function_metrics: dict[symbol_name → FunctionComplexity]
   #   - class_metrics: list[ClassComplexity]  (LCOM4 / god-class)
   #   - git_meta: per-file dict (hotspot, owners, bus factor, ...)
   #   - dependents_count: in_degree on the graph
   #   - repo_dependents_p80: repo-wide p80 of in-degree (brain_method floor)
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

## 5. The 26 biomarkers and their categories

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
| Organizational         | −3.5 | developer_congestion, knowledge_loss, hidden_coupling, function_hotspot, code_age_volatility, ownership_risk, churn_risk, change_entropy, co_change_scatter, prior_defect |
| Structural complexity  | −2.5 | brain_method, low_cohesion, god_class, nested_complexity, bumpy_road, complex_conditional |
| Test coverage          | −2.0 | untested_hotspot, coverage_gap |
| Test coverage (cont.)  | −2.0 | coverage_gradient |
| Size & complexity      | −1.5 | complex_method, large_method, primitive_obsession |
| Duplication            | −1.0 | dry_violation |
| Test quality           | −0.5 | large_assertion_block, duplicated_assertion_block |
| Error handling         | −0.5 | error_handling |

`large_assertion_block` and `duplicated_assertion_block` are the two
**test-quality** smells (see §5.3). They fire only on test files and sit in
a deliberately small category so a noisy test can't dominate its own score.
`large_method` is now gated on a minimal CCN floor (≥ 2) so a long-but-flat
body (a big data literal) reads as layout, not a complexity smell — a small
step toward decoupling the score from raw file size.

`ownership_risk` (long-run minor-contributor dispersion, Bird et al.) and
`churn_risk` (size-normalized relative churn, Nagappan-Ball) are git-only
process signals computed from `top_authors_json` / `lines_added_90d` /
`churn_percentile` — fields the git indexer already produces. `change_entropy`
(Hassan's History Complexity Metric) and `co_change_scatter` (breadth of
co-change coupling, D'Ambros) are likewise git-only and read the
`change_entropy` / `change_entropy_pct` fields (see §5.1) and
`co_change_partners_json`. `knowledge_loss` is activity-gated so
abandoned-but-stable files (the survivor effect) no longer fire.

`prior_defect` (recent bug-fix history, Ostrand-Weyuker / Kim's "bug cache")
is the other git-only process signal: the count of bug-fix commits touching a
file in the trailing ~6-month window, read from `prior_defect_count`. The
git indexer classifies a commit as a fix with the **same keyword rule the
defect benchmark labels fixes with** (`_constants.is_fix_commit`), counts only
non-merge commits inside the window, and anchors the window to the index's
`as_of` reference (`REPOWISE_GIT_WINDOW_ANCHOR`) — so scoring a historical T0
checkout measures the fixes *before* T0, never leaking the post-T0 fixes that
form the benchmark's labels. It carries a **neutral (1.0) weight by design**:
on the calibration corpus prior-defect history is largely redundant with the
existing process signals (correlation ≈ +0.59 with `change_entropy`, +0.38 with
churn; calibrated coefficient ≈ +0.02, effort-aware Popt gain within bootstrap
noise), so it is not boosted as a predictor. It ships for its **explanatory**
value — "this file was bug-fixed N times recently" is immediately actionable,
and it uniquely flags a few files the other signals miss — not for a measured
accuracy lift.

`coverage_gradient` makes the test-coverage signal **continuous**. The two
binary coverage gates (`untested_hotspot`, `coverage_gap`) only fire below hard
thresholds (≈40–60% line coverage), so on a well-tested codebase — where most
files sit at 85–99% — the score is effectively blind to coverage even though the
uncovered fraction still carries defect signal. `coverage_gradient` deducts
health in direct proportion to that fraction: `4.0 × (1 − line_coverage_pct/100)`
health points, clamped by its category cap (binding at ≥50% uncovered). It uses
the `deduction` override on `BiomarkerResult` — a continuous magnitude that
replaces the discrete severity → deduction table for that finding — so it stays
**linear and per-finding attributable** (the `health_impact` contract holds). It
is **silent when no coverage report was ingested** (`line_coverage_pct is None`):
absent coverage is never imputed as uncovered. It lives in its own capped
category (`test_coverage_gradient`, −2.0) so the additive continuous signal
neither squeezes nor is squeezed by the binary gates, and it skips test files.
Calibrated offline against the defect corpus, it recovers **+0.043 corpus AUC
[95% CI +0.023, +0.061]** on the covered subset (≈65% of the continuous-feature
ceiling), Popt-neutral, and is exactly zero on repos without ingested coverage —
a purely additive improvement.

`low_cohesion` (LCOM4) and `god_class` are the two **class-level**
structural smells. They read `ctx.class_metrics`, the per-class aggregates
the walker now emits alongside per-function metrics (see §5.2).
`brain_method`'s centrality gate is **language-agnostic**: instead of a
fixed `dependents ≥ 8`, it fires when a file is in the repo's top quintile
of connected files (`repo_dependents_p80`, computed once per analyze) or
clears the absolute hub bar of 8 — so it no longer goes silent on
sparse-graph languages (TS barrels, Rust) whose in-degrees are lower than
Python's.

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

### 5.2 Class-level walker metrics (LCOM4)

The complexity walker emits a `ClassComplexity` per class-like node for
languages that opt in (`LanguageNodeMap.class_kinds` non-empty: Python,
TS/JS, Java, Kotlin, Rust `impl`, C++, C#; Go has no grouping node). LCOM4 is the number of
connected components in the graph whose nodes are the class's methods and
whose edges link methods that share an instance field or call one another.
Member references are detected per-language via `self`/`this`/`$this`
member-access nodes. **Safety valve:** a class with no detected member
references (a static utility, or an unmapped language) reports `lcom4 = 1`
("no signal") rather than `len(methods)`, so adding a language can only
turn signal on — never produce a false-positive flood. See
`complexity/README.md` for the full heuristic and its limits.

`error_handling` is the **advisory maintainability** biomarker: swallowed
catches (an empty/comment-only `catch` / `except: pass` body), Python
catch-all `except:` / `except Exception:`, Rust `.unwrap()` / `.expect()` /
panic-family macros, and Go's empty `if err != nil {}` or blank-identifier
discard of a call's error. The walker collects each occurrence (with its
line) in a whole-tree pass — module-level code included — reusing the
`LanguageNodeMap` catch kinds for the seven catch-shaped languages and
dedicated recognizers for Rust/Go; an unsupported language or parse failure
yields no hits ("no signal", never a guess). The biomarker emits one LOW
finding per occurrence (0.15 after its floored 0.5 weight) in its own
`error_handling` category capped at −0.5, mirroring `test_quality`'s
advisory framing. It is deliberately **excluded from the defect-weight
calibration**: on the 21-repo / 9-language T0 benchmark it is AUC-neutral
(OOF delta ≈ 0, CI crosses zero) but size-orthogonal and the least redundant
signal tested, and it ships because users expect `except: pass` flagged —
bounded so it can never move a file by more than half a point.

### 5.3 Assertion-block walker metrics (test-quality)

The same single walker pass records `assertion_blocks` on each
`FunctionComplexity`: runs of ≥ 2 consecutive assertion statements, each
`(start_line, end_line, count)`. A statement counts as an assertion when it
is a bare `assert` (`LanguageNodeMap.assert_kinds`) or its expression is a
call (`assert_call_kinds`) whose callee name starts with `assert` or
`expect` — covering `assertEqual` / `assert_eq!` / `expect(...).toBe(...)`
across xUnit and BDD styles. Opt-in per language (all nine full-tier
languages — Python, TS/JS, Java, Kotlin, Go, Rust, C++, C# — are mapped);
a language that maps neither field simply emits no blocks.
`large_assertion_block` flags a single run ≥ 15; `duplicated_assertion_block`
intersects the clone report with assertion spans. Both gate on
`coverage.is_test_file(path)` so production code is never touched.

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

### Per-file trajectory

Snapshots also store a compact `{path: score}` map (`per_file_scores_json`),
so the same window yields a single file's score-over-time series:

- `file_score_series(history, path)` — oldest-first `FileTrendPoint`s,
  skipping snapshots that don't carry the file. Returns `[]` below two
  points (silent on thin history). This is the exact function the PR bot
  reuses for its in-comment sparkline.
- `file_trend(history, path)` — wraps the series with `current` / `previous`
  / `delta` and a `declining` flag (the per-file mirror of the alerts above:
  ≥ `DECLINE_THRESHOLD` below the lookback point, or
  `PREDICTED_DECLINE_CONSECUTIVE` consecutive drops). `snapshot_count` is the
  full window size so a young repo is distinguishable from a file missing in
  older snapshots.

Both are state-free; the server serialises `FileTrend` via
`_file_trend_to_dict` and embeds it in the file-detail health block, the
health-breakdown response, and the standalone trend route (§13).

### Per-file signals (`signals.py`)

The same state-free pattern, applied to the git-layer + topology fields we
already persist but only buried inside biomarker detail cards (or omitted
entirely). `file_signals(git_meta, degrees)` joins one `GitMetadata` row with
the file's graph degree into a `FileSignals` grouped as **Process**
(`prior_defect_count`, `change_entropy_pct` normalized 0-100, 90-day line
churn, `age_days`), **People** (recent vs all-time owner + commit share), and
**Topology** (`in_degree` / `out_degree`). No recompute — pure surfacing.

The honesty rule mirrors the trend: a field is `None` only when its *source
row* is absent (no git history → process/people silent; not a graph node →
topology silent), never imputed; a genuine `prior_defect_count` of `0` is kept
as a real signal. The server serialises it via `_file_signals_to_dict` and
embeds it in the file-detail health block and the breakdown response (§13); MCP
attaches a null-dropped copy to the `get_context` health block (§12). Mirrored
as `FileSignals` in `@repowise-dev/types/health`; rendered by the shared
`file-signals-panel.tsx` in both the drawer and the file-page Health tab.

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

- **Dashboard mode** (`targets=None`) — returns repo-level KPIs (with the
  repo `band`) + the NLOC-weighted `distribution` across the 3 bands +
  `worst_files` (top N lowest-scoring) + `top_findings` + a per-module
  `modules` rollup.
- **Targeted mode** (`targets=[...]`) — returns full `metrics` +
  `findings` for the listed paths, plus a per-file `trends` block (compact
  score series + `current` + `delta` + `declining`) for any target with at
  least two snapshots of history. Targets prefixed `module:foo` expand to
  the file set in that module.

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
  2 biomarkers (each with a `suggestion` string), a coverage block, and a
  null-dropped `signals` block (process/people/topology — see §8).
- `get_overview()` — adds a `code_health` block: avg, repo `band`, hotspot,
  worst performer, open finding count, and the NLOC-weighted `distribution`.

Every response carries the standard `_meta` envelope via `build_meta()`.

---

## 13. REST surface

`packages/server/src/repowise/server/routers/code_health.py`. All under
`/api/repos/{repo_id}/health/`:

| Route | Returns |
|---|---|
| `GET /overview` | summary (with repo `band`) + `distribution` + lowest-scoring files + top findings + module rollup |
| `GET /badge.svg` | self-rendered flat SVG health badge (color + `N.N/10`, no letter) |
| `GET /badge.json` | Shields.io endpoint-badge payload (`schemaVersion`/`label`/`message`/`color`/`band`) |
| `GET /files` | per-file metrics |
| `GET /files/breakdown` | one file's metric + score breakdown + findings + suggestions + per-file `trend` + `signals` |
| `GET /files/trend` | one file's score-over-time series + current delta + `declining` flag (`?file_path=`) |
| `GET /trend` | repo KPI history + alerts + last-two-snapshot per-file deltas |
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

`path` is an fnmatch glob over the repo-relative POSIX path; `path_glob`
and `glob` are accepted aliases. `to_analyzer_config(file_paths)` resolves
globs to per-file disabled sets, which the engine honors in
`_evaluate_file()`.

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
| `tests/unit/health/test_coverage_parsers.py` | LCOV / Cobertura / Clover / repowise-JSON happy paths + edge cases |
| `tests/unit/health/test_scoring.py` | Deduction caps, clamping, KPI math |
| `tests/unit/health/test_scoring_snapshot.py` | **Stability guard** — caps, severity table, biomarker→category mapping, two known fixture scores |
| `tests/unit/health/test_trends.py` | Declining + predicted alerts, ordering, per-file series + `file_trend` |
| `tests/unit/health/test_signals.py` | `file_signals` join — no-signal vs real-zero, entropy 0-1→0-100, owner handoff |
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
- **No predictive ML on trends.** `Predicted Decline` is a 3-snapshot
  direction check, not a model. (Commit-level change risk is a separate,
  shipped surface: the `analysis/change_risk/` package behind
  `repowise risk` scores a commit or base..head range with a calibrated
  logistic model.)
- **No letter grade.** The 1–10 score is the single number. The only
  categorical layer is the 3 defect-backed bands (Healthy/Warning/Alert,
  `grading.py`); a letter on top would be a third overlapping scale with
  arbitrary cliffs. The legacy 4-step `scoreBand` in `ui/health/tokens.ts`
  is retained only as a finer color ramp for file-table pills, not a
  labeling scheme — surfaced band labels and the distribution use the 3
  bands.

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
