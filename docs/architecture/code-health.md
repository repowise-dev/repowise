# Code Health: Architecture & Internals

Companion to the user-facing [`docs/layers/CODE_HEALTH.md`](../layers/CODE_HEALTH.md). This
document is for contributors: where every piece lives, how data flows from
parsed source to the dashboard, and the extension points for adding
markers, languages, coverage formats, or alerts.

> **TL;DR.** Health analysis is a deterministic, zero-LLM Python pipeline:
> tree-sitter walks every file once -> markers vote -> scores aggregate per
> category -> results land in four SQLAlchemy tables. The MCP server, CLI,
> and Next.js dashboard all read from those tables: no JSON cache, no
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
                  health, status,           get_health, get_risk,                   /repos/[id]/code-health
                  health --trend            get_context, get_overview              (tabs, ?tab=...)
```

Three architectural rules govern the whole layer:

1. **Zero LLM.** Every marker is AST, git, or coverage math.
2. **No JSON caches.** SQLite is the single source of truth; everything
   else reads from it.
3. **No new runtime dependencies.** Pure Python over tree-sitter (already
   in tree). No lizard, no jscpd, no Node.

---

## 2. Where things live

### Python: `packages/core/src/repowise/core/`

```
analysis/health/
├── README.md                       # developer overview (this layer)
├── __init__.py                     # public API: HealthAnalyzer, HealthReport
├── engine.py                       # orchestrator: walker → biomarkers → scorer
├── scoring.py                      # weighted aggregation, category caps, KPIs
├── ranking.py                      # canonical worst-first key + deduction fold
├── aggregation.py                  # module rollups, severity/biomarker/score breakdowns
├── grading.py                      # the five absolute bands + NLOC-weighted distribution
├── defect_accuracy.py              # "does the score find the bugs?" self-validation
├── trends.py                       # snapshot diff, Declining/Predicted alerts, per-file score series
├── signals.py                      # per-file process/people/topology join (surfacing-only)
├── churn_complexity.py             # churn × complexity scatter points (surfacing-only)
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
│   ├── jacoco.py                   # JaCoCo XML parser
│   ├── goprofile.py                # Go coverprofile parser (line-based)
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
├── orchestrator.py                 # _run_health_analysis(): builds community_label_map, runs analyzer
└── persist.py                      # persist_pipeline_result(): writes findings/metrics/snapshot
```

### CLI

```
cli/src/repowise/cli/commands/
├── health_cmd.py                   # repowise health [--trend|--refactoring-targets|--module]
├── status_cmd.py                   # `Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: ...)`
└── update_cmd.py                   # incremental path: HealthAnalyzer.analyze(changed_files=...)
```

### Server: MCP + REST

```
server/src/repowise/server/
├── mcp_server/
│   ├── tool_health/                # @mcp.tool get_health(targets, include, repo, limit)
│   ├── tool_risk.py                # enriched: health_score, top_biomarkers, line_coverage_pct
│   ├── tool_context.py             # include=["health"]: score, top 2 biomarkers, suggestion
│   └── tool_overview.py            # code_health block with KPIs
└── routers/
    └── code_health.py              # /api/repos/{id}/health/{overview,files,coverage,
                                    # refactoring-targets,modules,findings}
```

### Web dashboard

```
packages/ui/src/health/             # shared React components (used by web + future hosted frontend)
├── file-table.tsx
├── biomarker-list.tsx
├── coverage-bar.tsx
├── module-coverage-list.tsx
├── untested-hotspot-warning.tsx
├── refactoring-card.tsx
├── refactoring-target-list.tsx
├── health-badge.tsx               # score pill, colored by the health band
├── health-distribution-bar.tsx    # NLOC-weighted split across the five bands
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
├── test_coverage_parsers.py        # LCOV/Cobertura/Clover/JaCoCo/Go/JSON
├── test_scoring.py                 # category caps, clamping
├── test_scoring_snapshot.py        # stability snapshot: locks caps + deductions
├── test_health_config.py           # .repowise/health-rules.json
├── test_trends.py                  # diff_snapshots, declining/predicted alerts
├── test_signals.py                 # file_signals join + no-signal/normalization
├── test_churn_complexity.py        # churn × complexity point shaping + sort + filtering
└── test_suggestions.py

