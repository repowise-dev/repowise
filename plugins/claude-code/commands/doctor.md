---
description: Diagnose the Repowise setup — install, API keys, index/store drift — and optionally repair it.
allowed-tools: Bash(repowise doctor:*), Bash(uv --version), Read
disable-model-invocation: true
---

# Repowise Doctor

Check that Repowise is healthy: the install, configured provider / API keys, and
whether the index and stores are consistent with the repo.

## Steps

1. Run `repowise doctor`.
2. Summarize what passed and what didn't. For each problem, give the concrete fix
   (set an env var, run `/repowise:update`, re-run `/repowise:init`, etc.).

## Flags from `$ARGUMENTS`

- "repair" / "fix" → `repowise doctor --repair` (attempts to fix detected
  mismatches). Tell the user what it changed.
- "workspace" → `repowise doctor --workspace` (check every repo in the workspace);
  `--no-workspace` to force single-repo.
- a path → `repowise doctor <path>`

## Common findings

- **repowise not installed / not on PATH** → suggest `pip install repowise` (or
  `python -m pip install repowise` on Windows), then `/repowise:init`.
- **The plugin's MCP server does not connect** → it starts through uv: check
  `uv --version`, and point a user without uv to
  https://docs.astral.sh/uv/getting-started/installation/, then a restart.
- **No API key for the configured provider** → show the variable name for the
  provider in `.repowise/config.yaml` as a `.repowise/.env` line with
  `YOUR_API_KEY` in place of the key; the user fills it in. This is not fatal for `init`, which falls
  back to the template wiki. Only an explicitly named `--provider` errors out.
- **Index drift (HEAD ahead of last sync)** → run `/repowise:update`.
- **Missing embeddings / store drift** → `/repowise:reindex`.
