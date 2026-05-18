# Code Health analysis layer

Fifth intelligence layer alongside Graph, Git, Docs, and Decisions. Computes a
per-file health score (1.0–10.0) from deterministic biomarkers. Zero LLM
calls. Pure Python over tree-sitter + git data.

## Public API

```python
from repowise.core.analysis.health import HealthAnalyzer, HealthReport

analyzer = HealthAnalyzer(graph, git_meta_map, parsed_files=parsed_files)
report = analyzer.analyze(config=None)
# report.findings: list[HealthFindingData]
# report.metrics:  list[HealthFileMetricData]
# report.kpis:     {"hotspot_health": float, "average_health": float, ...}
```

## Inputs

- `graph`: NetworkX `DiGraph` from `graph_builder.graph()`.
- `git_meta_map`: dict mapping file path → git metadata dict (hotspot,
  commit_count_90d, primary_owner_name, contributor_count, bus_factor, ...).
- `parsed_files`: list[`ParsedFile`] from the ingestion phase.

## Outputs

`HealthReport` — see `models.py`. Persisted to SQLite via
`crud.save_health_findings()` + `crud.save_health_metrics()`.

## Sub-packages

- `complexity/` — tree-sitter AST walker; computes CCN, max nesting depth,
  cognitive complexity per function. Single AST pass per file. Writes
  `Symbol.complexity_estimate` as a side effect.
- `coverage/` — Phase 2. LCOV / Cobertura / Clover parsers + test-file
  heuristic.
- `duplication/` — Phase 3. Rabin–Karp over tree-sitter tokens.
- `biomarkers/` — one detector per file. Each implements the `Biomarker`
  Protocol from `biomarkers/base.py`.

## Extension points

- New biomarker → drop a file under `biomarkers/`, implement
  `Biomarker.detect(ctx) -> list[BiomarkerResult]`, register via
  `biomarkers/registry.py`.
- New language for the complexity walker → add a `LanguageNodeMap` entry to
  `complexity/languages.py`. No new `.scm` files needed.
- New coverage format → drop a parser under `coverage/` (Phase 2).