tests/integration/
├── test_health_coverage_integration.py
└── test_health_perf_benchmark.py   # 30 s budget on 3,000-file synthetic repo (slow)
```

---

## 3. The pipeline (init path)

`repowise init` runs `run_pipeline()` in `core/pipeline/orchestrator.py`.
`_run_health_analysis()` is a phase in that orchestrator, called between
`_run_dead_code_analysis()` and `_run_decision_extraction()`. It does four
things:

1. Builds a `{file_path: community label}` map from the graph's community
   detection so `HealthFileMetric.module` is populated (module rollups are
   never NULL).
2. Loads per-file override rules from `.repowise/health-rules.json` via
   `HealthConfig.load(repo_path)` (a no-op when the file is absent) and
   resolves them to per-file disabled sets with `to_analyzer_config()`.
3. Constructs the `HealthAnalyzer` with everything it needs: the NetworkX
   graph (for dependents), `git_meta_map` (hotspot bit, owners, co-change, bus
   factor), the `parsed_files` from the AST phase, and the module map.
4. Picks the sync or parallel path by repo size. tree-sitter releases the GIL
   during parsing, so on repos with `>= 500` parsed files `analyze_async()`
   (asyncio gather over worker threads) gives a real wall-clock speedup;
   smaller repos run `analyze()` on a single thread.

The returned report rides on `PipelineResult.health_report`. Then
`core/pipeline/persist.py` writes it in one session: `save_health_metrics`,
`save_health_findings` (only when there are findings), and a
`save_health_snapshot` carrying the three KPIs plus a `{path: score}` map for
trend tracking (rolling 50-row window per repo), and a second
`{path: total_deduction}` map covering only the files whose score is held at
the floor. Both maps come from `trends.snapshot_file_maps`, which the other two
snapshot writers (`repowise health` and `repowise update --full`) also call; a repo
whose writers disagreed would get a history whose depth changed depending on
which command last wrote it.

---

## 4. Inside `HealthAnalyzer.analyze()`

A single pass over the parsed file list. For each file the analyzer:

1. **Walks the AST** (`_walk`): `walk_file(language, source)` returns a
   `FileComplexity` of functions and classes. Each `FunctionComplexity` carries
   name, line range, nloc, ccn, max nesting, cognitive complexity, bumps, and
   param count; each `ClassComplexity` carries method count, total nloc, the
   method list, LCOM4, max method ccn, and field count.
2. **Populates symbol complexity** (`_populate_symbol_complexity`): writes
   `max(ccn)` into `Symbol.complexity_estimate` as a side effect, so the
   `ContextAssembler` symbol ranker benefits even when a caller never touches
   the health tables.
3. **Evaluates the file** (`_evaluate_file`): builds a `FileContext` (nloc,
   `has_test_file`, module, per-function and per-class metrics, the per-file
   `git_meta`, graph in-degree as `dependents_count`, the repo-wide p80 of
   in-degree used as the `brain_method` floor, coverage fields when ingested,
   and the file's clone slice), runs `detect_all()`, scores with `score_file()`,
   and attaches per-finding impacts.

After the loop, `compute_kpis()` runs over the metrics and the set of hotspot
paths (`git_meta_map[path]["is_hotspot"]`), and the analyzer returns a
`HealthReport(findings, metrics, kpis)`.

Duplication runs **once up-front** (it is cross-file by nature); each
`FileContext` gets a slice of the global clone report. The `dry_violation`
marker reads `ctx.clones` and ranks pairs by co-change frequency from
`git_meta_map[path]["co_change_partners_json"]`, so active clones rank higher
than dormant ones.

---

## 5. The markers and their categories

Each marker is a stateless class implementing the `Biomarker` Protocol from
`biomarkers/base.py`: a `name` (`"brain_method"`, `"nested_complexity"`, ...), a
`category` (see `scoring.CATEGORY_CAPS`), and a `detect(ctx: FileContext)` method
returning a list of `BiomarkerResult`s.

### The full roster

`biomarkers/registry.py` registers **53 detectors**; counting the three
governance findings written by the additive pass (`governance.py`) there
are **56 marker ids**. They divide by what each is permitted to affect:

| Group | Count | Scores into |
|---|---:|---|
| Defect-scoring | 25 | `defect` (11 of them also `maintainability`) |
| Performance | 22 | `performance` only |
| SQL | 3 | `maintainability` only |
| Governance | 3 | nothing: the finding surfaces, the score is untouched |
| Advisory | 3 | nothing: measured by construction, kept out of impact-ranked lists unless requested |

The authority is `scoring._BIOMARKER_DIMENSIONS`. Any biomarker **not** listed
there defaults into `defect`, which is why every `sql_*` and every performance
name must be listed explicitly: an omission would silently break the defect
golden guarantee (§6).

### Defect categories and caps

| Category               | Cap  | Markers |
|------------------------|------|------------|
| Organizational         | −3.5‡ | developer_congestion, knowledge_loss, function_hotspot, code_age_volatility, ownership_risk, churn_risk, change_entropy, co_change_scatter, prior_defect, ungoverned_hotspot†, stale_governance†, contradictory_decision† |
| Structural complexity  | −2.5 | brain_method, low_cohesion, god_class, nested_complexity, bumpy_road, complex_conditional |
| Test coverage          | −2.0 | untested_hotspot, coverage_gap |
| Test coverage gradient | −2.0 | coverage_gradient |
| Size & complexity      | −1.5 | complex_method, large_method, primitive_obsession |
| Duplication            | −1.0 | dry_violation |
| Test quality           | −0.5 | large_assertion_block, duplicated_assertion_block |
| Error handling         | −0.5 | error_handling |

† The three governance markers carry a category and a weight, but the pass that
writes them runs *after* scoring completes and never touches
`HealthFileMetric.score`, so in practice they never deduct. They are counted
in the table above because `scoring.py` maps them, not because they move a
number.

‡ A ceiling. The live cap is `history_cap(structure)`, `min(3.5, 1.0 + structure)`,
where `structure` is the file's capped deduction from every other defect
category, so git history alone costs a file at most 1.0.

`hidden_coupling` is advisory: still detected, stored and listed, it deducts
nothing. On its own it ranks defect-prone files near chance (AUC about 0.55), and
a pre-registered test on 12 repositories no earlier health study used found the
score without it non-inferior at predicting defects.

The maintainability dimension has its own independent tables
(`_MAINTAINABILITY_CATEGORY`, caps: structural_complexity 4.0,
size_and_complexity 2.0, duplication 2.0, error_handling 2.0, sql 2.0), and the
performance dimension a single `performance` category capped at 2.0. See §6.

`large_assertion_block` and `duplicated_assertion_block` are the two
**test-quality** smells (see §5.3). They fire only on test files and sit in
a deliberately small category so a noisy test can't dominate its own score.
`large_method` is now gated on a minimal CCN floor (≥ 2) so a long-but-flat
body (a big data literal) reads as layout, not a complexity smell: a small
step toward decoupling the score from raw file size.

`ownership_risk` (long-run minor-contributor dispersion, Bird et al.) and
`churn_risk` (size-normalized relative churn, Nagappan-Ball) are git-only
process signals computed from `top_authors_json` / `lines_added_90d` /
`churn_percentile`: fields the git indexer already produces. `change_entropy`
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
`as_of` reference (the indexed commit's committer date): so scoring a historical T0
checkout measures the fixes *before* T0, never leaking the post-T0 fixes that
form the benchmark's labels. It carries a **neutral (1.0) weight by design**:
on the calibration corpus prior-defect history is largely redundant with the
existing process signals (correlation ≈ +0.59 with `change_entropy`, +0.38 with
churn; calibrated coefficient ≈ +0.02, effort-aware Popt gain within bootstrap
noise), so it is not boosted as a predictor. It ships for its **explanatory**
value, not for a measured accuracy lift: "this file was bug-fixed N times
recently" is immediately actionable, and it uniquely flags a few files the
other signals miss.

`coverage_gradient` makes the test-coverage signal **continuous**. The two
binary coverage gates (`untested_hotspot`, `coverage_gap`) only fire below hard
thresholds (≈40–60% line coverage), so on a well-tested codebase (where most
files sit at 85–99%) the score is effectively blind to coverage even though the
uncovered fraction still carries defect signal. `coverage_gradient` deducts
health in direct proportion to that fraction: `4.0 × (1 − line_coverage_pct/100)`
health points, clamped by its category cap (binding at ≥50% uncovered). It uses
the `deduction` override on `BiomarkerResult` (a continuous magnitude that
replaces the discrete severity-to-deduction table for that finding) so it stays
**linear and per-finding attributable** (the `health_impact` contract holds). It
is **silent when no coverage report was ingested** (`line_coverage_pct is None`):
absent coverage is never imputed as uncovered. It lives in its own capped
category (`test_coverage_gradient`, −2.0) so the additive continuous signal
neither squeezes nor is squeezed by the binary gates, and it skips test files.
Calibrated offline against the defect corpus, it recovers **+0.043 corpus AUC
[95% CI +0.023, +0.061]** on the covered subset (≈65% of the continuous-feature
ceiling), Popt-neutral, and is exactly zero on repos without ingested coverage:
a purely additive improvement.

`low_cohesion` (LCOM4) and `god_class` are the two **class-level**
structural smells. They read `ctx.class_metrics`, the per-class aggregates
the walker now emits alongside per-function metrics (see §5.2).
`brain_method`'s centrality gate is **language-agnostic**: instead of a
fixed `dependents ≥ 8`, it fires when a file is in the repo's top quintile
of connected files (`repo_dependents_p80`, computed once per analyze) or
clears the absolute hub bar of 8: so it no longer goes silent on
sparse-graph languages (TS barrels, Rust) whose in-degrees are lower than
Python's.

### 5.1 Change-entropy git-layer fields

`change_entropy` is computed during the **single** FULL-tier co-change walk
(`ingestion/git_indexer/co_change.py::compute_co_changes_and_entropy`): no
extra `git log` subprocess. For each commit touching a set of tracked files
`F` (with `2 ≤ |F| ≤ 30`; wider commits are dropped as noise, Hassan's filter),
the commit's entropy is `log2(|F|)`, distributed uniformly (`1/|F|` per file)
and decayed with the same τ=180d half-life as co-change. The decay-weighted sum
per file is `git_meta["change_entropy"]`. `enrich.compute_percentiles` then
derives `change_entropy_pct` by ranking **only files with positive entropy**
(zero-entropy files, the ESSENTIAL tier or files only ever changed alone,
keep pct 0.0 so the marker stays silent). Both fields are persisted on
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
turn signal on, never produce a false-positive flood. See
`complexity/README.md` for the full heuristic and its limits.

`error_handling` is the **advisory maintainability** marker: swallowed
catches (an empty/comment-only `catch` / `except: pass` body), Python
catch-all `except:` / `except Exception:`, Rust `.unwrap()` / `.expect()` /
panic-family macros, and Go's empty `if err != nil {}` or blank-identifier
discard of a call's error. The walker collects each occurrence (with its
line) in a whole-tree pass (module-level code included) reusing the
`LanguageNodeMap` catch kinds for the seven catch-shaped languages and
dedicated recognizers for Rust/Go; an unsupported language or parse failure
yields no hits ("no signal", never a guess). The marker emits one LOW
finding per occurrence (0.15 after its floored 0.5 weight) in its own
`error_handling` category capped at −0.5, mirroring `test_quality`'s
advisory framing. It is deliberately **excluded from the defect-weight
calibration**: on the 21-repo / 9-language T0 benchmark it is AUC-neutral
(OOF delta ≈ 0, CI crosses zero) but size-orthogonal and the least redundant
signal tested, and it ships because users expect `except: pass` flagged:
bounded so it can never move a file by more than half a point.

### 5.3 Assertion-block walker metrics (test-quality)

The same single walker pass records `assertion_blocks` on each
`FunctionComplexity`: runs of ≥ 2 consecutive assertion statements, each
`(start_line, end_line, count)`. A statement counts as an assertion when it
is a bare `assert` (`LanguageNodeMap.assert_kinds`) or its expression is a
call (`assert_call_kinds`) whose callee name starts with `assert` or
`expect`: covering `assertEqual` / `assert_eq!` / `expect(...).toBe(...)`
across xUnit and BDD styles. Opt-in per language, with all nine full-tier
languages (Python, TS/JS, Java, Kotlin, Go, Rust, C++, C#) mapped;
a language that maps neither field simply emits no blocks.
`large_assertion_block` flags a single run ≥ 15; `duplicated_assertion_block`
intersects the clone report with assertion spans. Both gate on
`coverage.is_test_file(path)` so production code is never touched.

### 5.4 Advisory markers and their precision record

`advisory` is not in `DIMENSIONS`, so no weight, category or cap table can key
on it; "never deducts" is structural. A marker must appear in both
`_BIOMARKER_DIMENSIONS` and `_ADVISORY_HOME`, or it still deducts from defect.
Advisory findings carry `health_impact == 0.0`, never count toward a change's
introduced/worsened totals, never make a review verdict blocking, and stay out of
impact-ranked lists unless the dimension or marker is named.

A marker earns weight by clearing roughly 70% hand-labelled precision on a real
corpus. Precision is measured per language because it does not transfer:

- `mock_saturated_test`: 67% on Python (32 findings) and 40% on TypeScript (30),
  each the full population at the shipped gate. Both predate the assertion count
  gaining more oracle shapes, which can only suppress findings, so they are now a
  ceiling. It stays advisory until two false-positive families separate:
  `Fake*` value-object builders passed into real logic, and boundary isolation
  where the assertion reads a real artifact. Both turn on whether an assertion
  observes a double or production output, a dataflow question the pass does not
  ask.
- `assertion_free_test`: at least 71% on TypeScript (31 findings, the full
  population of two corpora; a floor, since three false-positive families closed
  after labelling) and 86% on Python (29 labelled, a systematic sample of 172). It
  does not report on Go or Java. A test that delegates its oracle to a helper is
  resolved by name in the same file and through the call graph across files. A
  mock verification counts as an oracle here, the opposite of
  `mock_saturated_test`; the tiers are in `complexity/assertions.py`.
- `hidden_coupling`: see §5 (near-chance AUC; non-inferior without it).

### 5.5 Worked example: the dispatch rule

`nested_complexity` increments depth on each `if` / `for` / `while` / `try` /
`switch` node and fires at depth 4 or more, severity scaling with depth. A function
that is mostly one dispatch on one value (a `switch`, `match` or same-subject `if`
chain holding at least 60% of its decision points) is judged by the code around the
dispatch and its heaviest arm: `nested_complexity` skips the two levels the
`switch` and its `case` open, and `complex_method`, `brain_method` and `bumpy_road`
read the CCN outside the dispatch plus the CCN of its most complex arm.

`biomarkers/registry.py` is an **explicit list**, not auto-discovery:
keeps the registration order deterministic and lets tests inject extras
via `registered_biomarkers(extra=...)`.

---

## 6. Scoring (`scoring.py`)

Every file starts at **10.0**. Each finding contributes a per-severity
deduction (`low=0.3, medium=0.7, high=1.2, critical=2.0`), scaled by the
marker's calibrated weight multiplier (§6.1). `score_file()` then groups the
weighted findings by category, sums the raw deductions per category, and either
accepts the sum or, when it exceeds the cap, scales every per-finding deduction
in that category proportionally so the total equals the cap. This keeps the
UI's "this finding cost you X points" honest after capping. The final score is
clamped to `[1.0, 10.0]`. So even ten critical structural findings can drive
structural complexity down by at most 3.5 points, not 20.

The per-finding scaled deduction lands on `HealthFinding.health_impact`
via `attach_impacts()`: that's what the dashboard's "−2.0" badge shows.

Snapshot tests in `tests/unit/health/test_scoring_snapshot.py` lock the
category caps, severity deductions, marker-to-category mapping, and two
known-fixture scores. A retune intentionally requires updating the
snapshot in the same PR.

### 6.1 Calibrated weight multipliers

`scoring._BIOMARKER_WEIGHT_MULTIPLIER` lets the strongest empirical predictors
deduct more than the uniform severity table alone allows. The multipliers are
**calibrated offline against a defect corpus, not hand-tuned**: each file is
scored at the pre-window commit (T0, no leakage) and an L2-logistic regression,
with NLOC as an explicit control, fits each marker's defect lift beyond file
size. The runtime stays deterministic; only the learned constants ship. The
full calibration, with confidence intervals, is published in the
[benchmark report](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/BENCHMARK_REPORT.md);
§6.2 describes the protocol.

| Weight | Markers | Rationale |
|---|---|---|
| 1.8 | `co_change_scatter` | Strongest calibrated predictor. |
| 1.51 | `change_entropy` | History Complexity Metric; second strongest. |
| 1.38 | `ownership_risk` | Long-run minor-contributor dispersion. |
| 1.34 | `nested_complexity` | Strongest structural predictor. |
| 1.1–1.33 | remaining structural complexity / size markers | Moderate calibrated lift. |
| 1.3 / 1.2 / 1.1 | `untested_hotspot` / `churn_risk` / `code_age_volatility` | Coverage-dependent and rarely-firing; keep prior weights the corpus could not fairly measure. |
| 1.0 | `prior_defect` | Neutral by design: largely redundant with the other process signals, kept for its explanatory value. |
| 0.5 (floored) | `developer_congestion`, `dry_violation`, `low_cohesion`, `brain_method`, `primitive_obsession`, `bumpy_road` | Fire widely but proved weak under leakage-free scoring; kept as maintainability and parity signals, not disabled. |
| 0.4 (de-rated) | `knowledge_loss` | Weakest of the floored set. |
| 0.5 (not fitted) | `error_handling` | Excluded from calibration (AUC-neutral); floored and capped at 0.5 per file. |

The same marker stream feeds the **maintainability** signal under an
independent, expert-set weight table (the floored smells deduct at full weight
there), and the **performance** signal under its own bounded `performance`
category. The three signals share one scoring kernel against separate
weight/category/cap tables and never feed back into each other; see the
[user guide](../layers/CODE_HEALTH.md#the-three-signals)
for what each signal surfaces. The headline stays the defect score: the band
cutoffs and every accuracy figure are claims about that pillar, and
`test_scoring_dimensions` locks `score_file(...)["defect"]` byte-for-byte to the
pre-split single score. Blending the pillars would need a written rationale and a
recalibration corpus.

### 6.2 Validation and calibration

**Protocol.** Every file is scored at a commit T0 that precedes the bug window, in
a detached worktree whose history stops at T0. Labels are the bug-fix commits of
the following six months. An L2-regularized logistic regression with NLOC as an
explicit control column fits each marker's lift beyond size, with
leave-one-repo-out cross-validation. Only the learned constants ship.

**Leakage runs both ways.** Scoring at HEAD lets the bug-fix commits manufacture
the churn, co-change and congestion the process markers then "detect". Scoring
naively at T0 silences every recency-windowed marker, because a worktree six
months back sees an empty "last 90 days". Recency windows are therefore anchored to
the worktree's own HEAD (`as_of`), not wall-clock time. The evidence that this
matters: `developer_congestion` shipped at 1.5 under HEAD scoring and came back at
coefficient -0.08 under T0 scoring, hence its 0.5 floor.

**What calibration rejected.** Graph centrality (PageRank), code naturalness,
change bursts and error-handling density were declared equivalent to null by
bootstrap TOST, not merely non-significant. Size-relative scoring (lifts small
files, costs 0.07 to 0.12 AUC overall), function-level prediction (Popt at or
below random effort ordering) and recalibrating on SZZ labels (lower out-of-fold
AUC than the shipped weights) were all run and refuted.

**Two corpora.** The cross-project result uses 21 repositories, 9 languages,
2,826 files (379 defect-bearing). The CodeScene comparison uses the 2,770 of those
files both tools could score. The band-separation figures (16.9x raw, 2.18x
size-normalized, At risk vs 8.0 and above) come from the paired 2,770-file set.

| Cross-project result (2,826 files) | Value |
|---|---|
| Mean ROC AUC | 0.737 (95% CI 0.683 to 0.787) |
| Held out: PROMISE jEdit 4.0 / 4.1 (no git history, structural markers only) | 0.761 / 0.776 |
| vs. recent churn | +0.100 AUC (DeLong p = 5e-10) |
| vs. prior-defect history | +0.117 AUC (DeLong p = 3e-15) |
| Per-repo range | 0.55 (axios) to 0.86 (zod) |

Intervals come from a two-stage repo-cluster bootstrap (2,000 seeded replicates,
repositories then files), since the repository is the unit of generalization.

Limits measured on the same corpus:

- LOC alone scores AUC 0.742 (delta -0.001, p = 0.92). The win is effort-aware:
  Popt +0.134 (95% CI +0.080 to +0.198).
- Within NLOC quartiles AUC is 0.525 / 0.572 / 0.593 / 0.718. A positive control
  injecting a size-orthogonal signal of the headline magnitude was recovered with
  power 0.998 to 1.000 in those same quartiles, so the collapse is a property of
  the score, not of sample size.
- A prior-defects baseline beats the score on Popt by 0.085 (95% CI 0.035 to
  0.141).

| Paired test vs CodeScene (2,770 files) | Repowise | CodeScene | p |
|---|---:|---:|---|
| Recall at 20%-of-lines budget | 0.173 | 0.074 | 0.003 |
| Popt | 0.607 | 0.462 | 0.003 |
| Defect density, size-normalized | 2.18x | 0.56x | 0.003 |
| ROC AUC | 0.731 | 0.705 | 0.054 (not significant) |
| Defect density, raw | 16.9x | 14.2x | 0.65 (not significant) |
| Precision at 20%-of-lines budget | 0.580 | 0.636 | 0.64 (tie) |
| Partial rho beyond size | -0.148 | -0.137 | both beat size (tie) |

CodeScene flags 27 files at its most severe level where Repowise flags 132 At
risk: a more conservative operating point, not a calibration flaw. Its "Code Red"
correlation of -0.58 with issue-resolution time (proprietary Jira data) did not
replicate on open GitHub data: -0.09 (95% CI -0.19 to +0.14), and six
queue-independent effort signals were flat.

**In-product check.** `defect_accuracy.py` ranks files by score, takes the 20
lowest and counts those touched by a fix commit in the trailing 180 days against the
repo base rate. It stays silent below 25 scored files or 5 fixed files. Because
`prior_defect` is an input, this is an association on indexed history, not a
forward test.

Full method and data:
[repowise-bench/health-defect](https://github.com/repowise-dev/repowise-bench/tree/master/health-defect)
([COMPARISON_REPORT.md](https://github.com/repowise-dev/repowise-bench/blob/master/health-defect/COMPARISON_REPORT.md)).

### 6.3 Fix first ranking (`fix_first/`, `worth.py`)

Fix first is built once in `analysis/health/fix_first/build.py` from stored rows
and rendered unchanged by `get_health`, `repowise health`, the dashboard and the
generated CLAUDE.md. Every excluded unit is counted in `totals.excluded` under one
reason:

| Reason | What it leaves out |
|---|---|
| `test`, `tooling`, `generated`, `vendored`, `docs_example` | Path-role classification. Tooling includes build files by type wherever they sit (Gradle, CMake, Makefiles, Bazel, MSBuild props/targets, root `setup.py`, `noxfile.py`, crate-root `build.rs`, bundler configs) |
| `unknown`, `expected`, `no_strategy`, `no_plan` | Performance causes with no classified context, expected repetition, no supported strategy, or no stored safe plan |
| `below_min_worth` | A refactoring recovering under `MIN_WORTH` (0.5) or with no steps; a loop-built string that is bounded or not in production |
| `history_only` | Only git-history findings |
| `deprecated` | Deprecated functions |
| `inherent_dispatch` | One dispatch holds 60% or more of the decision points (labels: 8 of 9 such rows rejected), unless a duplicate also sits in it |
| `small_function` | Under 30 code lines and CCN 15 (on 67 labelled rows this cut drops 13 rejected and 4 accepted) |
| `no_concrete_step` | No first edit with a file and line or a named group |
| `low_value_kind` | Extract Class (0 of 14 accepted), Move Method (0 of 34), low cohesion (0 of 46), long method (0 of 10), long parameter lists (0 of 2) |

Order: value first (the larger of health recovered and size: CCN 20/40/80/150,
lines 100/200/400/800, nesting 5/6/8, a critical or brain-method finding, one step
more on a hot file); a performance fix's value is its cost, with production,
entry-reachable, data-growing DB or network calls in the top band. Then tier
`now` / `next` / `later`, with at most `HEAD_PER_KIND` (3) of the first `HEAD` (5)
places per kind. `worth.py` decides `later` for every default list: a function
under CCN 40, 200 lines and nesting 6 (nesting under 8 only counts at 100 lines or
more) and not both CCN 25 and nesting 5; a dominant dispatch; nesting that is one
else-if or ternary chain; long but CCN under 20 and nesting under 5; a single
complex condition or error site. From CCN 80, 400 lines or nesting 8 a function is
worth doing regardless. The hot-file bonus orders items but never lifts one out of
`later`. Each item carries up to `MAX_TESTS` (5) tests from its validation profile.

The findings list uses the same rule: worth-doing findings lead and the rest carry
`lower_priority`. Class-design findings and history-only findings are lower
priority there; a performance opportunity is unless production code runs it over
growing data.

### 6.4 Performance opportunities (`perf/`)

**Sinks.** `io_in_loop` resolves a call inside a loop through the graph's
resolver and classifies the target against the I/O-boundary lexicon
(`io_boundaries.py`: db, network, filesystem, subprocess, lock). It fires only on a
real round-trip, never on a query-builder chain. A non-local sink is found by
walking the resolved call graph backwards up to three hops (`crossfn.py`), and the
`caller -> ... -> sink` path is attached.

**Gates.** Two markers use call-graph centrality as a precision gate: they fire
only in functions at or above the 80th percentile of distinct direct callers, and
never under two. That measures how widely a function is called, not request
reachability. `blocking_sync_in_async` skips tests, tooling, examples, generated
and vendored code.

**Opportunities.** One opportunity per intervention site within one cost family
and boundary: the function holding the loop, a shared helper every caller passes
through, or `<file>::__module__` for top-level code. Sinks, call sites and
same-family co-signals (`io_in_loop` with `nested_loop_with_io`) are members. Two
loops in one function are one site, because findings carry the sink's line. Each
carries one `actionability` state (`plan_ready`, `advisory`, `investigate`,
`expected`). The default queue holds production `plan_ready` and `advisory`, ranked
by `rank_score`; actionability only breaks ties. Excluded reasons are counted in
`default_queue`.

**Plans.** Proven means the transformation, not the runtime: parallelizing awaits
against a DB or network client is advisory with a `bounded_concurrency`
prerequisite. A loop already walking chunks is `loop_already_chunked`. When
`io_in_loop` and `serial_await_in_loop` fire on one call they name each other in
`siblings`. Plans list their edits (`mechanical` only when proven) and validation
tests (coverage, call graph, import graph, then `via: "name-match"`). An
opportunity links to a `performance_fix` refactoring plan only by exact
`opportunity_id`.

**Dataflow promotion.** `serial_await_in_loop` and `nested_loop_quadratic` are
promoted to `dataflow_verified: true` when a def/use pass proves iterations
independent. Promotion changes the wording, not the score.

**ORM lazy loads.** `lazy_load_in_loop` (sync SQLAlchemy, Django) needs the loop's
rows to resolve to one model through the cross-file model index and the attribute
to be a lazy relation the query does not eager-load. Weight is read per finding
from `details.orm` (`_PERFORMANCE_ORM_WEIGHT`): Django measured 29/32 (90.6%) on a
held-out sample and carries 0.7; SQLAlchemy, with under 30 held-out labels, stays at
0.4. `unbounded_read_reduced_in_memory` (Python) flags an unlimited query whose rows
are deduplicated per key in code.

**Dialects.** `perf/dialects/` registers 12 dialects over 19 language tags (TS/JS
also serve Vue, Svelte and Astro script blocks; C# serves Razor). C shares the C++ grammar
but has no perf dialect. A language without one emits nothing.

**Soundness.** Dynamic dispatch, monkeypatching and callbacks-as-values produce no
call edge. ORM lazy loads are seen only where the rows can be typed. Chains beyond
three hops are not followed; an unmodelled library has no classified sinks. The
published linter comparison and ranking quality figures are in
[repowise-bench/perf-detection](https://github.com/repowise-dev/repowise-bench/tree/master/perf-detection).

---

## 7. KPIs

Three repo-level numbers, computed in `compute_kpis()`:

- **Hotspot Health**: NLOC-weighted average over files where
  `git_meta_map[path]["is_hotspot"]` is true.
- **Average Health**: NLOC-weighted average over all files.
- **Worst Performer**: lowest-scoring file + its score.

These flow into `HealthSnapshot` rows (rolling 50 per repo) and feed the
CLI status one-liner, the `get_overview()` MCP block, and the dashboard
KPI cards.

---

## 8. Trends (`trends.py`)

State-free: callers pass an oldest-first list of snapshot rows. Both alerts
run over every metric in `_ALERT_METRICS` (hotspot health, the composite
headline and maintainability), skipping any a snapshot never recorded:

- **Declining Health**: current is ≥ `DECLINE_THRESHOLD` (default 0.5)
  below the snapshot `DECLINE_LOOKBACK` (5) positions back. Fires on the
  6th+ snapshot.
- **Predicted Decline**: the three most recent snapshots are each
  strictly below the one before. Magnitude is not required; direction is
  the signal.

Either can come back as a third `kind`, **`history_drag`**: a fall on the
composite headline where `driver` is `history` and the structure half held or
improved. It carries the same numbers and the opposite reading, because a
decline the code shape did not contribute to has nothing to act on and
reporting it in error red tells a reader their refactoring made things worse.
Maintainability is code shape already, so it has no halves to split and never
softens; it is the fall that always deserves the alarm.

`recent_kpis(history, limit=10)` returns a newest-first serialised view
for the CLI table and MCP `get_health(include=["trend"])` response.

### Per-file trajectory

Snapshots also store a compact `{path: score}` map (`per_file_scores_json`),
so the same window yields a single file's score-over-time series:

- `file_score_series(history, path)`: oldest-first `FileTrendPoint`s,
  skipping snapshots that don't carry the file. Returns `[]` below two
  points (silent on thin history). This is the exact function the PR bot
  reuses for its in-comment sparkline.
- `file_trend(history, path)`: wraps the series with `current` / `previous`
  / `delta` and a `declining` flag (the per-file mirror of the alerts above:
  ≥ `DECLINE_THRESHOLD` below the lookback point, or
  `PREDICTED_DECLINE_CONSECUTIVE` consecutive drops). `snapshot_count` is the
  full window size so a young repo is distinguishable from a file missing in
  older snapshots.

Both are state-free; the server serialises `FileTrend` via
`_file_trend_to_dict` and embeds it in the file-detail health block, the
health-breakdown response, and the standalone trend route (§13).

#### Below the floor

The stored score is clamped to `[SCORE_FLOOR, SCORE_MAX]`, so files 12.9 and
9.1 points deep both persist as `1.0` and their series is flat however much of
the work gets done. The second snapshot map (`per_file_deductions_json`) keeps
the pre-clamp deduction for exactly those files; for every other file the
deduction is `SCORE_MAX - score`, so there is nothing to store.

Each point therefore carries `unclamped_score` alongside `score`:
`SCORE_MAX - deduction` where the snapshot recorded one, and `score` otherwise.
It is the series `_file_declining` runs on, so `declining` describes the line
that can actually move, and a file getting worse *below* the floor now trips it.

Snapshots written before this existed have no deduction map. Their floored
files stay flat, which is correct; the depth was never measured, and inventing
one would be worse than a flat line.

### Per-file signals (`signals.py`)

The same state-free pattern, applied to the git-layer + topology fields we
already persist but only buried inside marker detail cards (or omitted
entirely). `file_signals(git_meta, degrees)` joins one `GitMetadata` row with
the file's graph degree into a `FileSignals` grouped as **Process**
(`prior_defect_count`, `change_entropy_pct` normalized 0-100, 90-day line
churn, `age_days`), **People** (recent vs all-time owner + commit share), and
**Topology** (`in_degree` / `out_degree`). No recompute: pure surfacing.

The honesty rule mirrors the trend: a field is `None` only when its *source
row* is absent (no git history means process/people silent; not a graph node
means topology silent), never imputed; a genuine `prior_defect_count` of `0` is kept
as a real signal. The server serialises it via `_file_signals_to_dict` and
embeds it in the file-detail health block and the breakdown response (§13); MCP
attaches a null-dropped copy to the `get_context` health block (§12). Mirrored
as `FileSignals` in `@repowise-dev/types/health`; rendered by the shared
`file-signals-panel.tsx` in both the drawer and the file-page Health tab.

---

## 9. Incremental analysis: the `repowise update` path

Full re-analysis would be wasteful on commit-sized diffs, so
`HealthAnalyzer.analyze()` accepts a `changed_files` set. When it is present:
duplication still runs full-repo (a changed file's clone partner may be
unchanged); the per-file loop skips files not in `changed_files`; and the KPIs
are **not** recomputed, since the subset would bias them. The dashboard
recomputes KPIs from the merged DB rows instead.

`update_cmd.py` builds the changed-files set from
`change_detector.get_changed_files()`, runs the analyzer, and persists through a
helper that uses the **upsert** variants (`upsert_health_metrics`,
`upsert_health_findings`, scoped to the changed paths) so unchanged files keep
their existing rows. The full-init writers (`save_health_findings`,
`save_health_metrics`) still use delete-then-insert: simpler, and the cost is
amortised across the whole `repowise init`.

---

## 10. Persistence schema

Four tables, all in the repo's `.repowise/wiki.db`. Foreign-keyed to
`repositories.id` with `ON DELETE CASCADE`.

### `health_findings`

One row per marker hit. Lifecycle: `open → acknowledged | resolved |
false_positive` (matches Dead Code). Open rows are deleted and rewritten on
full init and per changed file on `repowise update`; a triaged row is kept and
refreshed when its `public_id` is detected again, and `resolved` reopens.

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `repository_id` | FK |
| `file_path` | indexed |
| `biomarker_type` | `brain_method`, `nested_complexity`, ... |
| `severity` | `low` / `medium` / `high` / `critical` |
| `function_name` | nullable for file-level findings |
| `line_start`, `line_end` | nullable |
| `details_json` | per-marker evidence (CCN values, clone span, etc.) |
| `health_impact` | per-finding scaled deduction |
| `reason` | one-line summary string |
| `status` | lifecycle |
| `created_at`, `updated_at` | datetime |

**Identity and triage** (`finding_identity.py`). The public id
(`finding_<digest>`) is anchored on the enclosing function or class name plus
the finding's line offset into it, so an edit above the symbol keeps the id; a
file-level finding keeps absolute lines. Metric values are not part of the id;
only the detail keys a marker needs to tell two findings apart (a coupling
partner, an error kind) are. Each index replaces open findings, and a re-detected
finding matching a triaged row updates that row's evidence in place:
`acknowledged` and `false_positive` stay, `resolved` reopens.

### `health_file_metrics`

One row per file (unique on `(repository_id, file_path)`). Read directly
by the dashboard's file table.

| Column | Notes |
|---|---|
| `score` | 1.0–10.0 final |
| `defect_score`, `maintainability_score`, `performance_score` | per-pillar; nullable |
| `structure_deduction`, `history_deduction` | the two halves of the total deduction; nullable on rows written before the split. `counts=code_shape` rescores from `structure_deduction` alone, and a null pair is reported as unscored rather than counted as a ten |
| `is_test` | stored, not re-derived per surface, so `scope` is one row filter everywhere; nullable on rows predating the column |
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

Per-file coverage, overwritten on every `coverage add` run. Carries the
explicit `covered_lines_json` array so the `coverage_gap` marker can
flag the exact uncovered surface, not just the percent.

---

## 11. CLI surface

`packages/cli/src/repowise/cli/commands/health_cmd/`. Mirrors the
dead-code command's Click structure.

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
```

