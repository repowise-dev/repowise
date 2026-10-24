# Code Health: Architecture & Internals

How the code health layer works, for contributors. What it does and how to use it:
[`docs/layers/CODE_HEALTH.md`](../layers/CODE_HEALTH.md).

---

## 1. Layer overview

Code Health is the **fifth intelligence layer**, beside Graph, Git, Docs and Decisions:
a deterministic pipeline where tree-sitter walks every file once, markers vote, and
scores aggregate per category into four SQLAlchemy tables. It reads Graph and Git
without modifying them.

```
Ingestion (parsed_files, git_meta_map, community_labels)
  -> HealthAnalyzer (engine.py) -> HealthReport
  -> delete/upsert -> SQLite: health_findings, health_file_metrics, health_snapshots, coverage_files
  -> CLI (health, status, health --trend) | MCP (get_health, get_risk, get_context, get_overview)
     | dashboard (/repos/[id]/code-health, tabs by ?tab=)
```

Rules: **zero LLM** (every marker is AST, git or coverage math); **no JSON caches or
intermediate files** (SQLite is the one source the CLI, MCP server and Next.js dashboard
read); **no new runtime dependencies** (pure Python over tree-sitter: no lizard, jscpd or
Node).

---

## 2. Where things live

| `packages/core/src/repowise/core/analysis/health/` | Holds |
|---|---|
| `__init__.py`, `engine.py`, `file_evaluator.py` | `HealthAnalyzer`, `HealthReport`; walker → biomarkers → scorer; one file's context, detectors and score |
| `scoring.py`, `shape_curve.py`, `ranking.py`, `aggregation.py`, `grading.py` | Aggregation, caps, KPIs; the saturating curve; worst-first key; rollups; the five bands |
| `crap.py`, `governance.py`, `finding_identity.py`, `defect_accuracy.py` | CRAP score; governance findings; stable ids; self-validation |
| `trends.py`, `signals.py`, `churn_complexity.py`, `suggestions.py`, `config.py`, `models.py` | Alerts and series; per-file signals; churn × complexity; refactoring text; `HealthConfig`; dataclasses |
| `fix_first/`, `worth.py`, `queue/`, `perf/` | Fix first (§6.3); performance opportunities (§6.4) |
| `complexity/`, `coverage/`, `duplication/` | tree-sitter walker (`walker.py`, `languages.py`); stdlib report parsers and the test-file heuristic; Rabin-Karp clones (64-bit rolling hash, co-change weighting). Each has a `README.md` |
| `biomarkers/` | `base.py` (Protocol, `FileContext`, `BiomarkerResult`), `registry.py` (`detect_all()`), one `<id>.py` per detector (§5) |

| Elsewhere | Holds |
|---|---|
| `core/persistence/models.py`, `core/persistence/crud/analysis/health/` | The four ORM tables; `save_` / `upsert_` / `get_` functions |
| `packages/core/alembic/versions/0019_code_health.py` | Created the tables; later health migrations add columns |
| `core/pipeline/phases/analysis.py`, `core/pipeline/persist/` | `_run_health_analysis()`; `persist_pipeline_result()` |
| `cli/.../commands/health_cmd/`, `status_cmd.py`, `update_cmd/` | CLI (§11) |
| `server/.../mcp_server/tool_health/`, `tool_risk/`, `tool_context/`, `tool_overview/`; `server/.../routers/code_health/` | MCP (§12); REST (§13) |
| `packages/ui/src/health/`, `packages/web/src/app/repos/[id]/code-health/` | Shared React components (web and hosted frontend); the page (§14) |
| `tests/unit/health/`, `tests/integration/test_health_*` | Tests (§18) |

---

## 3. The pipeline (init path)

`run_pipeline()` (`core/pipeline/orchestrator.py`) calls `_run_health_analysis()` between
`_run_dead_code_analysis()` and `_run_decision_extraction()`. It:

1. maps `{file_path: community label}` so `HealthFileMetric.module` is never NULL;
2. loads `.repowise/health-rules.json` (`HealthConfig.load`, a no-op when absent) into
   per-file disabled sets (`to_analyzer_config()`);
3. builds `HealthAnalyzer` from the NetworkX graph, `git_meta_map` (hotspot bit, owners,
   co-change, bus factor), `parsed_files` and the module map;
4. runs `analyze_async()` (worker threads; tree-sitter releases the GIL) at `>= 500`
   parsed files, else `analyze()`.

`core/pipeline/persist/` writes `PipelineResult.health_report` in one session:
`save_health_metrics`, `save_health_findings` (when there are findings) and
`save_health_snapshot` (the KPIs, a `{path: score}` map, and `{path: total_deduction}`
for floored files). `trends.snapshot_file_maps` builds both maps for every snapshot
writer (also `health --recompute` and `update --full`), so history depth never depends on
the last writer.

---

## 4. Inside `HealthAnalyzer.analyze()`

Per file:

1. **Walk** (`_walk`, `walk_file`): each `FunctionComplexity` carries name, line range,
   nloc, ccn, max nesting, cognitive complexity, bumps and params; each
   `ClassComplexity` carries method count and list, total nloc, LCOM4, max method ccn and
   field count. A nested function gets its own entry; a lambda counts toward its holder.
2. **Symbol complexity** (`_populate_symbol_complexity`): `max(ccn)` into
   `Symbol.complexity_estimate` for the `ContextAssembler` ranker.
3. **Evaluate** (`FileEvaluator.evaluate`): a `FileContext` (nloc, `has_test_file`,
   module, metrics, `git_meta`, in-degree as `dependents_count`, the repo p80 in-degree,
   coverage, the clone slice), then `detect_all()`, `score_file()` and per-finding
   impacts.

`compute_kpis()` then gives the hotspot, average (both NLOC-weighted) and worst KPIs.
Duplication runs **once up-front**; `dry_violation` ranks clone pairs by co-change
(`co_change_partners_json`), so active clones rank above dormant ones.

---

## 5. The markers and their categories

