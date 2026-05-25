# Codex Integration

Repowise supports Codex in three separate ways:

- Project setup for Codex MCP and lifecycle hooks.
- The `codex_cli` LLM provider for wiki generation through your authenticated Codex CLI subscription.
- A local Codex plugin with bundled MCP, hooks, and Repowise skills.

These features use project-local files. `repowise init --codex` writes under the repository, not to global `~/.codex/config.toml`.

## Prerequisites

Install and authenticate the Codex CLI:

```bash
npm install -g @openai/codex
codex login
codex login status
```

Repowise checks `codex --version` and `codex login status`. When both succeed, interactive `repowise init` offers to enable Codex project setup. Non-interactive runs require `--codex`; use `--no-codex` to skip the prompt.

## Project MCP Setup

Run from the repository root:

```bash
repowise init --codex
```

Repowise merges this server into `.codex/config.toml`:

```toml
[mcp_servers.repowise]
command = "repowise"
args = ["mcp"]
cwd = "/absolute/path/to/repo"
startup_timeout_sec = 20

[features]
hooks = true
```

The MCP server uses `repowise mcp` without a path. In no-path mode, Repowise walks upward from the current directory to the nearest initialized `.repowise` repository.

Smoke check:

```bash
codex mcp list
```

## Codex Hooks

Repowise writes hooks to `.codex/hooks.json`, not inline `[hooks]` tables. The default hooks call the import-isolated `repowise-augment` entry point for:

- `SessionStart` to add Repowise MCP workflow guidance.
- `UserPromptSubmit` to remind Codex when Repowise context is available.
- `PostToolUse` for `Bash` to detect git operations that make the wiki stale.
- `PostToolUse` for `apply_patch`, `Edit`, and `Write` to remind Codex after edits.

Claude Code has its own search-result enrichment hook path for `Grep` and `Glob`. Codex setup stays focused on lifecycle guidance and freshness checks instead of trying to reuse that Claude-specific search enrichment.

## `codex_cli` Provider

Use `codex_cli` when you want Repowise page generation to run through your Codex CLI subscription instead of an API key:

```bash
repowise init --provider codex_cli --codex --yes
```

You can also persist it:

```bash
REPOWISE_PROVIDER=codex_cli repowise update
```

The provider runs:

```bash
codex exec --ephemeral --sandbox read-only --json --cd /absolute/path/to/repo -
```

Repowise sends the prompt on stdin, parses Codex JSONL output, records token usage from `turn.completed.usage`, and treats `codex_cli/*` cost as `$0.00` because subscription billing happens outside Repowise API pricing. `--model` is passed to Codex only when you explicitly configure a model. `--reasoning minimal` maps to Codex `model_reasoning_effort="low"`; `low`, `medium`, `high`, and `xhigh` pass through when the selected Codex model advertises those levels. `off`/`none` is not supported by the Codex CLI provider.

Smoke check:

```bash
codex exec --ephemeral --sandbox read-only --json "Return exactly OK"
```

## Plugin And Skills

The repository includes a local Codex plugin:

```text
.agents/plugins/marketplace.json
plugins/codex/.codex-plugin/plugin.json
plugins/codex/.mcp.json
plugins/codex/hooks/hooks.json
plugins/codex/skills/*/SKILL.md
```

From the Repowise repository root, add the local marketplace to Codex, then install the Repowise plugin from the Codex plugin browser:

```bash
codex plugin marketplace add .
codex
/plugins
```

The plugin bundles Repowise MCP, lifecycle hooks, and Codex-neutral skills for exploration, pre-modification checks, architectural decisions, and dead-code cleanup. It does not add Claude-style slash commands. Plugin-bundled hooks are opt-in in current Codex releases; enable them with `[features] plugin_hooks = true` if you want hooks loaded from an installed plugin.

## AGENTS.md

`repowise init --codex` generates a managed `AGENTS.md` by default. `repowise update` refreshes it when `editor_files.agents_md` is enabled, or when `--agents` is passed. The Repowise section is bounded by managed markers and user content outside the markers is preserved.

Controls:

```bash
repowise init --no-agents
repowise init --agents
repowise update --no-agents
repowise update --agents
```

The generated section tells Codex when to use Repowise MCP tools for overview, search, context, risk, why/decision history, dependency tracing, diagrams, and dead-code cleanup.

## Official Codex Docs

- [Codex hooks](https://developers.openai.com/codex/hooks)
- [Codex MCP](https://developers.openai.com/codex/mcp)
- [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
- [Codex plugins](https://developers.openai.com/codex/plugins)
- [Build Codex plugins](https://developers.openai.com/codex/plugins/build)
- [Codex skills](https://developers.openai.com/codex/skills)
- [AGENTS.md instructions](https://developers.openai.com/codex/guides/agents-md)