`repowise status` queries the same tables for a one-line summary:

```
Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: packages/server/.../app.py)
```

`repowise update` is unchanged from the user's perspective: health is
silently re-scored for changed files only.

---

## 12. MCP surface

### `get_health(targets?, include?, repo?, limit?)`

Defined in `tool_health/tool.py`, which dispatches to one module per mode and block. Modes:

- **Dashboard mode** (`targets=None`): returns repo-level KPIs (with the
  repo `band`) + the NLOC-weighted `distribution` across the bands +
  `worst_files` (top N lowest-scoring) + `top_findings` + a per-module
  `modules` rollup.
- **Targeted mode** (`targets=[...]`): returns full `metrics` +
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
| `"accuracy"` | the in-product defect-accuracy block (§6.2) |
| `"performance"` | performance opportunities (§6.4) |
| `"signals"`, `"churn_complexity"`, `"doc_drift"` | per-file signals, churn x complexity points, drift |
| `"defect"`, `"maintainability"`, `"advisory"` | dimensions for the ranked findings list (default: defect + maintainability) |
| `"unverified"`, `"semantics"` | provisional finding types (labelled), and the shared points/percentile legend |

`request.py::_KNOWN_INCLUDES` is the authority.

### Enrichments on existing tools

- `get_risk(targets)`: each per-target row carries `health_score`,
  `top_biomarkers`, `line_coverage_pct`, `branch_coverage_pct`.
