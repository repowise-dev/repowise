# Quickstart

Index your repository and have your coding agent answer from that index. The
default setup needs no API key, makes no LLM calls and sends nothing off your
machine.

> Looking for one command's flags? See the
> [CLI Reference](../reference/CLI_REFERENCE.md). For everyday workflows, see
> the [User Guide](USER_GUIDE.md).

## Fastest path: let your agent do it

Open your coding agent (Claude Code, Codex, Cursor, VS Code Copilot, OpenCode,
Hermes or any MCP client) in the repository and paste this line:

```text
Read https://docs.repowise.dev/setup.md and set up Repowise in this repository.
```

That page is a runbook written for agents. Following it, the agent will:

1. Install repowise with the first installer it finds: `uv tool install`, then
   `pipx`, then `pip`.
2. Index the repository keyless (`repowise init --yes --no-prose`).
3. Wire the MCP server into the host it is running in.
4. Ask you one question: build the documentation from the index for free, or
   have an LLM write it. If you pick an LLM, it asks which provider, tells you
   which key variable to set in `.repowise/.env`, and shows you a cost estimate.
   Nothing is spent until you approve that estimate.
5. Ask you to restart the host, then call `get_overview` to confirm the tools
   work.

The agent never asks you to paste a key into the chat. The rest of this page
does the same steps by hand.

## By hand

### 1. Install

Python 3.11+ and Git are required. Use the first one you have:

```bash
uv tool install repowise          # uv brings its own Python
pipx install repowise
python3 -m pip install repowise   # Windows: python -m pip install repowise
```

Check it with `repowise --version`. Every LLM provider SDK ships in the base
package, so there is nothing extra to pick at install time.

If the shell says `command not found`, the install directory is not on `PATH`:
run `uv tool update-shell` or `pipx ensurepath` and open a new shell.

### 2. Index

From the repository root:

```bash
repowise init                     # interactive
repowise init --yes --no-prose    # scripted: no questions, no key, no spend
```

Bare `repowise init` scans the repo and asks how to index it: **Everything**
(model-written docs), **No prose** (docs rendered from the code's structure, no
key) or **Advanced** (every indexing and generation option). It shows a cost
estimate and waits for your confirmation before any model call.

`--yes` never prompts. Pair it with `--no-prose` in scripts, CI and agent
setups: `--no-prose` keeps the run keyless even when an API key happens to be
set in the environment. Without it, `--yes` writes model prose whenever a key
is available, with the cost pre-approved.

The keyless run is the one to start with. It parses every file, builds the
dependency graph, reads git history, scores code health, finds dead code, and
renders a complete wiki from that structure. A large repository takes a few
minutes.

### 3. Connect your agent

Unless you pass `--no-editor-setup` (or set `REPOWISE_SKIP_EDITOR_SETUP=1`),
`init` wires these up for you, with or without `--yes`:

| What | Where |
|---|---|
| MCP server for Claude Code and other clients that read it | `.mcp.json` at the repo root |
| Claude Code context file | `.claude/CLAUDE.md` |
| VS Code MCP entry | `.vscode/mcp.json` |
| Claude Code and Claude Desktop MCP registration | `~/.claude/settings.json` and the Claude Desktop config |
| Claude Code hooks (session briefing, post-edit context) | `~/.claude/settings.json` |
| Post-commit hook that runs `repowise update` | the repo's git `post-commit` hook |

`--no-editor-setup` skips every row: only `.repowise/` is written.
`--no-hook` skips just the post-commit hook. The command-rewrite hook for
[Distill](../agent/DISTILL.md) is offered as a question in an interactive run
and is left off under `--yes` unless you pass `--distill-hook`.

Other hosts need one more command, run after `init`:

| Host | Command |
|---|---|
| Claude Code | nothing more |
| VS Code (Copilot) | nothing more; the [extension](../agent/VSCODE.md) adds editor features |
| Codex | `repowise agents add --target=codex --yes`, or add `--codex` to `init` (writes `.codex/config.toml`, `.codex/hooks.json` and `AGENTS.md`) |
| Cursor | `repowise agents add --target=cursor --yes` |
| OpenCode | `repowise agents add --target=opencode --yes` |
| Hermes | `repowise agents add --target=hermes --yes` |
| Any other MCP client | `repowise agents print-config claude-code`, then paste the entry into the client's MCP config |

`repowise agents` lists every supported host and whether it is wired.
`--scope=project` or `--scope=user` limits `agents add` to repo-local or
per-machine config. Per-host details: [Agent integrations](../agent/INTEGRATIONS.md),
[Codex](../agent/CODEX.md), [OpenCode](../agent/OPENCODE.md),
[Hermes](../agent/HERMES.md).

On Claude Code, the plugin adds slash commands and skills on top:

```text
/plugin marketplace add repowise-dev/repowise
/plugin install repowise@repowise
```

**Restart the host.** Most agents read MCP config only at startup. Claude Code
may ask once to approve the project's `repowise` server; `/mcp` shows its
status. Then ask the agent to call `get_overview`. An architecture summary back
means the setup works.

## First questions to ask

Ask in plain language. The agent picks the tool.

| Ask | Tool it reaches for |
|---|---|
| "Give me a tour of this codebase." | `get_overview` |
| "What breaks if I change `src/auth.py`?" | `get_context` with callers, `get_risk` |
| "What code is dead here?" | `get_dead_code` |
| "What should we fix first?" | `get_health` |
| "Which tests should I run for my branch against `main`?" | `get_change_risk`, `get_risk` |

Every one of these works without an API key. What each tool returns:
[MCP Tools](../agent/MCP_TOOLS.md).

The same answers are on the command line:

```bash
repowise health            # lowest-scoring files, and why
repowise dead-code         # what nothing references
repowise risk main..HEAD   # review priority for your branch
repowise impacted-tests main..HEAD   # tests your branch's changes exercise
```

## Open the dashboard

```bash
repowise serve
```

The API starts on `http://localhost:7337` and the dashboard on
`http://localhost:3000`. The dashboard needs Node.js 20+; it downloads once
(about 50 MB) and is cached in `~/.repowise/web/`. `--no-ui` starts the API
alone, and the [Docker image](../../docker/README.md) avoids installing Node.
View by view: [Dashboard](DASHBOARD.md).

## Optional upgrades

**Model-written docs.** A keyless index renders the subsystem pages from
structure. `repowise generate` rewrites them as prose that explains how the
code fits together, reusing the index you already have. Put the key in
`.repowise/.env` (gitignored; loaded by `generate`, `update` and the MCP
server), preview, then write:

```bash
repowise generate --provider anthropic --dry-run   # cost estimate only
repowise generate --provider anthropic             # asks before spending
```

`--path <dir>` or `--page <id>` writes a subset. Providers that need no key:
`ollama` (local), `claude_cli` and `codex_cli` (your subscription login),
`opencode`. Full list and key variables: [Config](../reference/CONFIG.md).

**Semantic search.** Full-text search works on any index. For semantic search,
configure an embedder (`REPOWISE_EMBEDDER`, or `--embedder` on `init`) and run
`repowise reindex`.

**Resuming.** If a run was interrupted, or a provider outage left some pages
missing, `repowise init --resume` writes only the pages that are absent.

## Keeping it fresh

The post-commit hook `init` installed runs `repowise update` after every
commit, so there is usually nothing to do. Other options:

```bash
repowise update     # incremental catch-up, by hand
repowise watch      # update continuously while you edit
repowise status     # what is indexed and how far behind HEAD
```

`repowise hook uninstall` removes the post-commit hook. Webhooks, polling and
workspace-wide sync: [Auto-Sync](../scale/AUTO_SYNC.md). A new `git worktree`
seeds its index from the main checkout: [Worktrees](../scale/WORKTREES.md).

## Where to go next

| Goal | Read |
|---|---|
| Everyday workflows: before a PR, in CI, changing provider | [User Guide](USER_GUIDE.md) |
| What each MCP tool answers | [MCP Tools](../agent/MCP_TOOLS.md) |
| Context that arrives without the agent asking | [Hooks](../agent/HOOKS.md) |
| Fewer tokens on test and build output | [Distill](../agent/DISTILL.md) |
| What the health score measures | [Code Health](../layers/CODE_HEALTH.md) |
| Unused files and exports | [Dead Code](../layers/DEAD_CODE.md) |
| Review priority for a change | [Change Risk](../layers/CHANGE_RISK.md) |
| Gates and annotations on pull requests | [CI](CI.md) |
| Several repositories as one | [Workspaces](../scale/WORKSPACES.md) |
| Every setting and environment variable | [Config](../reference/CONFIG.md) |
| Something is not working | [Troubleshooting](TROUBLESHOOTING.md) |

## Troubleshooting

Start with `repowise doctor`. It checks the install, API keys, index drift,
store health and each wired agent's MCP entry.

**`repowise: command not found` after install.** Run `uv tool update-shell`
(uv) or `pipx ensurepath` (pipx), open a new shell, and restart the agent
host, which starts `repowise` by name.

**The agent does not see the repowise tools.** Restart the host first. If they
are still missing, run `repowise doctor --repair`, which re-registers a stuck
Claude Code MCP entry and refreshes every wired agent's config. Failing that,
re-run `repowise agents add --target=<id> --yes`.

**The agent answers from old code.** `repowise status` shows drift and
`repowise update` catches up.

More, including large repositories, Windows encoding, empty results and
removing repowise: [Troubleshooting](TROUBLESHOOTING.md).
