# Codex Example

This example shows the expected Codex setup for an initialized Repowise repository.

## Setup

```bash
cd /path/to/your-repo
codex login status
repowise init --codex --no-prose --yes    # fast smoke: no LLM wiki prose, no key needed
repowise init --codex --provider codex_cli --yes
```

`repowise init --codex --no-prose` is the quick setup smoke path. Drop `--no-prose` when you want the full LLM-generated wiki, or drop `--yes` too and let `init` ask. `repowise init --codex` writes project-local Codex files:

```text
.codex/config.toml
.codex/hooks.json
AGENTS.md
```

It does not write to global `~/.codex/config.toml`.

## Smoke Checks

```bash
codex mcp list
codex exec --ephemeral --sandbox read-only --json "Return exactly OK"
```

Inside Codex, start a new session in the repository and ask:

```text
Use Repowise to summarize the architecture and identify risky files before editing.
```

Codex should have Repowise MCP available, load the managed `AGENTS.md`, and receive hook guidance from `.codex/hooks.json`.

## Local Plugin

From the Repowise repository root:

```bash
codex plugin marketplace add .
codex
/plugins
```

Install the Repowise plugin from the local marketplace. The plugin bundles Repowise MCP, hooks, and Codex-neutral skills; it does not add slash commands. Plugin-bundled hooks require `[features] plugin_hooks = true` in current Codex releases.

Full integration notes: [docs/agent/CODEX.md](../../docs/agent/CODEX.md).