Each marker is a stateless class implementing the `Biomarker` Protocol
(`biomarkers/base.py`): `name`, `category` (`scoring.CATEGORY_CAPS`) and
`detect(ctx: FileContext) -> list[BiomarkerResult]`. `biomarkers/registry.py` is an
**explicit list**, so order is deterministic and tests inject extras via
`registered_biomarkers(extra=...)`.

### The full roster

`biomarkers/registry.py` registers **59 detectors**; counting the three governance
findings written by the additive pass (`governance.py`) there are **62 marker ids**. They
divide by what each is permitted to affect:

| Group | Count | Scores into |
|---|---:|---|
| Defect-scoring | 24 | `defect` (10 of them also `maintainability`) |
| Performance | 22 | `performance` only |
| SQL | 3 | `maintainability` only |
| Governance | 3 | nothing: the finding surfaces, the score is untouched |
| Advisory | 10 | nothing: measured by construction, out of impact-ranked lists unless requested |

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
| `apex_soql_injection` | Apex | A String from outside the method spliced into a quoted literal of a dynamic query, unescaped | High; non-test Apex | Advisory |
| `apex_missing_sharing` | Apex | A top-level class with queries or updates and no sharing keyword | Medium; not subclasses, inner or test classes | Advisory |
| `apex_hardcoded_id` | Apex | A record Id as a string literal | Medium; 18 chars with a valid checksum or 15 on a standard prefix; non-test Apex | Advisory |
| `apex_see_all_data` | Apex | `@IsTest(SeeAllData=true)` | Low | Advisory |
| `cobol_unstructured_flow` | COBOL | A paragraph or section with `ALTER`, `GO TO ... DEPENDING ON`, or a `GO TO` that is not a forward jump to an `...EXIT` paragraph of its own `PERFORM THRU` range | Low; one finding per paragraph | Advisory |
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
| `json_parse_in_loop` | Performance | A `JSON.parse(JSON.stringify(x))` deep clone per iteration | Low; JavaScript, TypeScript | perf 0.4 |
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

- Maintainability caps (`_MAINTAINABILITY_CATEGORY`) and the single `performance`
  category (2.0) are hard caps. Governance findings are written *after* scoring and never
  touch `HealthFileMetric.score`.
- ‡ **History** is a hard cap, `history_cap(structure) = min(3.5, 1.0 + structure)`,
  where `structure` is the file's settled deduction from every other defect category.
  Before it, `destacked` counts the largest history finding whole and the rest at 0.25.
- **Every other defect category saturates** (`shape_curve.py`): raw total `R` deducts
  `A * (1 - exp(-R / A))` with `A = 3.09 x cap`, so no category deducts more than 3.09
  times its cap. The 3.09 is fitted so the corpus mean matches the hard-capped score;
  the curve is AUC-neutral on the 66-repo corpus.
- Function-shape findings (`FUNCTION_SHAPE_MARKERS`) on one function count the largest
  whole and the rest at 0.25. A critical `complex_method`, `large_method` or
  `nested_complexity` deducts `2.0 x value / threshold` (CCN 25, 200 lines, nesting 7),
  so halving a CCN-50 function pays while it stays critical.

### Marker notes

- **Git process markers** read indexer fields: `ownership_risk` (Bird et al.;
  `top_authors_json`), `churn_risk` (size-normalized, Nagappan-Ball; `lines_added_90d`,
  `churn_percentile`), `change_entropy` (Hassan; §5.1), `co_change_scatter` (D'Ambros;
  `co_change_partners_json`), `prior_defect` (Ostrand-Weyuker, Kim's "bug cache";
  `prior_defect_count`). `knowledge_loss` is activity-gated, so abandoned-but-stable files
  (the survivor effect) stay silent.
- **`prior_defect`** counts non-merge fix commits in the trailing ~6 months, classified by
  the **same keyword rule the defect benchmark labels with** (`_constants.is_fix_commit`)
  and anchored to the index's `as_of` (the indexed commit's date), so a T0 checkout never
  sees post-T0 fixes. Its **neutral 1.0 weight is by design**: correlation ≈ +0.59 with
  `change_entropy` and +0.38 with churn, coefficient ≈ +0.02, Popt gain within noise. It
  ships for its **explanatory** value and catches a few files the others miss.
- **`coverage_gradient`** makes coverage **continuous**: the binary gates fire only below
  ≈40-60%, blind to the 85-99% files where the uncovered share still predicts defects. Its
  `deduction` override on `BiomarkerResult` keeps it **linear and per-finding
  attributable** (2.0 points at 50% uncovered), in its own category so it neither squeezes nor is squeezed by the
  gates. It is **silent without a report** (`line_coverage_pct is None`), skips tests and,
  like `coverage_gap`, files of only a docstring, imports and `__all__` (an `__init__.py`
  or `index.ts` barrel). It adds **+0.043 corpus AUC [95% CI +0.023, +0.061]** on the
  covered subset (≈65% of the continuous-feature ceiling), Popt-neutral, and zero
  without coverage.
- **Class-level:** `low_cohesion` and `god_class` read `ctx.class_metrics` (§5.2).
  `brain_method`'s gate is **language-agnostic**: the repo's top quintile of connected
  files (`repo_dependents_p80`, once per analyze) or the absolute hub bar of 8, so sparse
  graphs (TS barrels, Rust) are not silent.
- **`large_method`** needs judged CCN ≥ 3, so a long-but-flat body (a data literal, a
  dispatch of one-line arms) reads as layout. In `.tsx` / `.jsx` a line of only JSX tags,
  attributes or text is markup; a `{...}` expression is code. Its reason reads "N lines of
  code (M with blanks and comments)", with M in `details.span`. `complex_method` reports
  the judged CCN, plus the raw CCN when they differ.
- **`error_handling`** (advisory maintainability) flags empty or comment-only `catch` /
  `except: pass`, Python `except:` / `except Exception:`, Rust `.unwrap()` / `.expect()` /
  panic macros, and Go's empty `if err != nil {}` or blank-identifier error discard. A
  whole-tree walker pass (module code included) reuses the catch kinds of the seven
  catch-shaped languages plus Rust and Go recognizers; unsupported or unparsable is "no
  signal". One LOW finding per occurrence (0.15 after the 0.5 floor); its 0.5-cap
  category deducts at most 3.09 × 0.5 ≈ 1.5. It is **excluded from calibration**:
  AUC-neutral on the 21-repo / 9-language T0 benchmark (OOF delta ≈ 0) but size-orthogonal
  and the least redundant signal tested; users expect `except: pass` flagged.
