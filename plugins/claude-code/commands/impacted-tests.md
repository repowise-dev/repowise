---
description: Print the tests whose coverage intersects a change's changed lines — for a commit, base..head range, or staged diff.
allowed-tools: Bash, Read
---

# Repowise Impacted Tests

Map a change to the tests that actually exercise its changed lines, using the
per-test coverage map from `repowise coverage add`. No LLM, no network —
an index lookup. Useful as a pre-merge / CI gate ("run these 40, not all 4,000").

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/repowise:init` first." Stop.
2. Resolve the target from `$ARGUMENTS`, run `repowise impacted-tests`, and
   present the test ids. Be honest about gaps (see Notes).

## Choosing the revspec

- No args → `repowise impacted-tests` (staged changes; same as `--staged`)
- A commit SHA → `repowise impacted-tests <sha>`
- A range / PR / branch → `repowise impacted-tests <base>..<head>`
  (e.g. `repowise impacted-tests main..HEAD`)
- "staged" → `repowise impacted-tests --staged`

Useful flags:
- `--format list` — test ids one per line (pipe to `xargs pytest`)
- `--format json` — full report
- `--path <dir>` — point at a different repo

```
repowise impacted-tests main..HEAD --format list | xargs pytest
```

## Notes

- Line-precise answers need a per-test map from `repowise coverage add` on a
  report with contexts. If none is ingested, the command still prompts to run
  `/repowise:coverage` / `repowise coverage add`. It does **not** invent an empty
  "no tests needed" result.
- A changed file with no coverage rows gets *candidates* instead, in a table
  headed "NOT coverage-backed", each carrying a `via` marker: `changed-test`
  (the changed file is itself a test), `call-graph` or `import-graph` (a test
  file that reaches it, no ingest needed), or `filename-pattern` (a name-shaped
  guess). Report them as candidates. Only `via: coverage` proves a test
  executed the change.
- A file none of those can speak to is "unknown, run the full suite".
- For a whole-change defect-risk score, use `/repowise:risk`. For per-file
  blast radius / `tests_to_run`, use the `get_risk` MCP tool.
