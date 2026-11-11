# biomarkers/

One detector per file. Each implements the `Biomarker` Protocol:

```python
class Biomarker(Protocol):
    name: str       # snake_case identifier, e.g. "brain_method"
    category: str   # scoring category (see scoring.CATEGORY_CAPS)
    def detect(self, ctx: FileContext) -> list[BiomarkerResult]: ...
```

## Registered detectors

`registry.py` registers 59 detectors: the 29 code-health markers below, 22
performance markers, 3 SQL maintainability markers, 4 advisory Apex markers and
1 advisory COBOL marker. Every id with its threshold
and weight is in
[the full roster](../../../../../../../../docs/architecture/code-health.md#the-full-roster).

Caps below are per category. History (organizational) is a hard cap; every other
defect category bends toward its cap on a saturating curve and can deduct up to
3.09 times it (`shape_curve.py`).

Structural complexity (cap −2.5):
- `brain_method` — symbols simultaneously long, complex, and central. The
  centrality gate is language-agnostic: `dependents ≥ min(8, max(repo p80, 3))`,
  so it fires on sparse-graph languages (TS/Rust) instead of assuming
  Python's import density. Advisory: listed, deducts nothing, since
  `complex_method` and `large_method` already charge the same function.
- `low_cohesion` — a class whose methods form unrelated clusters (LCOM4 ≥ 2).
- `god_class` — a large class (≥ 200 NLOC, ≥ 15 methods) that also hides a
  brain method.
- `nested_complexity` — functions with deep nesting (≥ 4 levels).
- `repeated_nesting` — multiple branches at the same nesting depth.
- `complex_conditional` — compound boolean expressions with ≥ 3 ops.

Size & complexity (cap −1.5):
- `complex_method` — functions with CCN ≥ 9.
- `large_method`: functions of 60+ code lines with a judged CCN of at least 3
  (severity bands at 60, 90, 120 and 200 lines).
- `primitive_obsession` — many primitive parameters in a single signature
  (suppressed in very small modules, where a wide signature is idiomatic).

Duplication (cap −1.0):
- `dry_violation` — Rabin–Karp clone pairs, weighted by co-change.

Test coverage (cap −2.0):
- `untested_hotspot` — hotspot × low coverage × many dependents.
- `coverage_gap` — non-test files with meaningful uncovered surface.
- `untested_complexity` — functions whose CRAP score (`CC² × (1 − cov)³ + CC`,
  `health/crap.py`) is 30 or more, on measured coverage or a reach tier.
  Advisory.

Test coverage — continuous (cap −2.0, own category `test_coverage_gradient`):
- `coverage_gradient` — a per-file deduction that scales **continuously** with
  the uncovered fraction (`4.0 × (1 − line_coverage_pct/100)`) for files with
  KNOWN coverage; silent when coverage was never ingested (absent ≠ uncovered).
  It uses the `deduction` override on `BiomarkerResult`. Rationale and
  calibration: `docs/architecture/code-health.md`, marker notes.

Organizational (hard cap `history_cap`: 1.0 plus the file's other defect
deduction, at most −3.5; the largest finding counts whole, the rest at 0.25):
- `developer_congestion` — too many active authors competing on a file.
- `knowledge_loss` — primary authors no longer active (de-rated to 0.4).
- `hidden_coupling` — files that co-change in history without an explicit
  import edge between them. Advisory: filed here, deducts nothing (it showed no
  defect signal on a held-out, pre-registered test).
- `function_hotspot` — functions that are both structurally complex and
  frequently modified (per-function churn from the FULL-tier blame index).
- `code_age_volatility` — dormant functions (median line age ≥ 1y) that
  are suddenly being modified again. Uses the same blame index.
- `ownership_risk` — long-run ownership dispersion: many minor
  contributors (each < 5% of commits) or no dominant owner. Bird's
  strongest literature defect correlate.
- `churn_risk`: a file whose 90-day window rewrote a large fraction of its
  own lines (size-normalized, so it doesn't re-flag big files).
- `change_entropy` — Hassan's History Complexity Metric: how scattered a
  file's changes are across noisy commits. Reads `change_entropy` /
  `change_entropy_pct`.
- `co_change_scatter`: a file coupled to many others (shotgun surgery);
  `hidden_coupling` flags specific undeclared pairs.
- `prior_defect`: recent bug-fix history, bug-fix commits touching the file
  in the trailing ~6-month window, anchored to the index's `as_of` so T0
  scoring stays leakage-free. Reads `prior_defect_count`. **Neutral weight
  (1.0) by design**: largely redundant with `change_entropy` / `churn_risk`, it
  ships as an actionable finding ("bug-fixed N times recently").

Test quality (cap −0.5, test files only):
- `large_assertion_block` — a test function with a run of ≥ 15 consecutive
  assertions (one case testing many behaviours at once).
- `duplicated_assertion_block` — copy-pasted assertion runs across tests,
  found by intersecting the clone detector with assertion spans.
- `assertion_free_test`: a test that checks nothing. Advisory.
- `mock_saturated_test`: mock setup that dwarfs the assertions. Advisory.

Error handling (cap −0.5, own category `error_handling`):
- `error_handling` — swallowed-exception / unsafe-unwrap anti-patterns: an
  empty or trivial `catch`/`except` body (Python/JS/TS/Java/Kotlin/C#/C++), a
  Python catch-all `except:` / `except Exception:`, Rust `.unwrap()` /
  `.expect()` / panic-family macros, and Go's empty `if err != nil {}` or
  blank-identifier discard of a call's error.
- One LOW finding per occurrence (0.15 after the floored 0.5 weight); the
  category deducts at most about 1.5 under the curve. **A maintainability flag,
  not a defect predictor**: AUC-neutral on the 21-repo T0 benchmark and
  excluded from the defect calibration. Precision-first (unsupported language or
  parse failure ⇒ no signal), in a whole-tree walker pass.

A per-marker weight multiplier in `scoring._BIOMARKER_WEIGHT_MULTIPLIER` lets
the strongest empirical predictors deduct more than the uniform severity table
alone would allow.

## Inputs

`FileContext` (see `base.py`) carries:

- `file_path`, `language`, `nloc`, `module`, `has_test_file`.
- `all_functions` — `tuple[FunctionComplexity, ...]`, every walked
  function in document order. Not keyed by name: a name key drops a
  file's anonymous `it` callbacks and same-named methods on a second
  class. Empty for SQL.
- `class_metrics` — `list[ClassComplexity]` (LCOM4, method count, size).
  Empty for languages whose walker map doesn't opt into class analysis.
- `git_meta` — per-file git metadata (commits, owners, bus factor,
  co-change partners).
- `dependents_count` — file-level in-edge count from the graph.
- `repo_dependents_p80` — repo-wide p80 of file in-degree; `None` with no
  graph. The language-agnostic centrality floor for `brain_method`.
- `pagerank_score` — graph centrality (0.0 when symbol-only).
- `line_coverage_pct`, `branch_coverage_pct`, `covered_lines` — coverage
  signals; `None` when no coverage was ingested.
- `clones`, `duplication_pct` — pre-computed cross-file clone data.
- `graph_view` — thin `HasEdge` protocol wrapper over the dependency
  graph; `None` on test fixtures that didn't construct a graph.
- `repo_commit_counts` — `dict[path, commit_count_total]` populated once
  per `analyze` call so co-change detectors can look up partner totals.
- `error_handling_hits` — `list[ErrorHandlingHit]` (kind + line) from the
  walker's whole-tree anti-pattern pass; empty means "no signal".

## Outputs

`BiomarkerResult` carries severity, function name, line span, a `details`
dict (JSON-serialised into `HealthFinding.details_json` for the UI), and a
`reason` string. `health_impact` is filled in by the scorer. An optional
`deduction` field carries a continuous deduction magnitude (health points,
pre-weight/pre-cap); when set the scorer uses it instead of the discrete
severity → deduction table, letting a marker (e.g. `coverage_gradient`)
express a per-finding signal that varies continuously while staying linear and
attributable.

## Performance characteristics

- Each detector is pure and stateless — safe to share across threads.
- The registry instantiates one fresh detector list per `detect_all`
  call. Cheap (constructors take no args).
- All detectors are O(symbols in file). No detector walks the whole
  parsed_file set on its own — cross-file signals (clones, co-change) are
  pre-aggregated by the engine.

## Extension points

To add a marker:

1. New file `biomarkers/my_marker.py` with a class implementing the
   `Biomarker` protocol.
2. Append to `_DETECTOR_FACTORIES` in `registry.py`.
3. List it in `scoring._BIOMARKER_DIMENSIONS` (an unlisted marker defaults into
   defect), then add it to its dimensions' category and weight tables, or to
   `_ADVISORY_HOME` for an advisory marker.
4. Add a label in `biomarker_labels.BIOMARKER_LABELS` and a suggestion template
   in `suggestions._TEMPLATES`.
5. Add a unit test under `tests/unit/health/`.
6. Update this README's "Registered detectors" list and the roster in
   `docs/architecture/code-health.md`.