- **Test quality:** the two assertion-block markers (§5.3) fire only on test files, in a
  small category so a noisy test can't dominate its score.

### 5.1 Change-entropy git-layer fields

Computed in the **single** FULL-tier co-change walk
(`ingestion/git_indexer/co_change.py::compute_co_changes_and_entropy`), no extra
`git log`. A commit touching `2 ≤ |F| ≤ 30` tracked files has entropy `log2(|F|)` (wider
ones are noise, Hassan's filter), spread `1/|F|` per file and decayed with co-change's
τ=180d half-life into `git_meta["change_entropy"]`. `enrich.compute_percentiles` ranks
**only positive-entropy files** into `change_entropy_pct`; zero-entropy files (the
ESSENTIAL tier, files only ever changed alone) keep 0.0. Both live on `git_metadata`
(migration `0025`), back-filled on legacy DBs by additive reconcile.

### 5.2 Class-level walker metrics (LCOM4)

Languages with non-empty `LanguageNodeMap.class_kinds` (Python, TS/JS, Java, Kotlin, Rust
`impl`, C++, C#; Go has no grouping node) get a `ClassComplexity` per class. LCOM4 counts
connected components of the class's methods, linked by a shared instance field or a call,
found via `self`/`this`/`$this` member access. **Safety valve:** no detected member
references (a static utility, an unmapped language) gives `lcom4 = 1` ("no signal"), so a
new language can only turn signal on. Details: `complexity/README.md`.

### 5.3 Assertion-block walker metrics (test-quality)

The walker records `assertion_blocks` per `FunctionComplexity`: runs of ≥ 2 consecutive
assertions as `(start_line, end_line, count)`. An assertion is a bare `assert`
(`assert_kinds`) or a call (`assert_call_kinds`) whose callee starts with `assert` or
`expect` (`assertEqual`, `assert_eq!`, `expect(...).toBe(...)`). All nine full-tier
languages (Python, TS/JS, Java, Kotlin, Go, Rust, C++, C#) are mapped; a language mapping neither field emits none. Both block markers
gate on `coverage.is_test_file(path)`; `duplicated_assertion_block` intersects the clone
report with assertion spans.

### 5.4 Advisory markers and their precision record

`advisory` is not in `DIMENSIONS`, so no weight, category or cap table can key on it. A
marker must be in both `_BIOMARKER_DIMENSIONS` and `_ADVISORY_HOME`, or it still deducts.
Advisory findings carry `health_impact == 0.0`, never count toward a change's
introduced/worsened totals or make a review blocking, and stay out of impact-ranked lists
unless named. A marker earns weight at roughly 70% hand-labelled precision, measured per
language because it does not transfer:

- `mock_saturated_test`: 67% Python (32 findings), 40% TypeScript (30), each the full
  population at the shipped gate and a ceiling (later oracle shapes only suppress). It
  waits on two false-positive families, `Fake*` builders passed into real logic and
  boundary isolation where the assertion reads a real artifact: a dataflow question.
- `assertion_free_test`: ≥ 71% TypeScript (31, the full population of two corpora; a
  floor, three false-positive families closed after labelling), 86% Python (29 labelled,
  a systematic sample of 172); not Go or Java. A helper's oracle resolves by name in the
  file and via the call graph across files; a mock verification counts as an oracle, the opposite of `mock_saturated_test`
  (tiers in `complexity/assertions.py`).
- `hidden_coupling`: near-chance alone (AUC about 0.55); a pre-registered test on 12
  repositories no earlier health study used found the score non-inferior without it.
- `brain_method`: fires only where `complex_method` and `large_method` already charge;
  dropping it left defect AUC unchanged on the 66-repo corpus.
- `untested_complexity`: complexity and coverage already deduct elsewhere
  ([user guide](../layers/TEST_INTELLIGENCE.md#untested-complexity)).
- The four `apex_*` markers: every finding on 13 Salesforce repos hand-read, none
  wrong; no defect corpus covers Apex yet.

### 5.5 Worked example: the dispatch rule

`nested_complexity` adds a level per `if` / `for` / `while` / `try` / `switch`. A
function mostly one dispatch on one value (a `switch`, `match` or same-subject `if` chain
with ≥ 60% of its decision points) is judged by the code around it plus its heaviest arm:
`nested_complexity` skips the `switch` and `case` levels, and `complex_method`,
`brain_method` and `repeated_nesting` read the outer CCN plus the worst arm's.

<details>
<summary>TypeScript and JavaScript ternaries</summary>

A ternary opens no level as an arm of a chain (`a ? x : b ? y : z`), inside JSX markup, or
with a JSX branch: one decision per arm (still +CCN and a flat +1 cognitive, like
`else if`). Still nesting: a ternary in another's `consequence`, in a call argument or
object literal inside JSX (`{fmt(a ? b : c)}`), with a non-JSX branch
(`cond ? c2 && <A/> : x`), and every `if` / `for` / `try` in a JSX handler.

</details>

### 5.6 CCN counting rules

Every function starts at **CCN 1** (the entry path). The AST walker (`complexity/cyclomatic.py` and the per-language node maps in `complexity/languages.py`) increments CCN on the following constructs and leaves others at zero, which is why `max_ccn` and the CCN quoted in findings can differ from a token-based counter such as `lizard`:

- **Branches and loops (+1 each):** `if`, `elif` / `else if`, ternaries and Python conditional expressions (`x if c else y`), statement-level loops (`for`, `while`, `do`), and inline guards (`if_clause`, `guard`, `where_clause`).
- **Boolean operators (+1 each):** `&&`, `||`, `and`, and `or` (`_is_boolean_operator` in `complexity/cyclomatic.py`), whether in a branch condition or a value expression.
- **Comprehension `for` (+0) vs. comprehension `if` (+1):** a comprehension `for` (`for_in_clause` in `[x for x in xs]`) is a generator, not a decision, so it adds no point; each `if` filter (`if_clause` in `[x for x in xs if x]`) is a branch point and adds +1.
- **`try` and `finally` (+0) vs. `catch` / `except` (+1 each):** opening a `try` block or a `finally` cleanup block adds no branch point or nesting level; each `catch` / `except` / `rescue` handler adds +1.
- **Flat `switch` / `when` / `match` (+1 total) vs. Python `match` (+1 per `case`):** when every arm of a `switch`, `when`, or Rust/Ruby/PHP `match` is a single expression with no nested branch, loop, switch, or multi-statement block (`_is_flat_match` in `complexity/cyclomatic.py`), the whole dispatch is one decision and adds +1 in total instead of +1 per arm. When any arm contains complex control flow, each `case` arm adds +1. Python's `match_statement` is excluded from `_is_flat_match` because structural pattern matching is equivalent to an `if` / `elif` chain, so each `case` adds +1.
- **Nullish coalescing `??` (+0):** `??` is not in the boolean-operator set and adds no point.
- **Nested functions vs. lambdas:** a nested named function or method (`function_kinds`) gets its own `FunctionComplexity` entry and does not add to the enclosing function's CCN; a lambda or arrow function (`lambda_kinds`) rolls up into the enclosing function.

| Construct | Repowise CCN | Token counter (`lizard`) | Why |
|---|---:|---:|---|
| Python `if a and b or c:` | 4 | 4 | Entry (1) + `if` (1) + `and` (1) + `or` (1) |
| Python `x = a and b; return x or a` | 3 | 3 | Entry (1) + `and` (1) + `or` (1) |
| Python `[x for x in xs]` | 1 | 2 | Comprehension `for` is a generator, not a decision |
| Python `[x for x in xs if x]` | 2 | 3 | Generator `for` (+0); `if` filter (+1) |
| Python `try: ... finally: ...` | 1 | 2 | Neither `try` nor `finally` is a decision point |
| Python `try` + 2 `except` clauses | 3 | 3 | Entry (1) + +1 per `except` handler |
| Python `match` with 3 `case` arms | 4 | 4 | Pattern matching counts +1 per `case` |
| TypeScript flat `switch` (2 `case` + `default`) | 2 | 3 | Flat dispatch counts +1 once for the `switch` |
| TypeScript `a ?? b` | 1 | 3 | `??` is not counted as a branch or boolean operator |
| TypeScript `try / catch / finally` | 2 | 2 | Entry (1) + `catch` (1); `try` and `finally` add 0 |

---

## 6. Scoring (`scoring.py`)

Every file starts at **10.0** and is clamped to `[1.0, 10.0]`. A finding deducts its
severity (`low=0.3, medium=0.7, high=1.2, critical=2.0`) times its weight (§6.1);
`score_file()` sums per category and settles each total (§5); each finding is then scaled
by settled/raw, so the parts still sum to the category and `HealthFinding.health_impact`
(via `attach_impacts()`, defect pillar only, the dashboard's "−2.0") stays honest.
`test_scoring_snapshot.py` locks caps, severity deductions, the marker-to-category map
and two fixture scores, so a retune updates it in the same PR.

### 6.1 Calibrated weight multipliers

`scoring._BIOMARKER_WEIGHT_MULTIPLIER` (values in [the full roster](#the-full-roster)) is
**calibrated offline, not hand-tuned**: files scored at T0, an L2-logistic regression with
NLOC as a control fits each marker's lift beyond size, and only the constants ship
([benchmark report](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/BENCHMARK_REPORT.md)).

- **Strongest:** `co_change_scatter` (strongest), `change_entropy` (second),
  `ownership_risk`, `nested_complexity` (strongest structural).
- **Prior weights kept:** `untested_hotspot`, `churn_risk`, `code_age_volatility`, which
  are coverage-dependent or rare, so the corpus could not fairly measure them.
- **Floored at 0.5:** `developer_congestion`, `dry_violation`, `low_cohesion`,
  `primitive_obsession`, `repeated_nesting` fire widely but proved weak under
  leakage-free scoring; `knowledge_loss` is de-rated to 0.4, the weakest.
  `error_handling` is not fitted.

Maintainability uses an independent expert-set table (floored smells at full weight) and
performance its own bounded category; the three share one kernel and never feed each
other. The headline stays the defect score: `test_scoring_dimensions` locks
`score_file(...)["defect"]` byte-for-byte to the pre-split score, and blending pillars
would need a written rationale and a recalibration corpus.

### 6.2 Validation and calibration

Headline results (jEdit 4.0 / 4.1 held out with no git history, structural markers only): [layer page](../layers/CODE_HEALTH.md#accuracy-and-limits) and
[BENCHMARKS.md](../BENCHMARKS.md#code-health-predicts-defects).

- **Protocol.** Files scored at a commit T0 before the bug window, in a detached worktree
  whose history stops at T0; labels are the next six months of fix commits;
  L2-regularized logistic regression with an NLOC control and leave-one-repo-out
  cross-validation.
- **Leakage both ways.** HEAD scoring lets fix commits manufacture the churn and
  congestion process markers "detect"; naive T0 scoring silences recency windows, so they
  anchor to the worktree's own HEAD (`as_of`). `developer_congestion` shipped at 1.5
  under HEAD scoring and came back at coefficient -0.08, hence its floor.
- **Rejected.** PageRank, code naturalness, change bursts and error-handling density were
  equivalent to null by bootstrap TOST. Size-relative scoring (−0.07 to −0.12 AUC),
  function-level prediction (Popt at or below random) and SZZ labels (lower out-of-fold
  AUC) were refuted.
- **Corpora.** 21 repositories, 9 languages, 2,826 files (379 defect-bearing); the
  commercial-tool comparison and band separation (16.9x raw, 2.18x size-normalized) use
  the 2,770 both tools scored. Intervals: two-stage repo-cluster bootstrap (2,000 seeded
  replicates).
- **More results.** +0.100 AUC over recent churn (DeLong p = 5e-10) and +0.117 over prior
  defects (p = 3e-15); per repo 0.55 (axios) to 0.86 (zod). LOC alone: delta -0.001
  (p = 0.92); Popt +0.134 (95% CI +0.080 to +0.198). NLOC quartiles 0.525 / 0.572 /
  0.593 / 0.718, with a positive control recovered at power 0.998 to 1.000, so the
  collapse is the score's. A prior-defects baseline beats it on Popt by 0.085 (95% CI
  0.035 to 0.141).
- **Paired vs the commercial tool:** Popt 0.607 vs 0.462, size-normalized density 2.18x
  vs 0.56x (both p = 0.003); AUC 0.731 vs 0.705 (p = 0.054); raw density 16.9x vs 14.2x
  (p = 0.65); precision 0.580 vs 0.636 (p = 0.64); partial rho beyond size -0.148 vs
  -0.137. It flags 27 files at its most severe level where Repowise flags 132 At risk (a
  more conservative point); its published -0.58 correlation with issue-resolution time
  (proprietary Jira data) was -0.09 (95% CI -0.19 to +0.14) on open GitHub data, and six
  queue-independent effort signals were flat.
- **In-product check** (`defect_accuracy.py`): an association, since `prior_defect` is an
  input. Data:
  [repowise-bench/health-defect](https://github.com/repowise-dev/repowise-bench/tree/master/health-defect)
  ([COMPARISON_REPORT.md](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/COMPARISON_REPORT.md)).

### 6.3 Fix first ranking (`fix_first/`, `worth.py`)

`fix_first/build.py` builds Fix first once from stored rows (index and update save it and
Do next); `get_health`, `repowise health`, the dashboard and the generated CLAUDE.md
render it unchanged. Each excluded unit is counted in `totals.excluded` under one reason,
decided with the performance queue's in `analysis/health/queue/`:

| Reason | What it leaves out |
|---|---|
| `test`, `tooling`, `generated`, `vendored`, `docs_example` | Path roles (notes below) |
| `unknown`, `expected`, `no_strategy`, `no_plan` | Performance causes: no classified context, expected repetition, no strategy, no safe plan ([PERFORMANCE.md](../layers/PERFORMANCE.md)) |
| `below_min_worth` | Under `MIN_WORTH` (0.5) recovered, or no steps |
| `unmeasured_cost`, `cold_role`, `background_unproven`, `unconfirmed_cost` | Dropped by the default queue, or not confirmed by `perf_confident_verdict` (registry or cold-path growth only, or no role and no reaching entry point) |
| `history_only` | Only git-history findings |
| `deprecated` | Deprecated functions |
| `gated_off` | Dormant: switched off by a same-file constant-false flag (`complexity/gating.py`) |
| `unreachable` | An open dead-code finding (confidence 0.8+ or safe to delete) covers the file, the symbol's lines, or its name |
| `inherent_dispatch` | One dispatch holds ≥ 60% of decision points (8 of 9 labelled rows rejected), unless a duplicate sits in it |
| `small_function` | Under 30 code lines and CCN 15 (on 67 labelled rows: drops 13 rejected, 4 accepted) |
| `no_concrete_step` | No first edit with a file and line or a named group |
| `low_value_kind` | Low cohesion (0 of 46 accepted), long method (0 of 10), long parameter lists (0 of 2) |
| `kind_unaudited` | Extract Class (0 of 14) or Move Method (0 of 34) plans, until a per-language audit passes |
| `terminal_flow` | ≥ 25% of calls print or prompt (`complexity/terminal.py`): status reports, checklists, wizards |
| `moderate_quiet` | Under CCN 80 and 200 lines, outside the top fifth by churn (or changed once), no `prior_defect` in the file, no `function_hotspot` on it |

<details>
<summary>Path-role and dormant-function details</summary>

- Tooling includes Dart's `tool/` scripts and build files by type anywhere: Gradle,
  CMake, Makefiles, Bazel, MSBuild props/targets, root `setup.py`, `noxfile.py`,
  crate-root `build.rs`, bundler configs.
- Docs means a root `docs/` or `docs_src/` tree, or one beside a docs-site config; a
  product's `app/docs/` route is production.
- Test helpers named for a module's tests (`bot.test-harness.ts`, `card-test-helpers.ts`,
  `test-support.mjs`) are test support and `*.bundle.js` is generated; worst-file lists
  say how many they left out and why.
- Dormant: the body opens with `if not FLAG: return ...` or is one `if FLAG:` block, and
  `FLAG` is a module-level `False`, `0` or `None` (`const FLAG = false` in TS/JS) the file
  never reassigns. Only the file is read: an imported flag, or one flipped by `setattr` or
  a monkeypatch, still reads as off. `totals.dormant` counts distinct functions; for
  performance it is state `expected`, reason `gated_off`.

</details>

#### Order

1. **Value**: the larger of health recovered and size (CCN 20/40/80/150, lines
   100/200/400/800, nesting 5/6/8, a critical or brain-method finding), one step more on
   a hot file (a function under 5% of its file's lines is credited that share of the
   importers). A performance fix's value is its cost; production, entry-reachable,
   data-growing DB or network calls are the top band.
2. **Tier** `now` / `next` / `later`; at most `HEAD_PER_KIND` (3) of the first `HEAD` (5)
   per kind.
3. **Inside a band**: complexity removed × reach (a 4,000-line loop outranks a long print
   routine) × `1 + ln(1 + n)` for the `n` commits that changed the lead function
   (`git_function_blame`); with no function history, a file too large to blame, no lead
   function, or a performance cause, a top-fifth-churn file weighs 1.25.

`worth.py` marks `later`: under CCN 40, 200 lines and nesting 6 (nesting under 8 counts
only from 100 lines) and not both CCN 25 and nesting 5; a dominant dispatch; nesting that
is one else-if or ternary chain; long but CCN under 20 and nesting under 5; a single
complex condition or error site. CCN 80, 400 lines or nesting 8 is worth doing
regardless; the hot-file bonus orders but never lifts out of `later`. Items carry up to
`MAX_TESTS` (5) tests. The findings list uses the same rule (`lower_priority` on the
rest; class-design and history-only findings, and performance unless production code
runs it over growing data).

#### Fix first steps

- A "Break up" reason says what splitting buys: smaller, easier parts, and less
  duplication when part is a copy. "Due" means `now` or `next`.
- A finding whose first step borrows a plan that still leaves a function above the bar
  keeps its tier, and the step says so.
- An Extract Method step finds its lines by their start and end text (line numbers move
  with the first edit); if that text repeats in the file, it says to stay inside the
  function.
- An Extract Method step carries the plan's `notes`. A stage that takes the shared parameter
  object shows the object's declaration above its helper and the line building it above
  its call. A plan missing either is not offered as a Fix first step, since its call would
  name a variable that does not exist.

### 6.4 Performance opportunities (`perf/`)

User guide (states, default queue, exclusions): [PERFORMANCE.md](../layers/PERFORMANCE.md).

- **Sinks.** `io_in_loop` resolves a loop call through the graph's resolver against the
  I/O lexicon (`io_boundaries.py`: db, network, filesystem, subprocess, lock): real
  round-trips only, never a query-builder chain. Non-local sinks are found up to three
  hops back (`crossfn.py`) with the `caller -> ... -> sink` path.
- **Gates.** Two markers fire only at or above the 80th percentile of distinct direct
  callers, never under two (how widely called, not request reachability).
  `blocking_sync_in_async` skips tests, tooling, examples, generated and vendored code.
- **Grouping.** One opportunity per intervention site per cost family and boundary
  (`<file>::__module__` for top-level code); members are sinks, call sites and
  same-family co-signals (`io_in_loop` with `nested_loop_with_io`), keyed by the sink's
  line. All-cold groups (`perf/cold_paths.py`) are `expected`/`cold_path` with or without a strategy; `gated_off`
  wins. `execution_context` stays path-only.
- **Queue.** Ranked by `rank_score`, exclusions counted in `default_queue`. Fix first, Do
  next and the lead take only queued `plan_ready` causes
  `queue.eligibility.perf_confident_verdict` confirms (`growth_source = loop`); advisory
  causes measured 0 true of 14 on three repositories.
- **Plans.** Proven means the transformation: awaits against a DB or network client are
  advisory with a `bounded_concurrency` prerequisite; a chunked loop is
  `loop_already_chunked`; `io_in_loop` and `serial_await_in_loop` on one call name each
  other in `siblings`. Edits are `mechanical` only when proven and name the call they
  repeat (`details.sink_call`, or the entered helper). `buffer_string_accumulation` and
  `replace_membership_collection` take one step per loop and variable (`details.subject`),
  which effort counts. Tests come from coverage, call graph, import graph, then
  `via: "name-match"`; a `performance_fix` plan links only by exact `opportunity_id`.
- **Dataflow promotion.** `serial_await_in_loop` and `nested_loop_quadratic` get
  `dataflow_verified: true` when a def/use pass proves iterations independent (wording,
  not score).
- **ORM lazy loads.** `lazy_load_in_loop` needs rows resolving to one model via the
  cross-file model index and a lazy relation the query does not eager-load. Its weight
  comes from `details.orm` (`_PERFORMANCE_ORM_WEIGHT`): Django 0.7 (29/32, 90.6% held
  out), SQLAlchemy 0.4 (under 30 labels).
- **Dialects.** `perf/dialects/` has 12 dialects over 19 language tags (TS/JS also cover
  Vue, Svelte and Astro scripts; C# covers Razor); C shares the C++ grammar but has no
  dialect, so it emits nothing.
- **Soundness.** No call edge for dynamic dispatch, monkeypatching or callbacks-as-values;
  ORM lazy loads are seen only where rows can be typed;
  chains beyond three hops are not followed; an unmodelled library has no sinks.
  Benchmarks: [repowise-bench/perf-detection](https://github.com/repowise-dev/repowise-bench/tree/master/perf-detection).

---

## 7. KPIs

`compute_kpis()` (§4) writes Hotspot Health, Average Health and Worst Performer into
`HealthSnapshot` rows; they feed the status line, `get_overview()` and the dashboard cards.

---

## 8. Trends (`trends.py`)

State-free over an oldest-first snapshot list; alerts run on each metric in
`_ALERT_METRICS` (hotspot health, the composite headline, maintainability) a snapshot
recorded.

- **Declining Health:** ≥ `DECLINE_THRESHOLD` (0.5) below the snapshot `DECLINE_LOOKBACK`
  (5) back, so from the 6th snapshot.
- **Predicted Decline:** three snapshots in a row each strictly lower; direction, not
  magnitude.
- **`history_drag`:** either, as a third `kind`, when the headline fell with `driver`
  `history` and the structure half held: nothing in the code to act on, so not error red.
  Maintainability never softens.

`recent_kpis(history, limit=10)` is the newest-first view for the CLI and
`get_health(include=["trend"])`.

### Per-file trajectory

- `file_score_series(history, path)` reads `per_file_scores_json`: oldest-first
  `FileTrendPoint`s, `[]` below two points. The PR bot reuses it for its sparkline.
- `file_trend(history, path)` adds `current` / `previous` / `delta` and `declining`
  (threshold below the lookback point, or `PREDICTED_DECLINE_CONSECUTIVE` drops);
  `snapshot_count` tells a young repo from a file missing in older snapshots. Served via
  `_file_trend_to_dict` in the file block, the breakdown and the trend route (§13).

#### Below the floor

Files 12.9 and 9.1 points deep both store `1.0` (`[SCORE_FLOOR, SCORE_MAX]`).
`per_file_deductions_json` keeps the pre-clamp deduction for floored files only, and each
point's `unclamped_score` (`SCORE_MAX - deduction`, else `score`) feeds
`_file_declining`, so a file worsening *below* the floor trips `declining`. Snapshots
without the map stay flat: the depth was never measured.

### Per-file signals (`signals.py`)

`file_signals(git_meta, degrees)` joins one `GitMetadata` row and the graph degree into
`FileSignals`, no recompute: **Process** (`prior_defect_count`, `change_entropy_pct` as
0-100, 90-day line churn, `age_days`), **People** (recent vs all-time owner and share),
**Topology** (`in_degree` / `out_degree`). A field is `None` only when its *source row*
is absent (never imputed; a real `0` stays). Served via `_file_signals_to_dict` (§13),
null-dropped in `get_context` (§12), typed as `FileSignals` in
`@repowise-dev/types/health`, drawn by `file-signals-panel.tsx`.

---

## 9. Incremental analysis: the `repowise update` path

With `changed_files`, duplication still runs repo-wide (a clone partner may be
unchanged), other files are skipped, and KPIs are **not** recomputed (the dashboard
derives them from merged rows). `update_cmd/` takes the set from
`change_detector.get_changed_files()` and **upserts** (`upsert_health_metrics`,
`upsert_health_findings`); full init deletes and inserts (`save_health_*`).

---

## 10. Persistence schema

Four tables in `.repowise/wiki.db`, keyed to `repositories.id` with `ON DELETE CASCADE`.

### `health_findings`

One row per hit; lifecycle `open → acknowledged | resolved | false_positive` (as Dead
Code). Columns: `id` (UUID), `repository_id`, `file_path` (indexed), `biomarker_type`,
`severity`, nullable `function_name` / `line_start` / `line_end`, `details_json`,
`health_impact`, `reason`, `status`, `created_at`, `updated_at`. Open rows are rewritten
on init and per changed file on update.

**Identity** (`finding_identity.py`): `finding_<digest>` from the enclosing function or
class plus the line offset (absolute lines at file level), so edits above keep it; only
detail keys that tell findings apart (a coupling partner, an error kind) count, never
metric values. A re-detected triaged row gets fresh evidence: `acknowledged` and
`false_positive` stay, `resolved` reopens.

### `health_file_metrics`

One row per `(repository_id, file_path)`: `score`; nullable `defect_score`,
`maintainability_score`, `performance_score`; `structure_deduction` and
`history_deduction` (null on rows before the split, reported unscored under
`counts=code_shape`, never a ten); `is_test` (so `scope` is one row filter); `max_ccn`,
`max_nesting`, `nloc`; nullable `duplication_pct`; `has_test_file`; nullable
`line_coverage_pct`, `branch_coverage_pct`; `module` (community label, else top-level
dir); `updated_at`.

### `health_snapshots` and `coverage_files`

Snapshots keep the latest 50 per repo (`HEALTH_SNAPSHOT_RETENTION`,
`crud/analysis/health/snapshots.py`). `coverage_files` is overwritten per `coverage add`;
its `covered_lines_json` lets `coverage_gap` name the exact uncovered surface.

---

## 11. CLI surface

`commands/health_cmd/` mirrors the dead-code command's Click structure; every flag is in
[CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-health-path).

```bash
repowise health --file path/to/x.py        # one file; --module, --refactoring-targets, --trend
repowise coverage add coverage.xml --format cobertura
repowise status                            # Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: ...)
```

`repowise update` silently re-scores changed files only.

---

## 12. MCP surface

### `get_health(targets?, include?, repo?, limit?)`

`tool_health/tool.py` dispatches per mode and block.

- **Dashboard** (`targets=None`): KPIs with `band`, `distribution`, `worst_files`,
  `top_findings`, `modules`.
- **Targeted**: full `metrics` + `findings` and a `trends` block for targets with two or
  more snapshots; `module:foo` expands to that module.
- `include` adds blocks and picks the ranked-list dimensions (`defect`,
  `maintainability`, `advisory`; default defect + maintainability). Flags:
  [MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_health);
  `request.py::_KNOWN_INCLUDES` is the authority (add one in `request.py`, `loading.py`,
  `blocks.py`).

### Enrichments on existing tools

`get_risk(targets)`: `health_score`, `top_biomarkers`, `line_coverage_pct`,
`branch_coverage_pct`. `get_context(include=["health"])`: `score`, `max_ccn`,
`max_nesting`, `nloc`, `module`, `duplication_pct`, top 2 markers with a `suggestion`,
coverage and `signals`. `get_overview()`: `code_health` with avg, `band`, hotspot, worst,
open finding count and files per band. All carry `_meta` via `build_meta()`.

---

## 13. REST surface

`server/.../routers/code_health/`: one module per surface sharing `scope.py`, `counts.py`,
`file_filters.py`, `statuses.py`; routes under `/api/repos/{repo_id}/health/`, auth by
`verify_api_key` (`server/deps.py`) at the router.

| Route | Returns |
|---|---|
| `overview` | summary with `band`, `distribution`, worst files, top findings, modules |
| `badge.svg`, `badge.json` | flat SVG (`N.N/10`, no letter); Shields.io payload (`schemaVersion`/`label`/`message`/`color`/`band`) |
| `files`, `files/breakdown`, `files/trend` | per-file metrics; one file's breakdown, suggestions, `trend`, `signals`; one file's series (`?file_path=`) |
| `trend` | KPI history, alerts, last-two-snapshot per-file deltas |
| `findings` | filter by `biomarker_type`, `file_path`, `dimension`, `status`, `severity` or `min_severity`; performance and advisory only when named or `include_zero_impact=true` |
| `coverage` (GET, POST) | summary and rows; ingest a report |
| `refactoring-targets` | queue by `total_impact / effort_bucket` (open findings only); findings filters plus `search`, `module`, `only_hotspots` / `only_untested` / `only_failing`, `limit` + `offset`, `total` and `finding_total` |
| `churn-complexity`, `modules` | `commit_count_90d`, `max_ccn`, `nloc`, `score`, `churn_percentile` per churned file; NLOC-weighted rollup |
| Also | `fix-first` (and `/{fix_id}`), `map`, `impact-effort`, `related-work`, `tests-reaching`, `performance-opportunities` (and `/{opportunity_id}`, `/findings`), `coverage/patch`, `findings/{id}` |

`ScopeQuery` / `CountsQuery` parse `scope` and `counts` wherever they change a figure and
echo them; an unknown value falls back to the default. `counts=code_shape` projects from
`structure_deduction`, drops history-derived findings, recomputes totals and rankings,
counts split-less rows in `unscored_files`, and writes nothing.

---

## 14. Web dashboard

`packages/web/src/app/repos/[id]/code-health/page.tsx`: a map whose lens shows health,
maintainability, performance or churn (paired with `ChurnComplexityQuadrant` from
`churn-complexity`), `scope` and `counts` controls, and four tabs by `?tab=` (`tabs.ts`): Map
(default, leads with Fix first; `triage`, `heatmap`, `hotspots`, `modules`, `trend` alias
to it), Findings, Performance, Dead code. Tests and Security are pages, Doc drift a Docs
view and blast radius the Commits page's "Review a change"; old tab ids (`coverage`,
`security`, `doc-drift`, `impact`) and `/repos/[id]/health/*` redirect. Sub-pages:
`coverage`, `refactoring-targets`, `trend`. The file Health tab has the "Functions by
churn" table. Primitives in `packages/ui/src/health/` let the hosted `frontend/` repo
reuse them.

---

## 15. CLAUDE.md integration

The generated `CLAUDE.md` gets a short `## Code health` section
(`core/generation/editor_files/data.py`, `templates/_editor_block.j2`) keeping only:
score ≤ 5.0 on a hotspot, a Brain Method with > 10 dependents, any Untested Hotspot, DRY
violations > 70 % similar, a > 1.0 drop over 5 snapshots.

---

## 16. Configuration: `.repowise/health-rules.json`

User-authored (the layer's **only** JSON file), loaded by `HealthConfig.load`; globs use
`path` (aliases `path_glob`, `glob`) over repo-relative POSIX paths, every match applies
and disabled sets union; `to_analyzer_config()` feeds `FileEvaluator.evaluate()`. Schema:
[user guide](../layers/CODE_HEALTH.md#configuration).

---

## 17. Performance

Budget **< 30 s on a 3,000-file synthetic repo** (`test_health_perf_benchmark.py`,
`-m slow` or `make health-bench`). `analyze_async()` uses `asyncio.gather` +
`asyncio.to_thread`. Duplication is O(total_tokens); scoring is O(findings); the walker
re-parses (~1 ms per file) because `ParsedFile` keeps no tree-sitter `Tree`, which a
shared parse cache would remove.

---

## 18. Testing

`make health-check` runs `tests/unit/health/`, `tests/unit/server/test_mcp.py` and a
health report. Key suites: `test_complexity_walker.py` (per-language CCN, nesting,
cognitive); `test_<biomarker>.py` with `test_biomarkers.py`, `test_structural_biomarkers.py`,
`test_coverage_biomarkers.py`, `test_organizational_biomarkers.py`,
`test_dry_violation.py` (two languages positive, one negative); `test_duplication.py`;
`test_coverage_parsers.py`; `test_scoring.py`; `test_scoring_snapshot.py` (the
**stability guard**); `test_scoring_dimensions.py`; `test_trends.py`; `test_signals.py`
(no-signal vs real zero, entropy 0-1 to 0-100, owner handoff); `test_churn_complexity.py`
(no-churn omission, danger-product sort, percentile scaling); `test_suggestions.py`;
`test_health_config.py`. Integration: `test_health_coverage_integration.py` (LCOV ->
`coverage_gap`) and the perf benchmark.

---

## 19. Extension points

- **Marker:**
  1. `biomarkers/<id>.py` implementing `Biomarker`.
  2. Append to `_DETECTOR_FACTORIES` (`registry.py`).
  3. List in `scoring._BIOMARKER_DIMENSIONS` (unlisted defaults into defect).
  4. Add to its tables: `_BIOMARKER_CATEGORY` / `_BIOMARKER_WEIGHT_MULTIPLIER`, `_MAINTAINABILITY_*`, `_PERFORMANCE_WEIGHT_MULTIPLIER`, or `_ADVISORY_HOME`.
  5. A label in `BIOMARKER_LABELS` and a template in `suggestions._TEMPLATES`.
  6. Three tests: two languages positive, one negative.
  7. A row in [the full roster](#the-full-roster) and `biomarkers/README.md`.
- **Walker language:** one `LanguageNodeMap` in `complexity/languages.py` mapping node
  types to `BRANCH` / `LOOP` / `TRY` / `BOOLEAN_OP`, plus a fixture in
  `tests/fixtures/lang_samples/<lang>/`; no `.scm` files.
- **Coverage format:** a stdlib-only parser in `coverage/` returning a `CoverageReport`,
  routed from `coverage/detector.parse`.
- **Rules key:** extend `HealthConfig` through `to_analyzer_config()` to
  `FileEvaluator.evaluate()`.
- **Retune:** `scoring.CATEGORY_CAPS` / `_SEVERITY_DEDUCTION` (update the snapshot);
  alerts in `trends.DECLINE_THRESHOLD` / `DECLINE_LOOKBACK`; a new entry point calls
  `HealthAnalyzer.analyze()` and upserts when incremental.

---

## 20. Where the layer deliberately stops

- **No LLM in scoring or suggestions**: `suggestions.py` is templates; code generation
  only behind `--generate-code` and the opt-in `generate_refactoring_code` tool.
- **No symbol-level score**: files are the refactor unit; symbol CCN feeds it via
  `all_functions`.
- **No `complexity_estimate` backfill** until a re-index.
- **No predictive ML on trends**; commit-level risk is `analysis/change_risk/`
  (`repowise risk`, a calibrated logistic model).
- **No letter grade**: the 1–10 score and the five bands (`grading.py`) are the only
  scales, and every band word or colour reads that vocabulary.

---

## See also

- [`packages/core/src/repowise/core/analysis/health/README.md`](../../packages/core/src/repowise/core/analysis/health/README.md) and the `complexity/`, `coverage/`, `duplication/`, `biomarkers/` READMEs.
- [`docs/architecture/graph-algorithms.md`](graph-algorithms.md): the graph layer health depends on.
