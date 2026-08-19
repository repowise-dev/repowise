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

- Requires a per-test map from `repowise coverage add` on a report with
  contexts. If none is ingested, the command prompts to run
  `/repowise:coverage` / `repowise coverage add` — it does **not** invent an
  empty "no tests needed" result.
- A changed file with no coverage rows may get a filename-pattern **guess**
  labelled as a guess — never present guesses as coverage-backed.
- A brand-new file with neither coverage nor a paired test is "unknown, run
  the full suite".
- For a whole-change defect-risk score, use `/repowise:risk`. For per-file
  blast radius / `tests_to_run`, use the `get_risk` MCP tool.
