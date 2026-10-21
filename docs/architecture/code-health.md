# Code Health: Architecture & Internals

How the code health layer works, for contributors: where each piece lives, how data
flows from parsed source to the dashboard, and how to extend it. What it does and how to
use it: [`docs/layers/CODE_HEALTH.md`](../layers/CODE_HEALTH.md).

---

## 1. Layer overview

Code Health is the **fifth intelligence layer**, alongside Graph, Git, Docs and
Decisions: a deterministic pipeline where tree-sitter walks every file once, markers
vote, and scores aggregate per category into four SQLAlchemy tables. It measures
technical debt (the code shape and change history that make a file likely to need the
next fix), reads Graph and Git without modifying them, and writes only its own tables.

```
Ingestion (parsed_files, git_meta_map, community_labels)
  -> HealthAnalyzer (engine.py) -> HealthReport
  -> delete/upsert -> SQLite: health_findings, health_file_metrics, health_snapshots, coverage_files
  -> CLI (health, status, health --trend) | MCP (get_health, get_risk, get_context, get_overview)
     | dashboard (/repos/[id]/code-health, tabs by ?tab=)
```

Three rules govern the layer: **zero LLM** (every marker is AST, git or coverage
math), **no JSON caches or intermediate files** (SQLite is the single source of truth
that the CLI, MCP server and Next.js dashboard read), and **no new runtime
dependencies** (pure Python over tree-sitter, already in tree: no lizard, no jscpd, no
Node).

---

## 2. Where things live

### Python: `packages/core/src/repowise/core/`

| `analysis/health/` | Holds |
|---|---|
| `__init__.py`, `engine.py` | Public API (`HealthAnalyzer`, `HealthReport`); the orchestrator: walker → biomarkers → scorer |
| `file_evaluator.py` | `FileEvaluator`: one file's context, detectors and score |
| `scoring.py`, `shape_curve.py` | Weighted aggregation, category caps, KPIs; the saturating curve, per-function grouping and critical scaling |
| `ranking.py`, `aggregation.py`, `grading.py` | Worst-first key and deduction fold; module rollups and breakdowns; the five bands and NLOC-weighted distribution |
| `crap.py`, `governance.py`, `finding_identity.py` | The CRAP score; the three governance findings; stable public finding ids |
| `defect_accuracy.py`, `trends.py`, `signals.py`, `churn_complexity.py` | Self-validation; snapshot diff, alerts and per-file series; per-file signals; churn × complexity points |
| `suggestions.py`, `config.py`, `models.py` | Refactoring text per biomarker; `HealthConfig` and `.repowise/health-rules.json`; result dataclasses |
| `fix_first/`, `worth.py`, `queue/`, `perf/` | Fix first and exclusion reasons (§6.3); performance opportunities (§6.4) |
| `complexity/` | tree-sitter walker (`walker.py`: CCN, nesting, cognitive, bumps, params, NLOC) and per-language maps (`languages.py`) |
| `coverage/` | Report parsers (LCOV, Cobertura, Clover, JaCoCo, Go coverprofile, normalized JSON; stdlib only) and the test-file heuristic |
| `duplication/` | Native Rabin-Karp clone detection: tokenizer, 64-bit rolling hash, clone pairs with co-change weighting |
| `biomarkers/` | `base.py` (Protocol, `FileContext`, `BiomarkerResult`), `registry.py` (`detect_all()`), and one `<id>.py` per detector (§5) |

Each sub-package has its own `README.md`.

### Persistence, pipeline, CLI and server

| Path | Holds |
|---|---|
| `core/persistence/models.py` | `HealthFinding`, `HealthFileMetric`, `HealthSnapshot`, `CoverageFile` |
| `core/persistence/crud/analysis/health/` | `save_` / `upsert_` / `get_` health functions; retention in `snapshots.py` |
| `packages/core/alembic/versions/0019_code_health.py` | Created the four tables; later health migrations add columns |
| `core/pipeline/phases/analysis.py`, `core/pipeline/persist/` | `_run_health_analysis()` (called by `orchestrator.py`); `persist_pipeline_result()` |
| `cli/.../commands/health_cmd/`, `status_cmd.py`, `update_cmd/` | `repowise health`, the status line, the incremental path |
| `server/.../mcp_server/tool_health/`, `tool_risk/`, `tool_context/`, `tool_overview/` | `get_health` and health enrichments (§12) |
| `server/.../routers/code_health/` | REST routes (§13) |
| `packages/ui/src/health/` | Shared React components (web and the hosted frontend) |
| `packages/web/src/app/repos/[id]/code-health/` | The Code Health page (§14); `repos/[id]/health/` holds redirects |
| `tests/unit/health/`, `tests/integration/test_health_*` | Tests (§18) |

---

## 3. The pipeline (init path)

`repowise init` runs `run_pipeline()` in `core/pipeline/orchestrator.py`, where
`_run_health_analysis()` (`pipeline/phases/analysis.py`) runs between `_run_dead_code_analysis()` and
`_run_decision_extraction()`. It:

1. builds a `{file_path: community label}` map from community detection, so
   `HealthFileMetric.module` is never NULL;
2. loads `.repowise/health-rules.json` via `HealthConfig.load(repo_path)` (a no-op when
   absent) and resolves per-file disabled sets with `to_analyzer_config()`;
3. constructs `HealthAnalyzer` with the NetworkX graph (dependents), `git_meta_map`
   (hotspot bit, owners, co-change, bus factor), the AST phase's `parsed_files` and the
   module map;
4. runs `analyze_async()` (asyncio gather over worker threads; tree-sitter releases the
   GIL) on repos with `>= 500` parsed files, else `analyze()` on one thread.

The report rides on `PipelineResult.health_report`, and `core/pipeline/persist/` writes
it in one session: `save_health_metrics`, `save_health_findings` (only when there are
findings), and `save_health_snapshot` with the three KPIs, a `{path: score}` map for
trends (rolling 50 rows per repo) and a `{path: total_deduction}` map for files held at
the floor. Both maps come from `trends.snapshot_file_maps`, which the other snapshot
writers (`repowise health --recompute`, `repowise update --full`) also call, so history
depth never depends on which command last wrote it.

---

## 4. Inside `HealthAnalyzer.analyze()`

One pass over the parsed files. Per file:

1. **Walk the AST** (`_walk`): `walk_file(language, source)` returns a `FileComplexity`.
   Each `FunctionComplexity` carries name, line range, nloc, ccn, max nesting, cognitive
   complexity, bumps and param count; each `ClassComplexity` carries method count, total
   nloc, the method list, LCOM4, max method ccn and field count. A nested function has a
   `FunctionComplexity` of its own (its branches count there, not in the outer
   function); a lambda counts toward the function that holds it.
2. **Populate symbol complexity** (`_populate_symbol_complexity`): write `max(ccn)` into
   `Symbol.complexity_estimate`, so the `ContextAssembler` symbol ranker benefits even
   without the health tables.
