---
description: Rank a live change for review using a repo-relative percentile and an auditable supporting diff-shape score.
allowed-tools: Bash, Read
---

# Repowise Risk

Assess a *change* (not a file) for review priority. The authoritative fields are
the repo-relative `risk_percentile` and `classification`. The response includes
`fix_history`: the recency-weighted bug-fix record of the files the change
touches, which of them carry it, and where that sits among the repo's own
recent commits. The supporting 0–10 score measures diff size and spread (named
in `score_measures`) rather than danger and is not a probability. Pure git +
learned constants: no LLM, no network. A natural pre-merge / PR gate.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/repowise:init` first." Stop.
2. Resolve the target from `$ARGUMENTS`, run `repowise risk`, and present the
   fix history first (which files keep breaking, and the repo-relative
   percentile), then the score and its drivers.

## Choosing the revspec

- No args → `repowise risk` (scores uncommitted work, or `HEAD` when the tree is clean)
- A commit SHA → `repowise risk <sha>`
- A range / PR / branch → `repowise risk <base>..<head>` (e.g. `repowise risk main..HEAD`)

Useful flags:
- `--ext .py,.ts` — only count changes in those file types
- `-x, --exclude <pattern>` — gitignore-style pattern to drop from the diff (repeatable; also honors a `.riskignore` file)
- `--format json` — machine-readable fix history + score + features + drivers
- `--baseline <n>` — how many recent commits to rank the change against (0 disables)
- `-t, --target <path>` — score what history says about named files instead of a change (repeatable; reads the index)
- `--path <dir>` — point at a different git repo

## How this differs from the other risk views

- This command = the **whole change** ranked against recent repository history.
- For **per-file** structural reach, missing co-changes, and missing tests,
  use the `get_risk` MCP tool / the `change-review` skill.
- For **per-file health** scores, use `/repowise:health`.
