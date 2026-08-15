---
description: Score the defect risk of a change — a commit or a base..head range — as a single 0–10 with drivers.
allowed-tools: Bash, Read
---

# Repowise Risk

Score a *change* (not a file) for defect risk. The response leads with
`fix_history`: the recency-weighted bug-fix record of the files the change
touches, which of them carry it, and where that sits among the repo's own
fix-bearing files. The 0–10 score sits beside it and measures diff size and
spread (named in `score_measures`) rather than danger, so read the fix history
first. Pure git + learned constants: no LLM, no network. A natural pre-merge /
PR gate.

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

- This command = the **whole change** as one number (defect-risk gate).
- For **per-file** blast radius (what breaks, missing co-changes, missing tests),
  use the `get_risk` MCP tool / the `change-review` skill.
- For **per-file health** scores, use `/repowise:health`.
