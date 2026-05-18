# coverage/

Coverage report ingestion + test-file heuristics for the code-health layer.

## Public API

```python
from repowise.core.analysis.health.coverage import (
    CoverageReport, FileCoverage,
    parse, detect_format,
    is_test_file, paired_test_file,
)
```

- `parse(text, format=None)` → `CoverageReport`. Auto-detects format
  when `format` is ``None``; pass an explicit ``"lcov" | "cobertura" |
  "clover"`` to override.
- `parse_lcov(text)`, `parse_cobertura(text)`, `parse_clover(text)` —
  format-specific entry points (used by tests / programmatic callers).
- `detect_format(text)` — returns the sniffed format or ``None``.
- `is_test_file(path, source=None)` — path + optional content heuristic.
- `paired_test_file(path, all_paths)` — find the conventional test
  partner for a source path.

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