- `get_context(targets, include=["health"])`: per-file `score`,
  `max_ccn`, `max_nesting`, `nloc`, `module`, `duplication_pct`, top
  2 markers (each with a `suggestion` string), a coverage block, and a
  null-dropped `signals` block (process/people/topology, see §8).
- `get_overview()`: adds a `code_health` block: avg, repo `band`, hotspot,
  worst performer, open finding count, and the NLOC-weighted `distribution`.

Every response carries the standard `_meta` envelope via `build_meta()`.

---

## 13. REST surface

`packages/server/src/repowise/server/routers/code_health/`: a package, one
module per surface, sharing `scope.py`, `counts.py`, `file_filters.py` and
`statuses.py`. All routes under `/api/repos/{repo_id}/health/`:

| Route | Returns |
|---|---|
| `GET /overview` | summary (with repo `band`) + `distribution` + lowest-scoring files + top findings + module rollup |
| `GET /badge.svg` | self-rendered flat SVG health badge (color + `N.N/10`, no letter) |
| `GET /badge.json` | Shields.io endpoint-badge payload (`schemaVersion`/`label`/`message`/`color`/`band`) |
| `GET /files` | per-file metrics |
| `GET /files/breakdown` | one file's metric + score breakdown + findings + suggestions + per-file `trend` + `signals` |
| `GET /files/trend` | one file's score-over-time series + current delta + `declining` flag (`?file_path=`) |
| `GET /trend` | repo KPI history + alerts + last-two-snapshot per-file deltas |
| `GET /findings` | findings list, filterable by `biomarker_type`, `file_path`, `dimension`, `status` and severity (`severity` exact, or the `min_severity` floor). The zero-impact dimensions, performance and advisory, are out of the ranked list: name one in `dimension`, name a marker in `biomarker_type`, or pass `include_zero_impact=true` |
| `GET /coverage` | coverage summary + per-file rows |
| `POST /coverage` | ingest a coverage report (used by some CI integrations) |
| `GET /refactoring-targets` | the work queue: files carrying findings, ranked by `total_impact / effort_bucket`. Takes the findings filters plus `search`, `module` and the `only_hotspots` / `only_untested` / `only_failing` row filters, pages by `limit` + `offset`, and returns `total` with `finding_total` beside it. Impact counts open findings only, so dismissing work moves a file down |
| `GET /churn-complexity` | churn × complexity scatter points (one per churned file: `commit_count_90d`, `max_ccn`, `nloc`, `score`, `churn_percentile`) |
| `GET /modules` | NLOC-weighted module rollup table |

