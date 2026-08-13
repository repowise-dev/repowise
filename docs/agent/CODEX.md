# Codex Integration

Repowise supports Codex in three separate ways:

- Project setup for Codex MCP and lifecycle hooks.
- The `codex_cli` LLM provider for wiki generation through your authenticated Codex CLI subscription.
- A local Codex plugin with bundled MCP, hooks, and Repowise skills.

These features use project-local files. `repowise init --codex` writes under the repository, not to global `~/.codex/config.toml`.

For a short smoke walkthrough, see [examples/codex/](../../examples/codex/).

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
- `PostToolUse` for `Bash|shell_command` to detect git operations that make the wiki stale. On current Codex the shell calls arrive as `shell_command`; `Bash` is kept in the set for older and future builds. See [HOOKS.md](HOOKS.md) for why `exec` is deliberately excluded.
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

Repowise sends the prompt on stdin, parses Codex JSONL output, records token usage from `turn.completed.usage`, and treats `codex_cli/*` cost as `$0.00` because subscription billing happens outside Repowise API pricing. `--model` is passed to Codex only when you explicitly configure a model. `--reasoning minimal` maps to Codex `model_reasoning_effort="minimal"` when the selected model advertises a `minimal` level, and falls back to `"low"` when it does not; `low`, `medium`, `high`, and `xhigh` pass through when the model advertises those levels. `off`/`none` maps to `model_reasoning_effort="none"`. `auto` sends no effort at all and lets Codex pick.

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

The plugin bundles Repowise MCP, lifecycle hooks, and Codex-neutral skills for exploration, pre-modification checks, architectural decisions, and dead-code cleanup. Plugin-bundled hooks are opt-in in current Codex releases; enable them with `[features] plugin_hooks = true` if you want hooks loaded from an installed plugin.

## Slash commands

The plugin does not carry these, and cannot: a Codex plugin manifest has no slot for commands. Codex reads slash commands from `~/.codex/prompts/`, which is global and which only the CLI can write, so Repowise installs them there:

```bash
repowise agents add --target=codex
```

That writes one `repowise-*.md` per command, and `repowise agents remove --target=codex` takes them back out, along with `.codex/config.toml`, `.codex/hooks.json` and the managed block in `AGENTS.md`. To remove every agent at once, see [`repowise uninstall`](../reference/CLI_REFERENCE.md#repowise-uninstall-path). Invoke them as `/prompts:repowise-risk`, `/prompts:repowise-ask` and so on. They are rendered from the same `plugins/shared/` source as the Claude Code plugin's commands, so the two hosts cannot drift.

Note this is the mirror image of Claude Code, where the commands come from the plugin and `repowise init` never writes any.

## AGENTS.md

`repowise init --codex` generates a managed `AGENTS.md` by default. `repowise update` refreshes it when `editor_files.agents_md` is enabled, or when `--agents` is passed. The Repowise section is bounded by managed markers and user content outside the markers is preserved.

`AGENTS.md` is a host-neutral convention rather than a Codex-only file: [OpenCode](OPENCODE.md) and [Hermes](HERMES.md) read the same path and manage the same section. All of them writing it is safe, because the section is idempotent. Removing one agent while another is still wired leaves the section in place and reports that it did, so the agent still using it does not silently lose its instructions.

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
