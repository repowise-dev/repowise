# User Guide

How the pieces of repowise fit together: one index, the layers built on it,
and the three ways to read it (your agent, the CLI, the dashboard). Every
command and flag lives in the [CLI Reference](../reference/CLI_REFERENCE.md);
first-time setup is in the [Quickstart](QUICKSTART.md).

## One index, read three ways

1. **Index.** `repowise init` parses every file to an AST, builds the
   dependency graph, reads git history, scores code health and renders the
   wiki into `.repowise/wiki.db`. This is the one slow step.
2. **Ask.** Everything after that is a read against the index: your agent
   through MCP tools, you through the CLI or the dashboard. Nothing
   re-analyzes anything.
3. **Keep fresh.** `repowise update` catches the index up incrementally after
   each commit. Every MCP response carries the indexed commit and warns when
   it trails your `HEAD`.

The default run needs no key and makes no model calls. A model adds written
documentation, decisions read from PR and commit history, and synthesized
answers; an embedder adds semantic search. Both are upgrades in place:
[Modes, costs and providers](MODES.md).

## The layers

| Piece | What it holds | Read more |
|---|---|---|
| Graph | Files, symbols, imports and calls, each edge with how it was resolved | [Graph](../layers/GRAPH.md), [Language support](../layers/LANGUAGE_SUPPORT.md) |
| Git history | Churn, hotspots, co-change, ownership and bug-fix history | [Ownership](../layers/OWNERSHIP.md), [Bug history](../layers/BUG_HISTORY.md), [Change risk](../layers/CHANGE_RISK.md) |
| Wiki | A page per module and file, built from code or written by a model, with search over code and pages | [Wiki](../layers/WIKI.md), [Doc drift](../layers/DOC_DRIFT.md) |
| Decisions | Why the code is shaped this way, from ADRs, comments, history and agent sessions | [Decisions](../layers/DECISIONS.md) |
| Health | Per-file defect, maintainability and performance scores, the Fix first queue, dead code, refactoring plans | [Code health](../layers/CODE_HEALTH.md), [Dead code](../layers/DEAD_CODE.md), [Refactoring](../layers/REFACTORING.md) |

The layers combine: change risk reads the graph and git together, untested
hotspots join git, graph and test reach. The map of how they feed each other:
[Intelligence layers](../layers/INTELLIGENCE_LAYERS.md).

## Your agent

**Connect once.** `repowise init` wires Claude Code (MCP server and hooks)
and VS Code. The other seven hosts (Codex CLI, Cursor, OpenCode, Hermes,
GitHub Copilot CLI, Kiro, Windsurf) need `repowise agents add
--target=<id>`; Codex also takes `--codex` on `init`. The per-host table is in
[Quickstart](QUICKSTART.md#4-connect-your-agent), with depth per host in
[Agent integrations](../agent/INTEGRATIONS.md).

**Ten tools, task-shaped.** Your agent gets architecture summaries, per-file
triage cards with callers and ownership, symbol source with exact bounds, risk
assessment for a set of changed files, decision lookups and health scores, each
in one call. Claude Code lists all ten. Other clients list the five core tools
by default, which keeps each request small; `repowise config set mcp.tools
standard` lists all ten there too. What each one answers:
[MCP Tools](../agent/MCP_TOOLS.md).

**Context that arrives unasked.** Hooks add a session briefing, the governing
decision when the agent edits a file it covers, and a warning on files with a
run of recent bug fixes. They make no model or network calls:
[Hooks](../agent/HOOKS.md). Agents that do not speak MCP still read the
`CLAUDE.md` and `AGENTS.md` that `init` generates from the index.

**Fewer tokens.** `repowise distill <cmd>` compresses noisy command output
before the agent reads it, and `repowise expand <ref>` returns anything it
omitted: [Distill](../agent/DISTILL.md).

## The CLI

The same answers on the command line: `repowise health`, `repowise dead-code`,
`repowise risk main..HEAD`, `repowise why <path>`, `repowise impacted-tests`
and `repowise search`. `repowise status` shows the mode and how far behind the
index is; `repowise doctor` checks the install, keys and wired agents. Every
command and flag: [CLI Reference](../reference/CLI_REFERENCE.md). Every
setting: [Config](../reference/CONFIG.md).

## The dashboard

`repowise serve` starts the API on port 7337 and the dashboard on port 3000,
both reading the same index. Every view and what it answers:
[Dashboard](DASHBOARD.md).

## Keeping it fresh

`init` installs a post-commit hook that runs `repowise update` after every
commit. `repowise watch` updates while you edit; webhooks and polling cover
servers: [Auto-Sync](../scale/AUTO_SYNC.md). A new `git worktree` seeds its
index from the main checkout: [Worktrees](../scale/WORKTREES.md).

## Beyond one repo

`repowise init` in a folder of repositories builds a workspace: cross-repo
contracts, co-change and one MCP server over all of them
([Workspaces](../scale/WORKSPACES.md)). Pull-request gates run through the
GitHub Action or the GitLab template ([CI](CI.md)). Repowise Hosted runs the
same index for a team: [Commercial](../business/COMMERCIAL.md).

## What init writes

```
your-repo/
├── .repowise/
│   ├── wiki.db           # pages, symbols, graph, git data, health scores
│   ├── state.json        # sync metadata (last commit, pages, tokens used)
│   ├── config.yaml       # provider, model, embedder, excludes
│   ├── .env              # saved API keys (gitignored)
│   └── ...               # search indexes and caches
├── .mcp.json             # MCP server entry for Claude Code and other clients
├── .claude/CLAUDE.md     # generated Claude Code context
├── .vscode/mcp.json      # VS Code MCP entry
├── AGENTS.md             # generated agent context, with --codex or --agents
└── .codex/               # project-local Codex MCP and hooks config, with --codex
```

Outside the tree, `init` registers the MCP server with Claude Code
(`~/.claude.json`) and Claude Desktop, adds the Claude Code hooks, and installs
a git post-commit hook that runs `repowise update`. `--no-editor-setup` (or
`REPOWISE_SKIP_EDITOR_SETUP=1`) skips all of that and writes only `.repowise/`;
`--no-hook` skips only the post-commit hook.

`.repowise/` is safe to delete and rebuild, and safe to gitignore. A team that
wants everyone on the same index without each person generating it can commit it.

## Common workflows

| Moment | Commands | More |
|---|---|---|
| Before you open a pull request | `repowise risk main..HEAD`, `repowise impacted-tests main..HEAD`, `repowise health --trend` | [Change risk](../layers/CHANGE_RISK.md) |
| Reviewing someone else's pull request | `repowise risk origin/main..their-branch`, `repowise health --file <path>` | [CI](CI.md) for the same checks on every pull request |
| Onboarding someone new | `repowise init --provider anthropic`, then `repowise serve` | [Modes, costs and providers](MODES.md) |
| Changing provider or model | `repowise init --provider <name> --model <id> --force` to regenerate, `repowise update --provider <name>` for future updates | [Config](../reference/CONFIG.md) |
| Cutting cost on a large repo | `repowise init --dry-run` shows the estimate before anything is spent | [Troubleshooting](TROUBLESHOOTING.md#indexing) |

## When something looks wrong

Start with `repowise doctor`, then `repowise doctor --repair`:
[Troubleshooting](TROUBLESHOOTING.md).