`scope` and `counts` are accepted by every route whose figures they could
change, parsed by the shared `ScopeQuery` / `CountsQuery`, and echoed back. An
unrecognized value falls back to the default rather than answering a different
question under the name that was asked for.

`counts=code_shape` is a projection over the two stored deduction columns, not
a rescore: it re-derives each score from `structure_deduction`, drops
history-derived findings from the row sets so a list cannot sum past the figure
above it, and recomputes `total` and every ranking afterwards. Rows with no
stored split are removed and counted in `unscored_files`. Nothing is written
back.

Auth is the standard `verify_api_key` dependency from
`server/deps.py`.

---

## 14. Web dashboard

One page, `packages/web/src/app/repos/[id]/code-health/page.tsx`, with a map whose
lens switches between health, maintainability, performance and churn, and tabs
selected by `?tab=`: Overview (`triage`, leads with Fix first), Performance,
Findings, Tests (`coverage`), Dead code, Doc drift, Security and Blast radius
(`impact`). `scope` and `counts` controls sit above the tabs. The older
`/repos/[id]/health/*` routes are redirects into this page;
`code-health/refactoring-targets` and `code-health/trend` remain as sub-pages.

The churn lens is paired with `ChurnComplexityQuadrant` (fed by
`GET /churn-complexity`); the file Health tab carries the per-function
"Functions by churn" blame table.

