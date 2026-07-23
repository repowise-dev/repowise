---
description: Ingest or inspect test-coverage reports — LCOV, Cobertura/Clover, or coverage.py .coverage (builds the per-test map when contexts are present).
allowed-tools: Bash, Read
---

# Repowise Coverage

Ingest coverage so untested-hotspot markers light up in `repowise health` and
so `repowise impacted-tests` can map a diff to the tests that exercise it.
No LLM — pure report ingestion into the local index.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/repowise:init` first." Stop.
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
- A report path (`coverage.lcov`, `lcov.info`, `coverage.xml`, `.coverage`, …)
  → `repowise coverage add <path>`
- "add" with no path → `repowise coverage add` (auto-discover common report paths)
- Multiple paths → `repowise coverage add <a> <b>` (merged; hit wins)

Useful flags on `add`: `--verbose` for ingestion debug logs; `--path <dir>`
to point at a different repo.

## Notes

- `coverage add` always stores per-file line/branch coverage.
- The **per-test map** (needed by `/repowise:impacted-tests`) is built only
  when the report carries per-test contexts — e.g. a coverage.py `.coverage`
  written with `coverage run --contexts=test`, or a per-test lcov. Reports
  without contexts ingest aggregates only; say so plainly.
- After ingesting, suggest `repowise health` so untested-hotspot markers update.
