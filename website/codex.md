---
layout: default
title: Codex Integration
nav_order: 5.5
---

# Codex Integration
{: .no_toc }

Use Repowise with Codex MCP, lifecycle hooks, `AGENTS.md`, the `codex_cli` provider, and a local plugin.
{: .fs-6 .fw-300 }

---

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Quick setup

```bash
npm install -g @openai/codex
codex login
codex login status

cd /path/to/your-repo
repowise init --codex --provider codex_cli --yes
```

`--codex` generates project-local `.codex/config.toml` and `.codex/hooks.json`. It does not write to global `~/.codex/config.toml`.

## MCP

Repowise writes:

```toml
[mcp_servers.repowise]
command = "repowise"
args = ["mcp"]
cwd = "/path/to/your-repo"
startup_timeout_sec = 20

[features]
hooks = true
```

`repowise mcp` can run without a path because it resolves upward to the nearest `.repowise` repository.

Smoke check:

```bash
codex mcp list
```

## Hooks

Codex hooks are written to `.codex/hooks.json`:

| Event | Purpose |
|-------|---------|
| `SessionStart` | Add Repowise MCP workflow guidance |
| `UserPromptSubmit` | Remind Codex that Repowise context is available |
| `PostToolUse` `Bash` | Detect git operations and stale wiki state |
| `PostToolUse` `apply_patch\|Edit\|Write` | Remind after edits that context may need refresh |

Repowise keeps Codex hooks focused on lifecycle guidance and freshness checks rather than trying to reuse Claude-specific search enrichment.

## Provider

`codex_cli` uses your local Codex CLI auth/subscription:

```bash
repowise init --provider codex_cli --yes
```

It runs:

```bash
codex exec --ephemeral --sandbox read-only --json --cd <repo> -
```

Repowise records token usage from Codex JSONL and treats `codex_cli/*` cost as `$0.00`. `--model` is passed to Codex only when explicitly configured.

## Plugin and skills

The Repowise repository includes a local Codex plugin at `plugins/codex` and a marketplace entry at `.agents/plugins/marketplace.json`.

```bash
cd /path/to/repowise
codex plugin marketplace add .
codex
/plugins
```

Install Repowise from the plugin browser. The plugin bundles MCP config, lifecycle hooks, and Codex-neutral skills. It does not add Claude-style slash commands. Plugin-bundled hooks are opt-in in current Codex releases; enable `[features] plugin_hooks = true` if you want hooks loaded from an installed plugin.

## AGENTS.md

`repowise init --codex` generates managed `AGENTS.md` by default. `repowise update` refreshes it when `editor_files.agents_md` is enabled, or when `--agents` is passed. Use:

```bash
repowise init --no-agents
repowise update --agents
```

User content outside the Repowise managed markers is preserved.

## Official docs

- [Codex hooks](https://developers.openai.com/codex/hooks)
- [Codex MCP](https://developers.openai.com/codex/mcp)
- [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
- [Codex plugins](https://developers.openai.com/codex/plugins)
- [Build plugins](https://developers.openai.com/codex/plugins/build)
- [Codex skills](https://developers.openai.com/codex/skills)
- [AGENTS.md](https://developers.openai.com/codex/guides/agents-md)
