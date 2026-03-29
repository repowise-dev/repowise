---
description: Check the health of your Repowise index — sync state, page counts, provider, and token usage.
allowed-tools: Bash, Read
---

# Repowise Status

Check if Repowise is set up for this project and report its health.

## Steps

1. Check if `.repowise/` directory exists in the project root. If not: "This repo isn't indexed yet. Run `/repowise:init` to set it up."

2. Run: `repowise status`

3. The command outputs two tables:
   - **Sync State**: last sync commit, total pages, provider, model, total tokens
   - **Pages by Type**: breakdown by page type (file_page, module_page, etc.) with counts

4. Present the results to the user in a readable summary.

5. If total pages is 0 and no provider is shown, this was likely an index-only run. Tell the user: "Your repo is in analysis-only mode (graph + git + dead code). Run `/repowise:init` again with an LLM provider to generate full documentation and enable semantic search."

6. If the user provides arguments like "$ARGUMENTS", check if they're asking for a specific path and pass it: `repowise status $ARGUMENTS`
