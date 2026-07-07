# coverage/

Coverage report ingestion + test-file heuristics for the code-health layer.

## Public API

```python
from repowise.core.analysis.health.coverage import (
    CoverageReport, FileCoverage,
    parse, detect_format,
    is_test_file, paired_test_file,
    # discovery + resolution
    CoverageConfig, ResolvedCoverage,
    discover_artifacts, resolve_reports, build_coverage_map,
)
```

- `parse(text, format=None)` → `CoverageReport`. Auto-detects format
  when `format` is ``None``; pass an explicit ``"lcov" | "cobertura" |
  "clover" | "repowise-json"`` to override.
- `parse_lcov(text)`, `parse_cobertura(text)`, `parse_clover(text)`,
  `parse_repowise_json(text)` — format-specific entry points (used by
  tests / programmatic callers).

### Repowise normalized JSON (`repowise-coverage-v1`)

A small, explicit JSON shape so coverage from *any* runner can be
normalized once (keyed by repo-relative POSIX path) and fed to
`repowise health --coverage`:

```json
{
  "format": "repowise-coverage-v1",
  "commit_sha": "abc123",
  "files": {
    "src/foo.py": {
      "line_coverage_pct": 87.5,
      "branch_coverage_pct": 70.0,
      "covered_lines": [1, 2, 5],
      "total_coverable_lines": 40
    }
  }
}
```

`files` may also be a list of the same objects, each with its own
`file_path`. Tolerant: any two of `(line_coverage_pct, covered_lines,
total_coverable_lines)` pin a file down; an entry that anchors none is
skipped (absent ≠ zero).
- `detect_format(text)` — returns the sniffed format or ``None``.
- `is_test_file(path, source=None)` — path + optional content heuristic.
- `paired_test_file(path, all_paths)` — find the conventional test
  partner for a source path.

## Discovery + path resolution (`discovery.py`)

Ingested coverage only helps if its file paths line up with the indexed
tree. repowise's canonical file key is **repo-relative, forward-slash
POSIX** (set in `ingestion/traverser.py`). Almost no coverage tool emits
that key — lcov / nyc / c8 / cargo-llvm-cov write absolute paths, Cobertura
writes paths relative to its own `<source>` root — so we reconcile them.

- `discover_artifacts(repo_root, globs=None)` — glob the filesystem for
  report files (`coverage/lcov.info`, `**/cobertura.xml`, ...). The report
  dirs are excluded from the indexed file set, so discovery hits the FS
  directly; results are pruned of vendored dirs and capped.
- `resolve_reports(reports, repo_keys, ...)` — map each report path to a
  canonical key by **longest trailing-segment overlap**, refusing to guess
  on a true tie. Merges multiple reports hit-wins. Returns a
  `ResolvedCoverage` with the engine `coverage_map`, rewritten
  `FileCoverage` rows, and `matched` / `unmatched` / `ambiguous`
  diagnostics (surfaced so coverage never silently shows 0%).
- `build_coverage_map(repo_root, report_paths, repo_keys, ...)` — read +
  parse + resolve end-to-end.

### Config (`.repowise/config.yaml`)

All keys optional; the defaults give zero-config auto-discovery during
`repowise init` / `repowise update`.

```yaml
coverage:
  auto_discover: true          # discover reports during indexing
  artifacts:                   # override the default discovery globs
    - coverage/lcov.info
  paths:                       # explicit report paths (skip discovery)
    - build/coverage/lcov.info
  format: lcov                 # force a parser (else content-sniffed)
  strip_prefix: build          # drop a leading prefix from report paths
  path_prefix: packages/web    # prepend a prefix to report paths
  reingest_on_update: false    # re-parse on every update (else reuse DB rows)
```

`CoverageConfig.from_repo_config(load_repo_config(repo_path))` parses it.

## Inputs

- A raw coverage report (the contents of `coverage.lcov`, a Cobertura
  `coverage.xml`, or a Clover `clover.xml`).
- For the test-file heuristic: a POSIX-style relative path and optionally
  the file contents (used to detect framework imports for files that
  don't follow naming conventions).

## Outputs

- `CoverageReport(source_format, files: list[FileCoverage], commit_sha)`.
- Each `FileCoverage` carries `file_path` (POSIX-normalized), line/branch
  coverage percentages, the explicit covered-line set, and the total
  coverable line count.

## Extension points

- Add a parser: drop a new file (e.g. `jacoco.py`), return a
  `CoverageReport`, and route to it from `detector.parse`.
- Tune the test-file heuristic: edit the module-level `_TEST_*` tuples in
  `detector.py`. Keep the rule deterministic (no globs that require
  recursion).
- The parsers depend only on the Python stdlib — no XML schema, no
  external XML library.
