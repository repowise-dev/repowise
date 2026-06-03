---
description: Score the defect risk of a change — a commit or a base..head range — as a single 0–10 with drivers.
allowed-tools: Bash, Read
---

# Repowise Risk

Score a *change* (not a file) for defect risk. Repowise reads the diff shape —
lines added/deleted, files, directories, subsystems, change entropy, and author
familiarity — and returns a 0–10 score with an attributable driver breakdown.
Pure git + learned constants: no LLM, no network. A natural pre-merge / PR gate.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/repowise:init` first." Stop.
2. Resolve the target from `$ARGUMENTS`, run `repowise risk`, and present the
   score, level, and top drivers — calling out *why* it's risky (e.g. wide
   diffusion across subsystems, low author familiarity).

## Choosing the revspec

- No args → `repowise risk` (scores `HEAD`)
- A commit SHA → `repowise risk <sha>`
- A range / PR / branch → `repowise risk <base>..<head>` (e.g. `repowise risk main..HEAD`)

Useful flags:
- `--ext .py,.ts` — only count changes in those file types
- `--format json` — machine-readable score + features + drivers
- `--path <dir>` — point at a different git repo

## How this differs from the other risk views

- This command = the **whole change** as one number (defect-risk gate).
- For **per-file** blast radius (what breaks, missing co-changes, missing tests),
  use the `get_risk` MCP tool / the `change-review` skill.
- For **per-file health** scores, use `/repowise:health`.