All visual primitives live in `packages/ui/src/health/` so the hosted
`frontend/` repo (separate git checkout) can reuse them: port is mostly
data fetching + auth.

---

## 15. CLAUDE.md integration

The auto-generated `CLAUDE.md` includes a `## Code health` section when
the health tables are populated. The block is intentionally short;
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

## 16. Configuration: `.repowise/health-rules.json`

`.repowise/health-rules.json` is user-authored (the **only** JSON file in the
layer) and is loaded by `HealthConfig.load(repo_path)`. It carries repo-wide and
per-path `disabled_biomarkers` and `severity_overrides`, keyed by a
gitignore-semantics glob over the repo-relative POSIX path (every matching rule
applies; disabled sets union) (`path`, with `path_glob` and `glob` as
accepted aliases). `to_analyzer_config(file_paths)` resolves the globs to
per-file disabled sets, which the engine honors in `_evaluate_file()`. The
schema and examples are in the
[user guide](../layers/CODE_HEALTH.md#configuration).

---

## 17. Performance

The budget is **< 30 s on a 3,000-file synthetic repo**. The
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
  per file); a shared parse cache would remove it.
- **No N² loops in scoring.** Category aggregation is O(findings).

---

## 18. Testing

| Suite | What it locks |
|---|---|
| `tests/unit/health/test_complexity_walker.py` | Per-language CCN, nesting, cognitive assertions on handcrafted fixtures |
| `tests/unit/health/test_<biomarker>.py` | Each marker: positive in two languages + one negative |
| `tests/unit/health/test_duplication.py` | Tokenizer normalization, rolling-hash determinism, co-change weighting |
| `tests/unit/health/test_coverage_parsers.py` | LCOV / Cobertura / Clover / JaCoCo / Go coverprofile / repowise-JSON happy paths + edge cases |
| `tests/unit/health/test_scoring.py` | Deduction caps, clamping, KPI math |
| `tests/unit/health/test_scoring_snapshot.py` | **Stability guard**: caps, severity table, marker-to-category mapping, two known fixture scores |
| `tests/unit/health/test_trends.py` | Declining + predicted alerts, ordering, per-file series + `file_trend` |
| `tests/unit/health/test_signals.py` | `file_signals` join: no-signal vs real-zero, entropy 0-1 to 0-100, owner handoff |
| `tests/unit/health/test_churn_complexity.py` | `churn_complexity_points`: no-churn omission, complexity never filters, danger-product sort, percentile scaling |
| `tests/unit/health/test_suggestions.py` | Suggestion strings keyed correctly |
| `tests/unit/health/test_health_config.py` | `.repowise/health-rules.json` parsing + glob matching |
| `tests/integration/test_health_coverage_integration.py` | End-to-end LCOV -> analyzer -> coverage_gap fires |
| `tests/integration/test_health_perf_benchmark.py` | 30 s budget on 3,000 synthetic files (`-m slow`) |

