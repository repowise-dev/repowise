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

5. If pages exist but no provider is shown, the wiki is template-rendered. Tell the user: "Your wiki is rendered from structure. Run `repowise generate` to upgrade any subset to model-written prose." If total pages is 0, the run did not finish, so suggest `repowise init --resume`.

6. If the user provides arguments like "$ARGUMENTS", check if they're asking for a specific path and pass it: `repowise status $ARGUMENTS`
