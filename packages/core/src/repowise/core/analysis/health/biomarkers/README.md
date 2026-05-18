# biomarkers/

One detector per file. Each implements the `Biomarker` Protocol:

```python
class Biomarker(Protocol):
    name: str  # snake_case identifier, e.g. "brain_method"
    category: str  # scoring category (see scoring.py)
    def detect(self, ctx: FileContext) -> list[BiomarkerResult]: ...
```

## Registered v1 detectors

- `brain_method` — symbols that are simultaneously long, complex, and
  central (high in-degree on the graph).
- `nested_complexity` — functions with nesting depth ≥ 4.
- `complex_method` — functions with CCN ≥ 9.

Phase 2 adds `untested_hotspot`, `coverage_gap`.
Phase 3 adds `bumpy_road`, `large_method`, `primitive_obsession`,
`dry_violation`, `developer_congestion`, `knowledge_loss`.

## Inputs

A `FileContext` dataclass populated by `engine.py`. Carries:

- `parsed_file` — ingestion `ParsedFile` (symbols, file_info).
- `function_metrics` — dict[symbol id → `FunctionComplexity`].
- `git_meta` — per-file git metadata dict (may be empty).
- `dependents_count` — number of in-edges on the file in the graph.
- `pagerank_score` — graph centrality score.

## Outputs

`BiomarkerResult` carries severity, function_name, line span, details
dict, and a `health_impact` deduction (added by the scorer). The `details`
dict is JSON-serialised into `HealthFinding.details_json` for the UI.

## Extension points

To add a 13th biomarker:

1. New file `biomarkers/my_marker.py` exporting a class with the
   `Biomarker` protocol shape and a module-level `BIOMARKER = MyMarker()`.
2. Add the class to `_DETECTOR_FACTORIES` in `registry.py`.
3. Add a deduction weight to `scoring.py`.
4. Add a unit test under `tests/unit/health/`.
