---
description: Trigger an incremental Repowise update to sync documentation with recent code changes.
---

# Repowise Update

Trigger an incremental update of the Repowise index.

## Steps

1. Check if `.repowise/` exists. If not: "This repo isn't indexed yet. Run `/prompts:repowise-init` first."

2. Run: `repowise update`

   This will:
   - Detect files changed since last sync commit
   - Update git metadata for changed files
   - Regenerate affected documentation pages (the cascade budget scales with the change)
   - Decay confidence scores for indirectly affected pages
   - Update dead code analysis
   - Update CLAUDE.md if enabled

3. Show the output summary to the user.

## Available flags

- `--full`: upgrade the index. On an Index only repo (`init --mode fast`) it backfills the full git history and builds the documentation: from code with no key, or written by a model when one is configured. On a repo built from code, a configured model writes the documentation. A model run shows the cost first; `--yes` accepts it.
- `--provider NAME`: override the LLM provider
- `--model NAME`: override the model
- `--since REF`: base git ref to diff from (overrides saved last_sync_commit). Accepts a commit SHA, tag, or branch name.
- `--cascade-budget N`: max pages to regenerate per run (auto-scaled when unset)
- `--working-tree`: also index uncommitted work
- `--dry-run`: show affected pages without regenerating
- `-v, --verbose`: show the full changed-file list, per-phase internals, and debug logs (the run is quiet by default)

## Handling $ARGUMENTS

If the user provides arguments:
- "dry-run" or "dry run": add `--dry-run`
- "full" or "force": run `repowise update --full`. If a model is configured, ask the user before adding `--yes`, which accepts the cost estimate.
- a git ref like a SHA or tag: add `--since {ref}`
- a file path: update does not take one; suggest `repowise update`, which detects changes itself
