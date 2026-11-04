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
  "clover" | "repowise-json" | "go-coverprofile" | "jacoco" | "apex"`` to override
  (`detector.PARSERS` is the registry).
- `parse_lcov(text)`, `parse_cobertura(text)`, `parse_clover(text)`,
  `parse_repowise_json(text)`, `parse_go_coverprofile(text)`,
  `parse_jacoco(text)` — format-specific entry points (used by
  tests / programmatic callers).
- Go cover profiles (`go test -coverprofile`) are expanded from statement
  blocks to lines, so their percentages are line-based, not Go's statement
  percentage. JaCoCo XML is read from per-line `<sourcefile>` data (lines
  and branches); package paths such as `com/foo/Bar.java` and Go module
  paths are mapped to repo files by suffix matching.
- `detect_format(text)`: returns the sniffed format or ``None``.
- `is_test_file(path, source=None, *, roots=None)`: path + optional content
  heuristic; pass pytest `roots` so a module pytest does not collect is not a
  test. Callers holding the stored `is_test` flag use that instead.
- `paired_test_file(path, all_paths)`: find the conventional test partner for
  a source path.
- `apex.py` (`parse_apex`, `parse_apex_contexts`): Salesforce CLI JSON. Paths
  are `Name.cls` / `Name.trigger`, mapped to the one file of that name; the run
  result's `ApexCodeCoverage` records are the per-test map.
- `contexts.py` (`parse_contexts_file`, `parse_lcov_contexts`): per-test readers
  for a coverage.py `.coverage` SQLite file with contexts and for per-test LCOV
  `TN:` records; a report without contexts yields no records.

### Repowise normalized JSON (`repowise-coverage-v1`)

A small, explicit JSON shape so coverage from *any* runner can be
normalized once (keyed by repo-relative POSIX path) and fed to
`repowise coverage add`:

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
skipped (absent ≠ zero). An optional `coverable_lines` array gives the
executable-line set patch coverage needs.

## Discovery + path resolution (`discovery.py`)

Ingested coverage only helps if its file paths line up with the indexed
tree. repowise's canonical file key is **repo-relative, forward-slash
POSIX** (set in `ingestion/traverser.py`). Almost no coverage tool emits
that key — lcov / nyc / c8 / cargo-llvm-cov write absolute paths, Cobertura
writes paths relative to its own `<source>` root — so we reconcile them.

- `discover_artifacts(repo_root, globs=None)` — glob the filesystem for
  report files (`coverage/lcov.info`, `**/cobertura.xml`, ...). The report
  dirs are excluded from the indexed file set, so discovery hits the FS
  directly; results are pruned of vendored dirs and capped. A pruned dir
  spelled literally in a pattern (Gradle's `build/reports/jacoco/**/*.xml`)
  is searched; one reached only through `**` is not.
- `resolve_reports(reports, repo_keys, ...)` — map each report path to a
  canonical key by **longest trailing-segment overlap**, refusing to guess
  on a true tie. Merges multiple reports hit-wins. Returns a
  `ResolvedCoverage` with the engine `coverage_map`, rewritten
  `FileCoverage` rows, and `matched` / `unmatched` / `ambiguous`
  diagnostics (surfaced so coverage never silently shows 0%).
- `build_coverage_map(repo_root, report_paths, repo_keys, ...)` — read +
  parse + resolve end-to-end. `report_prefixes` gives a report its own
  prefix, `ignore` drops entries `coverage.ignore` matches, resolved or not
  (counted in `ResolvedCoverage.ignored`), and a Go coverprofile's import
  paths under a `go.mod`'s module path (found on disk) are mapped to that
  module's directory.
- `expand_report_patterns(patterns, base)`: the report files a list of paths
  or globs names, shared by `coverage.paths` and `coverage check --report`;
  `**` goes through the same pruned walk as discovery.
- `configured_coverage(repo_root)`: the `coverage:` config (defaults when
  unreadable) for the stored-coverage surfaces (REST, agent tools), so they
  apply the same `coverage.ignore` and `coverage.gates` as the CLI gate.

### Config (`.repowise/config.yaml`)

All keys are optional; the defaults give zero-config auto-discovery during
`repowise init` / `repowise update`. The full `coverage:` block is in
`docs/layers/TEST_INTELLIGENCE.md#configuration`; besides those keys, a
top-level `path_prefix` prepends a prefix to every report path, and
`reingest_on_update` re-reads reports on every update when `true`, never when
`false`, and when one is newer than the last ingest when unset.
`CoverageConfig.from_repo_config(load_repo_config(repo_path))` parses it.
Each `gates` entry is `name`, `paths` (gitignore-style globs, at least one
not a `!` exclusion), optional `fail_under` (0-100) and optional
`informational`; valid ones land in `CoverageConfig.gates` as `PathGate`, and
each invalid one leaves a message naming it in `gate_errors`. `coverage check`
refuses to run on any; the stored-coverage surfaces carry them as
`scope.config_errors` and judge no path gate.

## Inputs

- A raw coverage report (the contents of `coverage.lcov`, a Cobertura
  `coverage.xml`, a Clover `clover.xml`, a Go `coverage.out`, or a JaCoCo
  `jacoco.xml`).
- For the test-file heuristic: a POSIX-style relative path and optionally
  the file contents (used to detect framework imports for files that
  don't follow naming conventions).

## Outputs

- `CoverageReport(source_format, files: list[FileCoverage], commit_sha)`.
- Each `FileCoverage` carries `file_path` (POSIX-normalized), line/branch
  coverage percentages, the explicit covered-line set, and the total
  coverable line count.

## Extension points

- Add a parser: drop a new file, return a `CoverageReport` built with
  `model.file_coverage`, register it in `detector.PARSERS` and add a sniff
  rule to `detector.detect_format`.
- Tune the test-file heuristic: edit the module-level `_TEST_*` tuples in
  `detector.py`. Keep the rule deterministic (no globs that require
  recursion).
- The parsers depend only on the Python stdlib — no XML schema, no
  external XML library.