3. **Evaluate** (`FileEvaluator.evaluate`, `file_evaluator.py`): build a `FileContext`
   (nloc, `has_test_file`, module, function and class metrics, `git_meta`, in-degree as
   `dependents_count`, the repo p80 in-degree for `brain_method`, coverage when
   ingested, the file's clone slice), run `detect_all()`, score with `score_file()` and
   attach per-finding impacts.

Then `compute_kpis()` runs over the metrics and hotspot paths
(`git_meta_map[path]["is_hotspot"]`), returning `HealthReport(findings, metrics, kpis)`.
Duplication runs **once up-front** (it is cross-file); each `FileContext` gets a slice,
and `dry_violation` ranks clone pairs by co-change frequency
(`git_meta_map[path]["co_change_partners_json"]`), so active clones rank above dormant
ones.

---

## 5. The markers and their categories

Each marker is a stateless class implementing the `Biomarker` Protocol from
`biomarkers/base.py`: a `name` (`"brain_method"`, `"nested_complexity"`, ...), a
`category` (see `scoring.CATEGORY_CAPS`), and a `detect(ctx: FileContext)` method
returning a list of `BiomarkerResult`s.

`biomarkers/registry.py` is an **explicit list**, not auto-discovery: it keeps the
registration order deterministic and lets tests inject extras via
`registered_biomarkers(extra=...)`.

### The full roster

`biomarkers/registry.py` registers **54 detectors**; counting the three governance
findings written by the additive pass (`governance.py`) there are **57 marker ids**. They
divide by what each is permitted to affect:

| Group | Count | Scores into |
|---|---:|---|
| Defect-scoring | 24 | `defect` (10 of them also `maintainability`) |
| Performance | 22 | `performance` only |
| SQL | 3 | `maintainability` only |
| Governance | 3 | nothing: the finding surfaces, the score is untouched |
| Advisory | 5 | nothing: measured by construction, out of impact-ranked lists unless requested |

The authority is `scoring._BIOMARKER_DIMENSIONS`. An unlisted biomarker defaults into
`defect`, so every `sql_*` and performance name is listed explicitly: an omission would
silently break the defect golden guarantee (§6).

Thresholds are the minimum for the default severity (medium unless stated), then the
bands above it; "judged" CCN and nesting follow the dispatch rule (§5.5). Scoring gives
the defect weight (§6.1), "maint" the maintainability and "perf" the performance weight.

| `id` | Category | Flags | Threshold | Scoring |
|---|---|---|---|---|
| `brain_method` | Structural | A long, complex, central function | NLOC 70, judged CCN 9, file dependents ≥ min(8, max(repo p80, 3)); high at CCN 14 or NLOC 120; critical at CCN 20 and NLOC 150 | Advisory |
| `low_cohesion` | Structural | A class split into unrelated method groups | LCOM4 2 and 5 methods; high at LCOM4 3 or 20 methods; critical at LCOM4 4 and 15 methods | 0.5; maint 1.0 |
| `god_class` | Structural | A large class hiding a brain method | 200 NLOC, 15 methods, one method at NLOC 70 and CCN 9; high at 300 NLOC or 20 methods; critical at 400 and 25 | 1.13; maint 1.0 |
| `nested_complexity` | Structural | Deep nesting in a function | Judged depth 4; high at 5; critical at 7 | 1.34; maint 1.0 |
| `repeated_nesting` | Structural | Several blocks at one nesting depth | 3 bumps (low) and judged CCN 5; medium at 4 bumps, high at 5 | 0.5; maint 1.0 |
| `complex_conditional` | Structural | A boolean expression with many operators | 3 operators (low); medium 4, high 5, critical 6; a flat chain stays low | 1.33; maint 1.0 |
| `complex_method` | Size | High cyclomatic complexity | Judged CCN 9; high 15; critical 25 | 1.21; maint 1.0 |
| `large_method` | Size | A long function | 60 code lines (low) and judged CCN 3; medium 90, high 120, critical 200 | 1.25; maint 1.0 |
| `primitive_obsession` | Size | A long list of scalar parameters | 5 parameters (constructors 7), over half the typed ones primitive, file at 60 NLOC, not a test (low); medium at threshold + 2, high at + 4 | 0.5; maint 1.0 |
| `dry_violation` | Duplication | A cross-file clone | File duplication 8% and a clone of 6 lines (low); medium with active co-change (3) or 25% duplication; high with both | 0.5; maint 1.0; hidden by default |
| `error_handling` | Error handling | Swallowed or catch-all handler, Rust `unwrap`/panic, discarded Go error | Low, one per occurrence | 0.5; maint 1.0 |
| `untested_hotspot` | Test coverage | A depended-on hotspot with weak test evidence | Hotspot, 4 dependents, coverage under 40% (no report: no paired or reaching test); high at ≤ 15% or 10 dependents; critical at both | 1.3 |
| `coverage_gap` | Test coverage | A large uncovered surface (needs a report) | Under 30% with 50 coverable lines (high at 100 uncovered); under 60% with 25 uncovered is low | 1.0 |
| `untested_complexity` | Test coverage | A complex function tests barely run | CRAP 30; high 50 and critical 100 on measured coverage only | Advisory |
| `coverage_gradient` | Coverage gradient | Uncovered share of a file (needs a report) | `4.0 × (1 − line_coverage_pct/100)` points; label high under 40%, medium under 70% | 1.0, continuous |
| `developer_congestion` | History | Many authors, no owner, high churn | 5 contributors, top quarter of churn, 3 commits in 90 days, nobody owns half (low); medium at 7 contributors or 12 commits; high at 10 and 20 | 0.5 |
| `knowledge_loss` | History | A changing file whose sole real author (bus factor 1) is gone or under 20% of recent commits | Low; medium when the owner is gone and under 20% of recent commits; high on a hotspot; low on a small team (≤ 3 contributors) off a hotspot | 0.4 |
| `hidden_coupling` | History | Files that co-change with no import edge | 5 commits each side, correlation 0.5; high 0.65 (with 8 co-changes), critical 0.8; at most 3 per file; tests skipped | Advisory |
| `function_hotspot` | History | A complex function changed often | Changes ≥ max(repo p80, 5) with CCN 10 or nesting 3; high at 2× the bar or CCN 15; critical at 3× and CCN 20 | 1.16 |
| `code_age_volatility` | History | A dormant function changed again | Median line age 365 days and 2 changes in 30 days; high at 730 days or 5 changes; critical at both | 1.1 |
| `ownership_risk` | History | Many minor contributors or no dominant owner | 5 commits and 3 minor contributors (each under 5%) or no author above 40% (low when only that arm fires); medium at 3 minors, high at 5 (3 on a hotspot), critical at 6 on a hotspot; low under 40 code lines or on a small team off a hotspot | 1.38 |
| `churn_risk` | History | Rewrote much of its own lines in 90 days | 5 commits in 90 days, top quarter of churn, relative churn 1.0 (low); medium 1.5, high 2.5, critical 4 on a hotspot | 1.2 |
| `change_entropy` | History | Scattered, noisy changes | Entropy percentile 0.80, 3 commits in 90 days; high 0.90; critical 0.95 on a hotspot | 1.51 |
| `co_change_scatter` | History | Coupled to many files (shotgun surgery) | Percentile 0.80, 3 commits in 90 days, not a test or barrel; high at 0.95 | 1.8 |
| `prior_defect` | History | Recent bug-fix commits | 1 fix in 180 days at percentile 0.80 (low); medium 2, high 3-4 (critical on a hotspot), critical 5 | 1.0 |
| `ungoverned_hotspot` | History | A hotspot no decision governs | Medium; from the decisions layer | Governance 0.7; never deducts |
| `stale_governance` | History | A governing decision gone stale | High; from the decisions layer | Governance 0.9; never deducts |
| `contradictory_decision` | History | Decisions that contradict each other | High; from the decisions layer | Governance 1.0; never deducts |
| `large_assertion_block` | Test quality | One test asserting many behaviours | 15 consecutive assertions; high at 30; test files only | 1.0 |
| `duplicated_assertion_block` | Test quality | Copy-pasted assertion runs | 5 real checks per block; test files only | 1.0; hidden by default |
| `assertion_free_test` | Test quality | A test that checks nothing | Low; Python, JavaScript, TypeScript, TSX | Advisory |
| `mock_saturated_test` | Test quality | Mock setup dwarfs the assertions | 6 mock setups at 3 per assertion (low); medium at 8; Python, JavaScript, TypeScript | Advisory |
| `sql_high_complexity` | SQL | A complex SQL routine | CCN 10; high at CCN 20 | maint 0.7 |
| `sql_select_star` | SQL | `SELECT *` | Low | maint 0.7 |
| `sql_update_delete_without_where` | SQL | `UPDATE` or `DELETE` with no `WHERE` | Medium | maint 0.7 |
| `sql_cartesian_join` | Performance | A cartesian join in SQL | Medium | perf 0.6 |
| `io_in_loop` | Performance | I/O per loop iteration (N+1), across functions | Medium | perf 1.0 |
| `string_concat_in_loop` | Performance | `+=` of a provable string in a loop | Low | perf 0.7 |
| `blocking_sync_in_async` | Performance | A sync blocking call in `async def` | Medium; Python, Rust, C#, Kotlin, Scala | perf 0.7 |
| `regex_compile_in_loop` | Performance | A static regex compiled per iteration | Medium; Java, Go, Rust, C++, Kotlin, Ruby, Scala | perf 0.6 |
| `defer_in_loop` | Performance | `defer` inside a loop | Medium; Go | perf 0.6 |
| `resource_construction_in_loop` | Performance | A client built per iteration | Medium | perf 0.7 |
| `lock_in_loop` | Performance | A lock taken each iteration | Low | perf 0.5 |
| `serial_await_in_loop` | Performance | Awaited I/O run serially in a loop | Low | perf 0.4 |
| `membership_test_against_list_in_loop` | Performance | `x in list` (or `.includes`) on a provable list in a loop | Low | perf 0.7 |
| `unbounded_read_reduced_in_memory` | Performance | An unlimited query reduced per key in code | Low; Python | perf 0.4 |
| `lazy_load_in_loop` | Performance | An ORM lazy relation read per iteration | Low; SQLAlchemy, Django | perf 0.4 (Django 0.7) |
| `nested_loop_with_io` | Performance | I/O in a loop nested two deep | Medium | perf 0.5 |
| `nested_loop_quadratic` | Performance | A quadratic nested loop, widely called | Low; centrality gated | perf 0.4 |
| `hot_path_sync_io` | Performance | Sync I/O in a widely called function | Low; centrality gated | perf 0.5 |
| `blocking_io_under_lock` | Performance | Blocking I/O while holding a lock | Medium | perf 0.6 |
| `list_insert_zero_in_loop` | Performance | `list.insert(0, x)` in a loop | Medium; Python | perf 0.6 |
| `pd_concat_in_loop` | Performance | `pd.concat` accumulating in a loop | Medium; Python | perf 0.6 |
| `pandas_iterrows_in_loop` | Performance | `df.iterrows()` | Medium; Python | perf 0.6 |
| `json_parse_in_loop` | Performance | JSON parse or stringify per iteration | Low; JavaScript, TypeScript | perf 0.4 |
| `array_spread_in_reduce` | Performance | `[...acc]` inside `reduce` | Medium; JavaScript, TypeScript | perf 0.5 |
| `goroutine_in_unbounded_loop` | Performance | A goroutine per range element | Medium; Go | perf 0.4 |

Notes:

- `biomarker_names.py` maps the legacy id `bumpy_road` to `repeated_nesting`.
- "Hidden by default": `analysis/finding_registry.py` keeps the type off every default
  surface (overview, priorities, MCP, REST lists) until a re-measurement clears its
  precision; it is still computed, stored and scored.
- Performance markers with no language listed run in every perf dialect that models the
  construct (§6.4, [PERFORMANCE.md](../layers/PERFORMANCE.md)).

### Defect categories and caps

| Category | Defect cap | Maintainability cap |
|---|---|---|
| Organizational (history and governance) | 3.5‡ | none |
| Structural complexity | 2.5 | 4.0 |
| Test coverage | 2.0 | none |
| Test coverage gradient | 2.0 | none |
| Size & complexity | 1.5 | 2.0 |
| Duplication | 1.0 | 2.0 |
| Test quality | 0.5 | none |
| Error handling | 0.5 | 2.0 |
| SQL | none | 2.0 |

Maintainability caps (`_MAINTAINABILITY_CATEGORY`) and the single `performance`
category (2.0) are hard caps. Governance markers carry a category and weight, but their
pass runs *after* scoring and never touches `HealthFileMetric.score`, so they never
deduct.

‡ **History cap.** 3.5 is a ceiling. The live cap is `history_cap(structure)`,
`min(3.5, 1.0 + structure)`, where `structure` is the file's capped deduction from every
other defect category, so git history alone costs a file at most 1.0. Before that cap,
the history findings de-stack: the largest counts whole and each other one at 0.25
(`destacked`), since a busy file trips several history markers for one cause.

**Saturating curve.** Every other defect category follows a curve rather than stopping
at its cap (`shape_curve.py`). A category's raw total `R` deducts `A * (1 - exp(-R / A))`
with `A = 3.09 x cap`, so a second critical function still costs something and no
category ever deducts more than 3.09 times its cap.

- Function-shape findings (`FUNCTION_SHAPE_MARKERS`) on one function count the largest in full and the rest at 0.25.
- A critical `complex_method`, `large_method` or `nested_complexity` finding deducts
  `2.0 x value / threshold` (CCN 25, 200 lines, nesting 7), so halving a CCN-50 function
  pays even while it stays critical.
- The 3.09 is fitted so the corpus mean score matches the hard-capped one; the curve is
  AUC-neutral on the 66-repo corpus.

### Marker notes

**Git process markers** read fields the git indexer already produces: `ownership_risk`
(Bird et al.; `top_authors_json`), `churn_risk` (size-normalized, Nagappan-Ball;
`lines_added_90d`, `churn_percentile`), `change_entropy` (Hassan's History Complexity
Metric; `change_entropy`, `change_entropy_pct`, §5.1), `co_change_scatter` (D'Ambros;
`co_change_partners_json`) and `prior_defect` (`prior_defect_count`).
`knowledge_loss` is activity-gated, so abandoned-but-stable files (the survivor effect)
do not fire.

**`prior_defect`** (recent bug-fix history; Ostrand-Weyuker, Kim's "bug cache") counts
non-merge bug-fix commits touching a file in the trailing ~6-month window.

- A fix is classified with the **same keyword rule the defect benchmark labels fixes
  with** (`_constants.is_fix_commit`).
- The window is anchored to the index's `as_of` (the indexed commit's committer date),
  so scoring a historical T0 checkout never leaks the post-T0 fixes that form the
  benchmark's labels.
- Its **neutral (1.0) weight is by design**: it is largely redundant with the other
  process signals (correlation ≈ +0.59 with `change_entropy`, +0.38 with churn;
  coefficient ≈ +0.02; Popt gain within bootstrap noise). It ships for its
  **explanatory** value ("bug-fixed N times recently") and flags a few files the other
  signals miss.

**`coverage_gradient`** makes the coverage signal **continuous**.

- The binary gates (`untested_hotspot`, `coverage_gap`) fire only below ≈40-60% line
  coverage, so on a well-tested codebase (most files at 85-99%) they are blind to the
  uncovered fraction that still carries defect signal.
- It deducts `4.0 × (1 − line_coverage_pct/100)` points (2.0 at 50% uncovered), settled
  in its own category (`test_coverage_gradient`, cap 2.0), so it neither squeezes nor is
  squeezed by the binary gates.
- It uses the `deduction` override on `BiomarkerResult` (a continuous magnitude in place
  of the severity table), so it stays **linear and per-finding attributable**.
- It is **silent with no ingested report** (`line_coverage_pct is None`), skips test
  files, and, like `coverage_gap`, skips a file that holds only a docstring, imports and
  export lists (`__all__`): a package `__init__.py` or an `index.ts` barrel.
- Calibrated offline, it recovers **+0.043 corpus AUC [95% CI +0.023, +0.061]** on the
  covered subset (≈65% of the continuous-feature ceiling), Popt-neutral, and exactly
  zero without coverage: a purely additive improvement.

**Class-level markers.** `low_cohesion` (LCOM4) and `god_class` read
`ctx.class_metrics` (§5.2). `brain_method`'s centrality gate is **language-agnostic**: it
fires when a file is in the repo's top quintile of connected files
(`repo_dependents_p80`, computed once per analyze) or clears the absolute hub bar of 8,
so sparse-graph languages (TS barrels, Rust) are not silent.

**`large_method` and `complex_method`.** `large_method` needs a judged CCN ≥ 3, so a
long-but-flat body (a big data literal, a dispatch of one-line arms) reads as layout. In
`.tsx` / `.jsx` it counts code lines only: a line of nothing but JSX tags, attributes or
text is markup; a line with a `{...}` expression is code. Its reason reads "N lines of
code (M with blanks and comments)", and `details.span` carries M. `complex_method`
reports the judged CCN that sets its severity, plus the raw CCN when they differ.

**`error_handling`** is the **advisory maintainability** marker.

- It flags swallowed catches (an empty or comment-only `catch` / `except: pass`), Python
  `except:` / `except Exception:`, Rust `.unwrap()` / `.expect()` / panic-family macros,
  and Go's empty `if err != nil {}` or blank-identifier discard of a call's error.
- The walker finds them in a whole-tree pass (module-level code included), reusing the
  `LanguageNodeMap` catch kinds for the seven catch-shaped languages plus dedicated Rust
  and Go recognizers; an unsupported language or parse failure is "no signal".
- One LOW finding per occurrence (0.15 after its floored 0.5 weight), in its own
  category with a 0.5 cap, mirroring `test_quality`. Under the saturating curve the
  category deducts at most 3.09 × 0.5 ≈ 1.5 points.
- It is **excluded from the defect-weight calibration**: AUC-neutral on the 21-repo /
  9-language T0 benchmark (OOF delta ≈ 0, CI crosses zero) but size-orthogonal and the
  least redundant signal tested. It ships because users expect `except: pass` flagged.

**Test quality.** `large_assertion_block` and `duplicated_assertion_block` (§5.3) fire
only on test files, in a deliberately small category so a noisy test can't dominate its
own score.

### 5.1 Change-entropy git-layer fields

`change_entropy` is computed in the **single** FULL-tier co-change walk
(`ingestion/git_indexer/co_change.py::compute_co_changes_and_entropy`), with no extra
`git log` subprocess.

- A commit touching tracked files `F` with `2 ≤ |F| ≤ 30` has entropy `log2(|F|)`; wider
  commits are dropped as noise (Hassan's filter).
- Entropy is spread uniformly (`1/|F|` per file) and decayed with co-change's τ=180d
  half-life; the decay-weighted sum per file is `git_meta["change_entropy"]`.
- `enrich.compute_percentiles` ranks **only files with positive entropy** into
  `change_entropy_pct`; zero-entropy files (the ESSENTIAL tier, or files only ever
  changed alone) keep 0.0, so the marker stays silent.
- Both fields live on `git_metadata` (migration `0025`); the additive-reconcile path
  back-fills them on legacy DBs.

### 5.2 Class-level walker metrics (LCOM4)

The walker emits a `ClassComplexity` per class-like node in languages that opt in
(`LanguageNodeMap.class_kinds` non-empty: Python, TS/JS, Java, Kotlin, Rust `impl`, C++,
C#; Go has no grouping node).

- LCOM4 is the number of connected components in the graph of the class's methods,
  linked when they share an instance field or call one another. Member references are
  found per language via `self`/`this`/`$this` member access.
- **Safety valve:** a class with no detected member references (a static utility, an
  unmapped language) reports `lcom4 = 1` ("no signal"), not `len(methods)`, so adding a
  language can only turn signal on, never flood false positives.

Full heuristic and limits: `complexity/README.md`.

### 5.3 Assertion-block walker metrics (test-quality)

The same walker pass records `assertion_blocks` on each `FunctionComplexity`: runs of
≥ 2 consecutive assertions, each `(start_line, end_line, count)`.

- An assertion is a bare `assert` (`LanguageNodeMap.assert_kinds`) or a call
  (`assert_call_kinds`) whose callee starts with `assert` or `expect`: `assertEqual`,
  `assert_eq!`, `expect(...).toBe(...)` across xUnit and BDD styles.
- Opt-in per language; all nine full-tier languages (Python, TS/JS, Java, Kotlin, Go,
  Rust, C++, C#) are mapped, and a language mapping neither field emits no blocks.
- `large_assertion_block` flags one run ≥ 15; `duplicated_assertion_block` intersects
  the clone report with assertion spans. Both gate on `coverage.is_test_file(path)`.

### 5.4 Advisory markers and their precision record

Advisory markers are detected, stored and listed, and deduct nothing.

- `advisory` is not in `DIMENSIONS`, so no weight, category or cap table can key on it;
  "never deducts" is structural.
- A marker must appear in both `_BIOMARKER_DIMENSIONS` and `_ADVISORY_HOME`, or it still
  deducts from defect.
- Advisory findings carry `health_impact == 0.0`, never count toward a change's
  introduced/worsened totals, never make a review verdict blocking, and stay out of
  impact-ranked lists unless the dimension or marker is named.

A marker earns weight by clearing roughly 70% hand-labelled precision on a real corpus.
Precision is measured per language because it does not transfer:

- `mock_saturated_test`: 67% on Python (32 findings) and 40% on TypeScript (30), each the
  full population at the shipped gate. Both figures are a ceiling: they predate oracle
  shapes the assertion count gained, which only suppress findings. It stays advisory
  until two false-positive families separate (`Fake*` value-object builders passed into
  real logic; boundary isolation where the assertion reads a real artifact), a dataflow
  question of whether an assertion observes a double or production output.
- `assertion_free_test`: at least 71% on TypeScript (31 findings, the full population of
  two corpora; a floor, as three false-positive families closed after labelling) and 86%
  on Python (29 labelled, a systematic sample of 172). No Go or Java. An oracle in a
  helper is resolved by name in the file and through the call graph across files; a
  mock verification counts as an oracle here, the opposite of `mock_saturated_test`
  (tiers in `complexity/assertions.py`).
- `hidden_coupling`: alone it ranks defect-prone files near chance (AUC about 0.55), and
  a pre-registered test on 12 repositories no earlier health study used found the score
  without it non-inferior.
- `brain_method`: it fires only where `complex_method` and `large_method` already
  charge, so scoring it double-charged; dropping it left defect AUC unchanged on the
  66-repo corpus. It stays listed under structural complexity.
- `untested_complexity`: complexity and coverage already deduct through their own
  markers ([user guide](../layers/TEST_INTELLIGENCE.md#untested-complexity)).

### 5.5 Worked example: the dispatch rule

`nested_complexity` adds a level on each `if` / `for` / `while` / `try` / `switch` and
fires at depth 4, severity scaling with depth. A function that is mostly one dispatch
on one value (a `switch`, `match` or same-subject `if` chain holding at least 60% of its
decision points) is judged by the code around the dispatch and its heaviest arm:
`nested_complexity` skips the two levels the `switch` and its `case` open, and
`complex_method`, `brain_method` and `repeated_nesting` read the CCN outside the
dispatch plus that of its most complex arm.

In TypeScript and JavaScript (TSX / JSX included) a ternary opens no level when it is an
arm of a chain (`a ? x : b ? y : z`), sits inside JSX markup, or has a JSX branch: a view
choosing one of ten panels is one decision per arm, not ten levels (each arm still adds
to CCN and a flat +1 cognitive, like an `else if`). Still nesting: a ternary in
another's `consequence`, one in a call argument or object literal inside JSX
(`{fmt(a ? b : c)}`), one whose branch is not JSX (`cond ? c2 && <A/> : x`), and every
`if` / `for` / `try` in a handler written in JSX.

---

## 6. Scoring (`scoring.py`)

Every file starts at **10.0**, each finding deducts, each category settles its total,
and the score is clamped to `[1.0, 10.0]`.

- A finding deducts by severity (`low=0.3, medium=0.7, high=1.2, critical=2.0`) times
  its marker's weight multiplier (§6.1).
- `score_file()` sums the weighted deductions per category, and each total settles by
  its rule (§5): the saturating curve, the history cap, or a hard cap in
  maintainability and performance.
- Each finding is scaled by its category's settled total over the raw total, so the
  parts still sum to the category's deduction and "this finding cost you X points" stays
  honest after capping.
- That scaled deduction is `HealthFinding.health_impact` (via `attach_impacts()`, a
  defect-pillar quantity): the dashboard's "−2.0" badge.

`tests/unit/health/test_scoring_snapshot.py` locks the caps, severity deductions,
marker-to-category mapping and two known-fixture scores, so a retune updates the
snapshot in the same PR.

### 6.1 Calibrated weight multipliers

`scoring._BIOMARKER_WEIGHT_MULTIPLIER` (values in [the full roster](#the-full-roster))
lets the strongest empirical predictors deduct more than the severity table alone.

- The multipliers are **calibrated offline, not hand-tuned**: each file is scored at the
  pre-window commit (T0, no leakage), and an L2-logistic regression with NLOC as a
  control fits each marker's defect lift beyond size. Only the learned constants ship;
  the runtime stays deterministic. Full calibration with confidence intervals:
  [benchmark report](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/BENCHMARK_REPORT.md);
  protocol in §6.2.
- **Strongest:** `co_change_scatter` (1.8, strongest calibrated predictor),
  `change_entropy` (1.51, second), `ownership_risk` (1.38), `nested_complexity` (1.34,
  strongest structural). The other structural and size markers and `function_hotspot`
  carry a moderate lift (1.13-1.33).
- **Prior weights kept:** `untested_hotspot` 1.3, `churn_risk` 1.2, `code_age_volatility`
  1.1: coverage-dependent or rarely firing, so the corpus could not fairly measure them.
- **Neutral or default:** `prior_defect` 1.0 by design; `coverage_gap`,
  `coverage_gradient` and the assertion-block markers take the default 1.0.
- **Floored at 0.5:** `developer_congestion`, `dry_violation`, `low_cohesion`,
  `primitive_obsession`, `repeated_nesting` fire widely but proved weak under
  leakage-free scoring; they stay as maintainability and parity signals.
  `knowledge_loss` is de-rated to 0.4, the weakest. `error_handling` is 0.5 and not
  fitted (§5, marker notes).

The same stream feeds **maintainability** (an independent expert-set table, where the
floored smells deduct at full weight) and **performance** (its own bounded category).
The three signals share one kernel against separate weight/category/cap tables and never
feed each other ([what each surfaces](../layers/CODE_HEALTH.md#the-three-signals)). The
headline stays the defect score: the band cutoffs and every accuracy figure are claims
about it, and `test_scoring_dimensions` locks `score_file(...)["defect"]` byte-for-byte
to the pre-split single score. Blending pillars would need a written rationale and a
recalibration corpus.

### 6.2 Validation and calibration

Headline results: [BENCHMARKS.md](../BENCHMARKS.md#code-health-predicts-defects).

- **Protocol.** Every file is scored at a commit T0 before the bug window, in a detached
  worktree whose history stops at T0; labels are the next six months of bug-fix
  commits. An L2-regularized logistic regression with NLOC as a control column fits each
  marker's lift beyond size, with leave-one-repo-out cross-validation. Only the learned
  constants ship.
- **Leakage runs both ways.** Scoring at HEAD lets bug-fix commits manufacture the
  churn, co-change and congestion the process markers then "detect". Scoring naively at
  T0 silences every recency-windowed marker (a worktree six months back sees an empty
  "last 90 days"), so recency windows are anchored to the worktree's own HEAD (`as_of`),
  not wall-clock time. `developer_congestion` shipped at 1.5 under HEAD scoring and came
  back at coefficient -0.08 under T0 scoring, hence its 0.5 floor.
- **Rejected.** Graph centrality (PageRank), code naturalness, change bursts and
  error-handling density were equivalent to null by bootstrap TOST, not merely
  non-significant. Size-relative scoring (costs 0.07 to 0.12 AUC overall), function-level
  prediction (Popt at or below random effort ordering) and recalibrating on SZZ labels
  (lower out-of-fold AUC) were run and refuted.
- **Two corpora.** The cross-project result uses 21 repositories, 9 languages, 2,826
  files (379 defect-bearing). The comparison with a commercial code-health tool, and the
  band-separation figures (16.9x raw, 2.18x size-normalized, At risk vs 8.0 and above),
  use the 2,770 of those files both tools could score.
- **Intervals** come from a two-stage repo-cluster bootstrap (2,000 seeded replicates,
  repositories then files), since the repository is the unit of generalization.

| Cross-project result (2,826 files) | Value |
|---|---|
| Mean ROC AUC | 0.737 (95% CI 0.683 to 0.787) |
| Held out: PROMISE jEdit 4.0 / 4.1 (no git history, structural markers only) | 0.761 / 0.776 |
| vs. recent churn / prior-defect history | +0.100 AUC (DeLong p = 5e-10) / +0.117 (p = 3e-15) |
| Per-repo range | 0.55 (axios) to 0.86 (zod) |

Limits on the same corpus:

- LOC alone scores AUC 0.742 (delta -0.001, p = 0.92); the win is effort-aware, Popt
  +0.134 (95% CI +0.080 to +0.198).
- Within NLOC quartiles AUC is 0.525 / 0.572 / 0.593 / 0.718. A positive control of
  headline magnitude was recovered with power 0.998 to 1.000 in those quartiles, so the
  collapse is a property of the score, not of sample size.
- A prior-defects baseline beats the score on Popt by 0.085 (95% CI 0.035 to 0.141).

| Paired test vs a commercial tool (2,770 files) | Repowise | Commercial tool | p |
|---|---:|---:|---|
| Recall at 20%-of-lines budget | 0.173 | 0.074 | 0.003 |
| Popt | 0.607 | 0.462 | 0.003 |
| Defect density, size-normalized | 2.18x | 0.56x | 0.003 |
| ROC AUC | 0.731 | 0.705 | 0.054 (not significant) |
| Defect density, raw | 16.9x | 14.2x | 0.65 (not significant) |
| Precision at 20%-of-lines budget | 0.580 | 0.636 | 0.64 (tie) |
| Partial rho beyond size | -0.148 | -0.137 | both beat size (tie) |

The commercial tool flags 27 files at its most severe level where Repowise flags 132 At
risk: a more conservative operating point, not a calibration flaw. Its published -0.58
correlation between that level and issue-resolution time (proprietary Jira data) did not
replicate on open GitHub data: -0.09 (95% CI -0.19 to +0.14), and six queue-independent
effort signals were flat.

**In-product check.** `defect_accuracy.py` takes the 20 lowest-scoring files and counts
those touched by a fix commit in the trailing 180 days against the repo base rate,
silent below 25 scored files or 5 fixed files. Because `prior_defect` is an input, this
is an association on indexed history, not a forward test.

Method and data:
[repowise-bench/health-defect](https://github.com/repowise-dev/repowise-bench/tree/master/health-defect)
([COMPARISON_REPORT.md](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/COMPARISON_REPORT.md)).

### 6.3 Fix first ranking (`fix_first/`, `worth.py`)

Fix first is built once in `analysis/health/fix_first/build.py` from stored rows and
rendered unchanged by `get_health`, `repowise health`, the dashboard and the generated
CLAUDE.md. Index and update save the queue and the Do next list, so these surfaces open
without rebuilding them.

Every excluded unit is counted in `totals.excluded` under one reason. These reasons and
the performance default queue's are decided in one place, `analysis/health/queue/`:

| Reason | What it leaves out |
|---|---|
| `test`, `tooling`, `generated`, `vendored`, `docs_example` | Path-role classification (see notes) |
| `unknown`, `expected`, `no_strategy`, `no_plan` | Performance causes: no classified context, expected repetition, no supported strategy, or no stored safe plan ([PERFORMANCE.md](../layers/PERFORMANCE.md)) |
| `below_min_worth` | A refactoring recovering under `MIN_WORTH` (0.5) or with no steps |
| `unmeasured_cost`, `cold_role`, `background_unproven`, `unconfirmed_cost` | Causes the default queue drops, and queued causes `perf_confident_verdict` does not confirm (growth only a registry or cold-path loop shows, or no role and no reaching entry point) |
| `history_only` | Only git-history findings |
| `deprecated` | Deprecated functions |
| `gated_off` | Functions a same-file constant-false flag switches off (`complexity/gating.py`); see notes |
| `unreachable` | Covered by an open dead-code finding (confidence 0.8+ or safe to delete) on the whole file, the symbol's lines, or its name |
| `inherent_dispatch` | One dispatch holds 60% or more of the decision points (labels: 8 of 9 such rows rejected), unless a duplicate also sits in it |
| `small_function` | Under 30 code lines and CCN 15 (on 67 labelled rows this cut drops 13 rejected and 4 accepted) |
| `no_concrete_step` | No first edit with a file and line or a named group |
| `low_value_kind` | Low cohesion (0 of 46 accepted), long method (0 of 10), long parameter lists (0 of 2) |
| `kind_unaudited` | Plans led by Extract Class (0 of 14 accepted) or Move Method (0 of 34), kept to the full list until each passes a per-language audit |
| `terminal_flow` | A quarter or more of the function's calls print or prompt (`complexity/terminal.py`): a status report, checklist or setup wizard, whose branches choose what to show |
| `moderate_quiet` | Under CCN 80 and 200 lines, in a file outside the top fifth by churn (or changed once), with no `prior_defect` in the file and no `function_hotspot` on the function |

Notes:

- Tooling includes Dart's `tool/` scripts and build files by type wherever they sit:
  Gradle, CMake, Makefiles, Bazel, MSBuild props/targets, root `setup.py`,
  `noxfile.py`, crate-root `build.rs`, bundler configs.
- Test helpers named for a module's tests (`bot.test-harness.ts`,
  `card-test-helpers.ts`, `test-support.mjs`) count as test support, and bundler output
  (`*.bundle.js`) as generated code. Neither leads Fix first, the refactoring list or
  the dashboard's worst-file lists, which say how many files they left out and why.
- Docs means a root `docs/` or `docs_src/` tree, or one beside a docs-site config; a
  product's own `app/docs/` route is production.
- `gated_off` (dormant): the body opens with `if not FLAG: return ...` or is one
  `if FLAG:` block, and `FLAG` is a module-level `False`, `0` or `None`
  (`const FLAG = false` in TypeScript or JavaScript) nothing in the same file assigns
  again. Only the file is read: a flag imported from another module, or flipped from
  outside with `setattr` or a test's monkeypatch, still reads as off.
- Dormant functions are counted next to the excluded totals (`totals.dormant`, distinct
  functions), not dropped silently. For performance this is the opportunity state
  `expected` with reason `gated_off`, and the default queue counts it under `gated_off`.

#### Order

1. **Value**: the larger of health recovered and size (CCN 20/40/80/150, lines
   100/200/400/800, nesting 5/6/8, a critical or brain-method finding), one step more on
   a hot file, where a function under 5% of its file's lines is credited that share of
   the file's importers. A performance fix's value is its cost, with production,
   entry-reachable, data-growing DB or network calls in the top band.
2. **Tier** `now` / `next` / `later`, with at most `HEAD_PER_KIND` (3) of the first
   `HEAD` (5) places per kind.
3. **Inside a band**: complexity removed times reach (so a 4,000-line loop outranks a
   long print routine) times the lead function's history, `1 + ln(1 + n)` for the `n`
   commits that changed it (`git_function_blame`). With no function history, a file too
   large to blame, a unit led by no function, or a performance cause, a file in the top
   fifth by churn weighs 1.25 instead.

`worth.py` decides `later` for every default list:

- a function under CCN 40, 200 lines and nesting 6 (nesting under 8 only counts at 100
  lines or more), and not both CCN 25 and nesting 5;
- a dominant dispatch;
- nesting that is one else-if or ternary chain;
- long but CCN under 20 and nesting under 5;
- a single complex condition or error site.

From CCN 80, 400 lines or nesting 8 a function is worth doing regardless. The hot-file
bonus orders items but never lifts one out of `later`. Each item carries up to
`MAX_TESTS` (5) tests from its validation profile.

The findings list uses the same rule: worth-doing findings lead and the rest carry
`lower_priority`. Class-design findings and history-only findings are lower priority
there; a performance opportunity is unless production code runs it over growing data.

#### Fix first steps

A finding's first step can borrow a stored refactoring plan. A "Break up" reason says
what splitting buys: smaller parts that are easier to read and change, and less
duplicated code when part of the function is a copy. "Due" means tier `now` or `next`.

- A refactoring item is `later` when any step's plan still leaves a function above the
  break-up bar; a finding whose first step borrows such a plan keeps its tier, and the
  step says the plan alone leaves the function above the bar.
- An Extract Method step finds its lines by the text they start and end with, as the
  plan does, since line numbers move with the first edit; when that text also appears
  elsewhere in the file, the step says to keep the edit inside its function.

### 6.4 Performance opportunities (`perf/`)

What the layer finds, opportunity states, the default queue and its exclusion reasons:
[PERFORMANCE.md](../layers/PERFORMANCE.md). The internals:

- **Sinks.** `io_in_loop` resolves a call inside a loop through the graph's resolver and
  classifies the target against the I/O-boundary lexicon (`io_boundaries.py`: db,
  network, filesystem, subprocess, lock). It fires only on a real round-trip, never on a
  query-builder chain. A non-local sink is found by walking the resolved call graph
  backwards up to three hops (`crossfn.py`), and the `caller -> ... -> sink` path is
  attached.
- **Gates.** Two markers fire only in functions at or above the 80th percentile of
  distinct direct callers, never under two: a measure of how widely a function is
  called, not request reachability. `blocking_sync_in_async` skips tests, tooling,
  examples, generated and vendored code.
- **Grouping.** One opportunity per intervention site within one cost family and
  boundary (`<file>::__module__` for top-level code). Sinks, call sites and same-family
  co-signals (`io_in_loop` with `nested_loop_with_io`) are members; findings carry the
  sink's line. Each carries one `actionability` state. A group every member's name or
  file stem marks as cold (`perf/cold_paths.py`) is `expected`/`cold_path` with or
  without a strategy; `gated_off` takes precedence. The identity kernel's
  `execution_context` stays path-only.
- **Queue.** The default queue ranks by `rank_score`; excluded reasons are counted in
  `default_queue`. Fix first, Do next and the queue's lead take only the queued
  `plan_ready` causes `queue.eligibility.perf_confident_verdict` confirms (facet
  `growth_source = loop`); advisory causes measured 0 true of 14 on three repositories,
  so they stay in the queue.
- **Plans.** Proven means the transformation, not the runtime: parallelizing awaits
  against a DB or network client is advisory with a `bounded_concurrency` prerequisite,
  and a loop already walking chunks is `loop_already_chunked`. When `io_in_loop` and
  `serial_await_in_loop` fire on one call they name each other in `siblings`. Edits are
  `mechanical` only when proven; each step names the call it repeats
  (`details.sink_call`, or the helper a cross-function path enters), however many
  markers saw it.
- **Buffers and sets.** `buffer_string_accumulation` and `replace_membership_collection`
  list one step per loop and variable, on the loop's line, naming the variable
  (`details.subject`) where the walk reads it as a plain name; effort counts those
  steps. Validation tests come from coverage, call graph, import graph, then
  `via: "name-match"`. An opportunity links to a `performance_fix` plan only by exact
  `opportunity_id`.
- **Dataflow promotion.** `serial_await_in_loop` and `nested_loop_quadratic` become
  `dataflow_verified: true` when a def/use pass proves iterations independent; this
  changes the wording, not the score.
- **ORM lazy loads.** `lazy_load_in_loop` (sync SQLAlchemy, Django) needs the loop's rows
  to resolve to one model through the cross-file model index and the attribute to be a
  lazy relation the query does not eager-load. `_PERFORMANCE_ORM_WEIGHT` reads the weight
  from `details.orm`: Django measured 29/32 (90.6%) held out and carries 0.7; SQLAlchemy,
  with under 30 held-out labels, stays at 0.4. `unbounded_read_reduced_in_memory`
  (Python) flags an unlimited query whose rows are deduplicated per key in code.
- **Dialects.** `perf/dialects/` registers 12 dialects over 19 language tags (TS/JS also
  serve Vue, Svelte and Astro script blocks; C# serves Razor). C shares the C++ grammar
  but has no perf dialect; a language without one emits nothing.
- **Soundness.** Dynamic dispatch, monkeypatching and callbacks-as-values produce no call
  edge; ORM lazy loads are seen only where rows can be typed; chains beyond three hops
  are not followed; an unmodelled library has no classified sinks. Linter comparison and
  ranking quality:
  [repowise-bench/perf-detection](https://github.com/repowise-dev/repowise-bench/tree/master/perf-detection).

---

## 7. KPIs

`compute_kpis()` produces three repo-level numbers: **Hotspot Health** (NLOC-weighted
average over files where `git_meta_map[path]["is_hotspot"]`), **Average Health**
(NLOC-weighted over all files) and **Worst Performer** (lowest-scoring file and its
score). They flow into `HealthSnapshot` rows (rolling 50 per repo) and feed the CLI
status one-liner, the `get_overview()` MCP block and the dashboard KPI cards.

---

## 8. Trends (`trends.py`)

State-free: callers pass an oldest-first list of snapshot rows. Both alerts run over
every metric in `_ALERT_METRICS` (hotspot health, the composite headline and
maintainability), skipping any a snapshot never recorded.

- **Declining Health:** current is ≥ `DECLINE_THRESHOLD` (0.5) below the snapshot
  `DECLINE_LOOKBACK` (5) positions back; fires on the 6th+ snapshot.
- **Predicted Decline:** the three most recent snapshots each strictly below the one
  before; direction is the signal, magnitude is not required.
- **`history_drag`:** either alert as a third `kind`, when the composite headline fell
  with `driver` `history` and the structure half held or improved. Same numbers,
  opposite reading: nothing in the code to act on, so not error red (which would say a
  refactoring made things worse). Maintainability is code shape already; it never
  softens.

`recent_kpis(history, limit=10)` returns a newest-first view for the CLI table and
`get_health(include=["trend"])`.

### Per-file trajectory

Snapshots also store `{path: score}` (`per_file_scores_json`):

- `file_score_series(history, path)`: oldest-first `FileTrendPoint`s, skipping snapshots
  without the file; `[]` below two points. The PR bot reuses it for its in-comment
  sparkline.
- `file_trend(history, path)`: `current` / `previous` / `delta` and a `declining` flag (≥
  `DECLINE_THRESHOLD` below the lookback point, or `PREDICTED_DECLINE_CONSECUTIVE`
  consecutive drops). `snapshot_count` is the full window, so a young repo is
  distinguishable from a file missing in older snapshots.
- The server serialises `FileTrend` via `_file_trend_to_dict` into the file-detail
  health block, the breakdown response and the standalone trend route (§13).

#### Below the floor

The stored score is clamped to `[SCORE_FLOOR, SCORE_MAX]`, so files 12.9 and 9.1 points
deep both persist as `1.0`. The second snapshot map (`per_file_deductions_json`) keeps
the pre-clamp deduction for exactly those files (for every other file it is
`SCORE_MAX - score`). Each point carries `unclamped_score` (`SCORE_MAX - deduction` where
recorded, else `score`), and `_file_declining` runs on it, so a file getting worse
*below* the floor trips `declining`. Snapshots without the map keep floored files flat:
the depth was never measured, and inventing one would be worse.

### Per-file signals (`signals.py`)

`file_signals(git_meta, degrees)` joins one `GitMetadata` row with the file's graph
degree into a `FileSignals`, surfacing persisted fields otherwise buried in marker
detail cards, with no recompute: **Process** (`prior_defect_count`,
`change_entropy_pct` normalized 0-100, 90-day line churn, `age_days`), **People**
(recent vs all-time owner and commit share) and **Topology** (`in_degree` /
`out_degree`).

- A field is `None` only when its *source row* is absent (no git history: process and
  people silent; not a graph node: topology silent), never imputed. A real
  `prior_defect_count` of `0` is kept.
- Served via `_file_signals_to_dict` in the file-detail health block and the breakdown
  response (§13); MCP attaches a null-dropped copy to the `get_context` health block
  (§12). Mirrored as `FileSignals` in `@repowise-dev/types/health` and rendered by
  `file-signals-panel.tsx` in the drawer and the file-page Health tab.

---

## 9. Incremental analysis: the `repowise update` path

`HealthAnalyzer.analyze()` takes a `changed_files` set. Duplication still runs full-repo
(a clone partner may be unchanged), the per-file loop skips other files, and KPIs are
**not** recomputed (a subset would bias them; the dashboard recomputes them from the
merged rows). `update_cmd/` builds the set from `change_detector.get_changed_files()`
and persists with the **upsert** variants (`upsert_health_metrics`,
`upsert_health_findings`, scoped to the changed paths). Full init uses delete-then-insert
(`save_health_findings`, `save_health_metrics`): simpler, and amortised across the
whole `repowise init`.

---

## 10. Persistence schema

Four tables in the repo's `.repowise/wiki.db`, foreign-keyed to `repositories.id` with
`ON DELETE CASCADE`.

### `health_findings`

One row per marker hit. Lifecycle `open → acknowledged | resolved | false_positive`
(matches Dead Code). Open rows are rewritten on full init and per changed file on
`repowise update`.

| Column | Notes |
|---|---|
| `id`, `repository_id`, `file_path` | UUID PK; FK; indexed |
| `biomarker_type`, `severity` | `brain_method`, ...; `low` / `medium` / `high` / `critical` |
| `function_name`, `line_start`, `line_end` | nullable (file-level findings) |
| `details_json` | per-marker evidence (CCN values, clone span, etc.) |
| `health_impact`, `reason` | per-finding scaled deduction; one-line summary |
| `status`, `created_at`, `updated_at` | lifecycle; datetimes |

**Identity** (`finding_identity.py`): `finding_<digest>` from the enclosing function or
class name plus the line offset into it (absolute lines for file-level findings), so an
edit above keeps it; of the details, only keys that tell findings apart (a coupling
partner, an error kind) count, never metric values. A re-detected triaged finding gets
fresh evidence in place: `acknowledged` and `false_positive` stay, `resolved` reopens.

### `health_file_metrics`

One row per file (unique on `(repository_id, file_path)`), read directly by the
dashboard's file table.

| Column | Notes |
|---|---|
| `score` | 1.0–10.0 final |
| `defect_score`, `maintainability_score`, `performance_score` | per-pillar; nullable |
| `structure_deduction`, `history_deduction` | the two halves of the deduction; `counts=code_shape` rescores from `structure_deduction`, and a null pair (rows before the split) is unscored, not a ten |
| `is_test` | stored so `scope` is one row filter everywhere; nullable on older rows |
| `max_ccn`, `max_nesting`, `nloc` | aggregate function metrics |
| `duplication_pct` | percent of NLOC covered by clones; nullable |
| `has_test_file` | paired or heuristic |
| `line_coverage_pct`, `branch_coverage_pct` | nullable |
| `module` | community label; falls back to top-level dir |
| `updated_at` | datetime |

### `health_snapshots`

KPI + per-file score history; a rolling delete keeps the latest 50 per repo
(`HEALTH_SNAPSHOT_RETENTION` in `persistence/crud/analysis/health/snapshots.py`).

### `coverage_files`

Per-file coverage, overwritten on every `coverage add`. The explicit
`covered_lines_json` array lets `coverage_gap` flag the exact uncovered surface.

---

## 11. CLI surface

`packages/cli/src/repowise/cli/commands/health_cmd/` mirrors the dead-code command's
Click structure.

```bash
repowise health                            # KPIs + lowest-scoring files + findings
repowise health --file path/to/x.py        # deep-dive one file
repowise health --module packages/server   # restrict to a directory prefix
repowise health --refactoring-targets      # ranked by impact / effort
repowise health --trend                    # last 10 snapshots + active alerts
repowise coverage add coverage.lcov        # ingest coverage; can repeat
repowise coverage add coverage.xml --format cobertura
repowise coverage check --fail-under 80    # patch-coverage gate for CI, no index needed
repowise health --format json | jq ...
repowise status                            # Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: ...)
```

`repowise update` silently re-scores changed files only.

---

## 12. MCP surface

### `get_health(targets?, include?, repo?, limit?)`

`tool_health/tool.py` dispatches to one module per mode and block.

- **Dashboard** (`targets=None`): KPIs with the repo `band`, the NLOC-weighted
  `distribution`, `worst_files`, `top_findings` and a per-module `modules` rollup.
- **Targeted** (`targets=[...]`): full `metrics` + `findings`, plus a per-file `trends`
  block (series, `current`, `delta`, `declining`) for targets with two or more
  snapshots. `module:foo` expands to that module's files.
- `include` adds blocks (`biomarkers`, `coverage`, `refactoring` suggestions, `trend`,
  `untested_complexity`, `recipe` (with `plan_id`), `what_if`,
  `accuracy` §6.2, `performance` §6.4, `signals`, `churn_complexity`, `doc_drift`,
  `unverified`, `semantics`) and picks the ranked-list dimensions (`defect`,
  `maintainability`, `advisory`; default defect + maintainability).
  `request.py::_KNOWN_INCLUDES` is the authority; every flag:
  [MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_health).

### Enrichments on existing tools

- `get_risk(targets)`: `health_score`, `top_biomarkers`, `line_coverage_pct`,
  `branch_coverage_pct` per target.
- `get_context(targets, include=["health"])`: `score`, `max_ccn`, `max_nesting`, `nloc`,
  `module`, `duplication_pct`, top 2 markers with a `suggestion`, a coverage block and a
  null-dropped `signals` block (§8).
- `get_overview()`: a `code_health` block with avg, repo `band`, hotspot, worst
  performer, open finding count and the file count per band.

Every response carries the standard `_meta` envelope via `build_meta()`.

---

## 13. REST surface

`server/.../routers/code_health/` is one module per surface, sharing `scope.py`,
`counts.py`, `file_filters.py` and `statuses.py`. Routes sit under
`/api/repos/{repo_id}/health/`; auth is the standard `verify_api_key` dependency from
`server/deps.py`.

| Route | Returns |
|---|---|
| `GET /overview` | summary (with `band`), `distribution`, lowest-scoring files, top findings, module rollup |
| `GET /badge.svg`, `GET /badge.json` | flat SVG badge (colour + `N.N/10`, no letter); Shields.io payload (`schemaVersion`/`label`/`message`/`color`/`band`) |
| `GET /files`, `GET /files/breakdown` | per-file metrics; one file's score breakdown, findings, suggestions, `trend` and `signals` |
| `GET /files/trend`, `GET /trend` | one file's series, delta and `declining` (`?file_path=`); repo KPI history, alerts, last-two-snapshot per-file deltas |
| `GET /findings` | findings, filtered by `biomarker_type`, `file_path`, `dimension`, `status`, `severity` or `min_severity` |
| `GET /coverage`, `POST /coverage` | coverage summary and rows; ingest a report (some CI integrations) |
| `GET /refactoring-targets` | the work queue, ranked by `total_impact / effort_bucket` |
| `GET /churn-complexity` | one point per churned file: `commit_count_90d`, `max_ccn`, `nloc`, `score`, `churn_percentile` |
| `GET /modules` | NLOC-weighted module rollup |
| Also | `fix-first` (and `/{fix_id}`), `map`, `impact-effort`, `related-work`, `tests-reaching`, `performance-opportunities` (and `/{opportunity_id}`, `/findings`), `coverage/patch`, `findings/{id}` |

- The zero-impact dimensions (performance, advisory) are out of the ranked `/findings`
  list unless named in `dimension`, a marker is named in `biomarker_type`, or
  `include_zero_impact=true`.
- `/refactoring-targets` takes the findings filters plus `search`, `module` and
  `only_hotspots` / `only_untested` / `only_failing`, pages by `limit` + `offset`, and
  returns `total` with `finding_total`. Impact counts open findings only, so dismissing
  work moves a file down.
- `scope` and `counts` are parsed by `ScopeQuery` / `CountsQuery` wherever they change a
  figure, and echoed; an unrecognized value falls back to the default rather than answer
  another question under the asked name.
- `counts=code_shape` is a projection, not a rescore: scores from
  `structure_deduction`, history-derived findings dropped from row sets (so a list
  cannot sum past the figure above it), `total` and rankings recomputed. Rows with no
  stored split are counted in `unscored_files`. Nothing is written back.

---

## 14. Web dashboard

One page, `packages/web/src/app/repos/[id]/code-health/page.tsx`: a map whose lens
switches between health, maintainability, performance and churn (the churn lens pairs
with `ChurnComplexityQuadrant`, fed by `GET /churn-complexity`), `scope` and `counts`
controls, and four tabs by `?tab=` (`tabs.ts`): Map (the default, leads with Fix first;
`triage`, `heatmap`, `hotspots`, `modules` and `trend` alias to it), Findings,
Performance and Dead code. Tests and Security are pages of their own, Doc drift a Docs
view, and blast radius the Commits page's "Review a change" view; their old tab ids
(`coverage`, `security`, `doc-drift`, `impact`) redirect there. The file Health tab
carries the per-function "Functions by churn" blame table. Older `/repos/[id]/health/*`
routes redirect; `code-health/coverage`, `code-health/refactoring-targets` and
`code-health/trend` are sub-pages. Visual primitives live in `packages/ui/src/health/`
so the hosted `frontend/` repo (separate checkout) can reuse them; a port is mostly data
fetching + auth.

---

## 15. CLAUDE.md integration

The generated `CLAUDE.md` has a short `## Code health` section when the health tables
are populated (filters in `core/generation/editor_files/data.py`, stanza in
`core/generation/templates/_editor_block.j2`). It keeps only: score ≤ 5.0 **and** a
hotspot; any Brain Method in a file with > 10 dependents; any Untested Hotspot; DRY
violations > 70 % similarity; a declining trend (> 1.0 drop in the last 5 snapshots).
Everything else is filtered so a fresh agent is not drowned in noise.

---

## 16. Configuration: `.repowise/health-rules.json`

The user-authored `.repowise/health-rules.json` (the **only** JSON file in the layer) is
loaded by `HealthConfig.load(repo_path)`. Repo-wide and per-path `disabled_biomarkers`
and `severity_overrides` are keyed by a gitignore-semantics glob over the repo-relative
POSIX path (`path`, aliases `path_glob` and `glob`); every matching rule applies and
disabled sets union. `to_analyzer_config(file_paths)` resolves them to per-file disabled
sets, honored in `FileEvaluator.evaluate()`. Schema and examples:
[user guide](../layers/CODE_HEALTH.md#configuration).

---

## 17. Performance

The budget is **< 30 s on a 3,000-file synthetic repo**.

- `analyze_async()` (§3) uses `asyncio.gather` + `asyncio.to_thread`, which scales on
  single-process CPython because tree-sitter releases the GIL.
- `test_health_perf_benchmark.py` is marked `slow` (`pytest -m slow`, `make health-bench`).
- Duplication is O(total_tokens), near-linear on repos with low duplication.
- The walker re-parses files because `ParsedFile` doesn't retain a tree-sitter `Tree`
  across the ingestion boundary (~1 ms per file; a shared parse cache would remove it).
- Scoring has no N² loops: category aggregation is O(findings).

---

## 18. Testing

`make health-check` runs `tests/unit/health/`, `tests/unit/server/test_mcp.py` and a health report on this repository.
In `tests/unit/health/`:

- `test_complexity_walker.py`: per-language CCN, nesting, cognitive on handcrafted
  fixtures.
- `test_<biomarker>.py` (with `test_biomarkers.py`, `test_structural_biomarkers.py`,
  `test_coverage_biomarkers.py`, `test_organizational_biomarkers.py`,
  `test_dry_violation.py`): each marker positive in two languages plus one negative.
- `test_duplication.py`: tokenizer normalization, rolling-hash determinism, co-change
  weighting. `test_coverage_parsers.py`: LCOV / Cobertura / Clover / JaCoCo / Go /
  repowise-JSON happy paths and edge cases.
- `test_scoring.py`: caps, clamping, KPI math. `test_scoring_snapshot.py`, the
  **stability guard**: caps, severity table, marker-to-category mapping, two fixture
  scores. `test_scoring_dimensions.py`: the defect score byte-for-byte equal to the
  pre-split single score.
- `test_trends.py` (alerts, per-file series), `test_signals.py` (no-signal vs real-zero,
  entropy 0-1 to 0-100, owner handoff), `test_churn_complexity.py` (no-churn omission,
  complexity never filters, danger-product sort, percentile scaling),
  `test_suggestions.py`, `test_health_config.py` (rules parsing and glob matching).

Integration: `test_health_coverage_integration.py` (LCOV -> analyzer -> `coverage_gap`
fires) and `test_health_perf_benchmark.py` (30 s on 3,000 synthetic files, `-m slow`).

---

## 19. Extension points

### Add a marker

1. A file under `biomarkers/` implementing the `Biomarker` Protocol, named for the id.
2. Append to `_DETECTOR_FACTORIES` in `biomarkers/registry.py`.
3. List it in `scoring._BIOMARKER_DIMENSIONS`; an unlisted marker silently defaults into
   `defect`.
4. Add it to its dimensions' tables: `_BIOMARKER_CATEGORY` (and
   `_BIOMARKER_WEIGHT_MULTIPLIER` when not 1.0) for defect; `_MAINTAINABILITY_CATEGORY`
   and `_MAINTAINABILITY_WEIGHT_MULTIPLIER`; `_PERFORMANCE_WEIGHT_MULTIPLIER`; or
   `_ADVISORY_HOME` (§5.4).
5. A label in `biomarker_labels.BIOMARKER_LABELS` and a template in
   `suggestions._TEMPLATES`.
6. At least three tests (two positive in different languages, one negative).
7. A row in [the full roster](#the-full-roster) and in `biomarkers/README.md`.

### Add a language to the complexity walker

One `LanguageNodeMap` entry in `complexity/languages.py` maps the language's
tree-sitter control-flow node types to `BRANCH` / `LOOP` / `TRY` / `BOOLEAN_OP`, plus a
fixture under `tests/fixtures/lang_samples/<lang>/`. **No `.scm` files**: the ingestion
parser owns those.

### Add a coverage format

A stdlib-only parser under `coverage/` returning a `CoverageReport`, routed from
`coverage/detector.parse`.

### Add a per-file override

Users author `.repowise/health-rules.json`. A new key beyond `disabled_biomarkers`
extends `HealthConfig` and threads through `to_analyzer_config()` ->
`FileEvaluator.evaluate()` (`file_evaluator.py`).

---

## 20. Where the layer deliberately stops

- **No LLM in scoring or suggestions.** `suggestions.py` is static templates; LLM code
  generation exists only behind `--generate-code` and the opt-in
  `generate_refactoring_code` MCP tool, and never feeds a score.
- **No symbol-level scoring.** The score is per file, matching refactor units;
  symbol-level CCN still feeds it via `all_functions`.
- **No `complexity_estimate` backfill.** The walker writes it during the current run;
  old indexes change only on a re-index.
- **No predictive ML on trends.** `Predicted Decline` is a 3-snapshot direction check.
  Commit-level change risk is separate: `analysis/change_risk/` behind `repowise risk`
  scores a commit or base..head range with a calibrated logistic model.
- **No letter grade.** The 1–10 score is the single number, and the five absolute bands
  (Excellent / Good / Fair / Needs work / At risk, `grading.py`) the only categorical
  layer; a letter would be a third overlapping scale with arbitrary cliffs. Every band
  word or colour reads that one vocabulary.

---

## 21. Quick lookup: where do I edit X?

| I want to... | Edit... |
|---|---|
| Tweak a cap or severity deduction | `scoring.CATEGORY_CAPS` / `_SEVERITY_DEDUCTION` (update the snapshot test) |
| Change suggestion text | `suggestions._TEMPLATES` |
| Adjust the trend-alert threshold | `trends.DECLINE_THRESHOLD` / `DECLINE_LOOKBACK` |
| Add an MCP `include` flag | `tool_health/`: `request.py`, then `loading.py`, then `blocks.py` |
| Add a REST route | `routers/code_health/` (auth is wired at the router level) |
| Add a new entry point | call `HealthAnalyzer.analyze()`; persist with the upsert variants when incremental |

Markers: §19. Retention, CLI, dashboard and UI paths: §2 and §10.

---

## See also

- [`docs/layers/CODE_HEALTH.md`](../layers/CODE_HEALTH.md): user-facing guide.
- [`packages/core/src/repowise/core/analysis/health/README.md`](../../packages/core/src/repowise/core/analysis/health/README.md): developer overview at the layer root.
- Sub-package READMEs under `complexity/`, `coverage/`, `duplication/`, `biomarkers/`.
- [`docs/architecture/graph-algorithms.md`](graph-algorithms.md): the graph layer health depends on.
