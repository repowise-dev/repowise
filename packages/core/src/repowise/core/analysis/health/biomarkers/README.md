# biomarkers/

One detector per file. Each implements the `Biomarker` Protocol:

```python
class Biomarker(Protocol):
    name: str       # snake_case identifier, e.g. "brain_method"
    category: str   # scoring category (see scoring.CATEGORY_CAPS)
    def detect(self, ctx: FileContext) -> list[BiomarkerResult]: ...
```

## Registered detectors (24)

Structural complexity (cap −2.5):
- `brain_method` — symbols simultaneously long, complex, and central. The
  centrality gate is language-agnostic: `dependents ≥ min(8, max(repo p80, 3))`,
  so it fires on sparse-graph languages (TS/Rust) instead of assuming
  Python's import density.
- `low_cohesion` — a class whose methods form unrelated clusters (LCOM4 ≥ 2).
- `god_class` — a large class (≥ 200 NLOC, ≥ 15 methods) that also hides a
  brain method.
- `nested_complexity` — functions with deep nesting (≥ 4 levels).
- `bumpy_road` — multiple branches at the same nesting depth.
- `complex_conditional` — compound boolean expressions with ≥ 3 ops.

Size & complexity (cap −1.5):
- `complex_method` — functions with CCN ≥ 9.
- `large_method` — functions exceeding the NLOC threshold.
- `primitive_obsession` — many primitive parameters in a single signature
  (suppressed in very small modules, where a wide signature is idiomatic).

Duplication (cap −1.0):
- `dry_violation` — Rabin–Karp clone pairs, weighted by co-change.

Test coverage (cap −2.0):
- `untested_hotspot` — hotspot × low coverage × many dependents.
- `coverage_gap` — non-test files with meaningful uncovered surface.

Test coverage — continuous (cap −2.0, own category `test_coverage_gradient`):
- `coverage_gradient` — a per-file deduction that scales **continuously** with
  the uncovered fraction (`4.0 × (1 − line_coverage_pct/100)`, capped) for files
  with KNOWN coverage; silent when coverage was never ingested (absent ≠
  uncovered). Unlike the binary gates above it fires across the whole 0–100%
  range, so well-tested files at 85–99% still carry proportional signal. Uses
  the `deduction` override on `BiomarkerResult` (continuous magnitude in place of
  the discrete severity table), kept in its own capped category so it neither
  squeezes nor is squeezed by the binary gates. Calibrated offline: +0.043 corpus
  AUC [95% CI +0.023, +0.061] on the covered subset, Popt-neutral.

Organizational (cap −3.5):
- `developer_congestion` — too many active authors competing on a file.
- `knowledge_loss` — primary authors no longer active (de-rated to 0.4).
- `hidden_coupling` — files that co-change in history without an explicit
  import edge between them.
- `function_hotspot` — functions that are both structurally complex and
  frequently modified (per-function churn from the FULL-tier blame index).
- `code_age_volatility` — dormant functions (median line age ≥ 1y) that
  are suddenly being modified again. Uses the same blame index.
- `ownership_risk` — long-run ownership dispersion: many minor
  contributors (each < 5% of commits) or no dominant owner. Bird's
  strongest literature defect correlate.
- `churn_risk` — relative churn: a file whose 90-day window rewrote a
  large fraction of its own lines (size-normalized, so it doesn't simply
  re-flag big files).
- `change_entropy` — Hassan's History Complexity Metric: how scattered a
  file's changes are across noisy commits (not a churn proxy). Reads the
  FULL-tier `change_entropy` / `change_entropy_pct` git fields.
- `co_change_scatter` — breadth of co-change coupling: a file coupled to
  many others (shotgun surgery). Complements `hidden_coupling`, which
  flags specific undeclared pairs.
- `prior_defect` — recent bug-fix history: the count of bug-fix commits
  touching the file in the trailing ~6-month window (same keyword rule the
  defect benchmark labels fixes with, anchored to the index's `as_of`
  reference so historical/T0 scoring stays leakage-free). Reads the
  `prior_defect_count` git field. **Neutral weight (1.0) by design** — on the
  calibration corpus it is largely redundant with `change_entropy`/`churn_risk`
  (no measured lift), so it ships as an interpretable, actionable finding
  ("bug-fixed N times recently") rather than a boosted predictor.

Test quality (cap −0.5, test files only):
- `large_assertion_block` — a test function with a run of ≥ 15 consecutive
  assertions (one case testing many behaviours at once).
- `duplicated_assertion_block` — copy-pasted assertion runs across tests,
  found by intersecting the clone detector with assertion spans.

Caps were recalibrated to lift `organizational` (was −1.0) and de-rate
`size_and_complexity` / `duplication` per plan §3.1. A per-biomarker
weight multiplier in `scoring._BIOMARKER_WEIGHT_MULTIPLIER` lets the
strongest empirical predictors deduct more than the uniform severity
table alone would allow.

## Inputs

`FileContext` (see `base.py`) carries:

- `file_path`, `language`, `nloc`, `module`, `has_test_file`.
- `function_metrics` — `dict[symbol_name → FunctionComplexity]`.
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

## Outputs

`BiomarkerResult` carries severity, function name, line span, a `details`
dict (JSON-serialised into `HealthFinding.details_json` for the UI), and a
`reason` string. `health_impact` is filled in by the scorer. An optional
`deduction` field carries a continuous deduction magnitude (health points,
pre-weight/pre-cap); when set the scorer uses it instead of the discrete
severity → deduction table, letting a biomarker (e.g. `coverage_gradient`)
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

To add a 13th biomarker:

1. New file `biomarkers/my_marker.py` with a class implementing the
   `Biomarker` protocol.
2. Append to `_DETECTOR_FACTORIES` in `registry.py`.
3. Add the biomarker → category mapping in `scoring._BIOMARKER_CATEGORY`.
4. Add a suggestion template in `suggestions._TEMPLATES`.
5. Add a unit test under `tests/unit/health/`.
6. Update this README's "Registered v1 detectors" list.
