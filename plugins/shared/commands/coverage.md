---
frontmatter: |
    description: Ingest or inspect test-coverage reports (LCOV, Cobertura, Clover, Go cover profiles, JaCoCo, repowise JSON, or coverage.py .coverage; builds the per-test map when contexts are present), or gate a change on its patch coverage in CI.
    allowed-tools: Bash(repowise coverage:*), Read
---

# Repowise Coverage

Ingest coverage so untested-hotspot markers light up in `repowise health` and
so `repowise impacted-tests` can map a diff to the tests that exercise it.
No LLM — pure report ingestion into the local index.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `{{cmd:init}}` first." Stop.
2. Decide the mode from `$ARGUMENTS` (see below), run the command, and present
   a short summary (files covered, line/branch %, whether a per-test map was
   built). Don't dump raw JSON unless asked.

## Modes

Default / "status" — show what is already ingested:
```
repowise coverage status
```

Handle `$ARGUMENTS`:
- "status" / "show" → `repowise coverage status`
- A report path (`coverage.lcov`, `lcov.info`, `coverage.xml`, `coverage.out`,
  `jacoco.xml`, `.coverage`, …) → `repowise coverage add <path>`
- "add" with no path → `repowise coverage add` (auto-discover common report paths)
- Multiple paths → `repowise coverage add <a> <b>` (merged; hit wins)
- "check" / "gate" / "patch coverage" → `repowise coverage check [REVSPEC]`

Useful flags on `add`: `--verbose` for ingestion debug logs; `--path <dir>`
to point at a different repo; `--format <parser>` to force a parser instead
of auto-detecting from content.

## Gating a change in CI

`repowise coverage check` reports patch coverage: the share of a change's
executable lines the tests ran. It needs no index, so it runs in CI.
REVSPEC is the change (`origin/main...HEAD`, `base..head`, or one commit;
defaults to the default branch `...HEAD`).
```
repowise coverage check origin/main...HEAD --report coverage/lcov.info --fail-under 80
```
- `--report FILE` (repeatable; discovered from the usual locations when omitted)
- `--fail-under PCT` exits 1 below the threshold (defaults to
  `coverage.fail_under` in `.repowise/config.yaml`)
- `--report-format <parser>` forces a parser
- `--format github` writes annotations plus a step summary in GitHub Actions;
  `--format json|markdown` for other consumers

## Notes

- `coverage add` always stores per-file line/branch coverage.
- The **per-test map** (needed by `{{cmd:impacted-tests}}`) is built only
  when the report carries per-test contexts — e.g. a coverage.py `.coverage`
  written with `coverage run --contexts=test`, or a per-test lcov. Reports
  without contexts ingest aggregates only; say so plainly.
- After ingesting, suggest `repowise health --recompute` so untested-hotspot markers update.
