---
description: Trigger an incremental Repowise update to sync documentation with recent code changes.
allowed-tools: Bash, Read
---

# Repowise Update

Trigger an incremental update of the Repowise index.

## Steps

1. Check if `.repowise/` exists. If not: "This repo isn't indexed yet. Run `/repowise:init` first."

2. Run: `repowise update`

   This will:
   - Detect files changed since last sync commit
   - Update git metadata for changed files
   - Regenerate affected wiki pages (up to cascade budget of 30)
   - Decay confidence scores for indirectly affected pages
   - Update dead code analysis
   - Update CLAUDE.md if enabled

3. Show the output summary to the user.

## Available flags

- `--provider NAME` — override the LLM provider
- `--model NAME` — override the model
- `--since REF` — base git ref to diff from (overrides saved last_sync_commit). Accepts a commit SHA, tag, or branch name.
- `--cascade-budget N` — max pages to regenerate per run (default: 30)
- `--dry-run` — show affected pages without regenerating

## Handling $ARGUMENTS

If the user provides arguments:
- "dry-run" or "dry run" → add `--dry-run` flag
- "force" or "full" → add `--cascade-budget 999` to regenerate all stale pages
- a git ref like a SHA or tag → add `--since {ref}`
- a file path → this is not supported by update; suggest `repowise update` which auto-detects changes