99 unit tests + 2 integration tests at time of writing. Run with
`make health-check`.

---

## 19. Extension points

### Add a marker

1. New file under `biomarkers/` implementing the `Biomarker` Protocol.
2. Append to `_DETECTOR_FACTORIES` in `biomarkers/registry.py`.
3. Add the marker-to-category mapping in
   `scoring._BIOMARKER_CATEGORY`.
4. Add a suggestion template in `suggestions._TEMPLATES`.
5. Add at least three test cases (two positive in different languages,
   one negative).
6. Update `biomarkers/README.md`'s "Registered v1 detectors" list.

### Add a language to the complexity walker

Add one `LanguageNodeMap` entry to `complexity/languages.py` mapping the
language's tree-sitter control-flow node-type names to abstract `BRANCH`
/ `LOOP` / `TRY` / `BOOLEAN_OP` categories. Add a fixture under
`tests/fixtures/lang_samples/<lang>/`. **No `.scm` files needed**: those
are owned by the ingestion parser.

### Add a coverage format

Drop a parser under `coverage/` returning a `CoverageReport`. Route to it
from `coverage/detector.parse`. Stdlib-only (no extra XML libraries).

### Add a per-file override

Users (not contributors) author `.repowise/health-rules.json`. To add
a new override key (beyond `disabled_biomarkers`), extend
`HealthConfig` and thread it through `to_analyzer_config()` ->
`engine._evaluate_file()`.

---

## 20. Where the layer deliberately stops

Things the layer **does not** do, by design.

- **No LLM in scoring or suggestions.** `suggestions.py` is static
  templates. LLM code generation exists only behind the explicit
  `--generate-code` flag and the opt-in `generate_refactoring_code` MCP tool,
  and never feeds a score.
- **No symbol-level scoring.** Score lives at the file granularity to
  match how engineers think about refactor units. Symbol-level CCN
  still feeds the file score via `all_functions`.
- **No `complexity_estimate` propagation backfill.** The walker writes
  the field as a side effect during the current run; old indexes don't
  get touched until a re-index.
- **No predictive ML on trends.** `Predicted Decline` is a 3-snapshot
  direction check, not a model. (Commit-level change risk is a separate,
  shipped surface: the `analysis/change_risk/` package behind
  `repowise risk` scores a commit or base..head range with a calibrated
  logistic model.)
- **No letter grade.** The 1–10 score is the single number. The only
  categorical layer is the five absolute bands (Excellent / Good / Fair /
  Needs work / At risk, `grading.py`); a letter on top would be a third
  overlapping scale with arbitrary cliffs. Every surface that shows a band
  word or a band colour reads that one vocabulary; there is no second
  ramp.

---

## 21. Quick lookup: where do I edit X?

| I want to... | Edit... |
|---|---|
| Tweak a category cap | `scoring.CATEGORY_CAPS` (snapshot test will fail; update it) |
| Tweak a severity deduction | `scoring._SEVERITY_DEDUCTION` (ditto) |
| Add a new marker | `biomarkers/*.py`, `registry.py`, `scoring.py`, `suggestions.py` |
| Change the suggestion text for a marker | `suggestions._TEMPLATES` |
| Adjust the trend-alert threshold | `trends.DECLINE_THRESHOLD` / `DECLINE_LOOKBACK` |
| Change snapshot retention | `crud.HEALTH_SNAPSHOT_RETENTION` |
| Add a new MCP `include` flag | `tool_health/`: name it in `request.py`, read it in `loading.py`, render it in `blocks.py` beside the existing `"coverage"` / `"refactoring"` blocks |
| Add a new REST route | `routers/code_health.py`: auth is wired at the router level |
| Add a new dashboard view | new file under `packages/web/src/app/repos/[id]/health/`, primitives under `packages/ui/src/health/` |
| Add a CLI flag | `packages/cli/src/repowise/cli/commands/health_cmd/` |
| Wire the analyzer into a new entry point | call `HealthAnalyzer.analyze()` directly; persist via the upsert variants if your caller is incremental |

---

## See also

- [`docs/layers/CODE_HEALTH.md`](../layers/CODE_HEALTH.md): user-facing guide.
- [`packages/core/src/repowise/core/analysis/health/README.md`](../../packages/core/src/repowise/core/analysis/health/README.md): developer overview at the layer root.
- Sub-package READMEs under `complexity/`, `coverage/`, `duplication/`, `biomarkers/`.
- [`docs/architecture/graph-algorithms.md`](graph-algorithms.md): the graph layer health depends on.
