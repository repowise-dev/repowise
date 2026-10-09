# User Guide

How repowise fits into a normal working week: what to run, when to run it, and
what to do when something looks wrong.

This guide is deliberately not a flag reference. When you need the exact options
for a command, [CLI Reference](../reference/CLI_REFERENCE.md) has every one of
them, and it is the file that stays in sync with the code.

| If you want | Go to |
|---|---|
| To get running in five minutes | [Quickstart](QUICKSTART.md) |
| To fix something that is not working | [Troubleshooting](TROUBLESHOOTING.md) |
| Every command and flag | [CLI Reference](../reference/CLI_REFERENCE.md) |
| Every config key and env var | [Config](../reference/CONFIG.md) |
| What each MCP tool answers | [MCP Tools](../agent/MCP_TOOLS.md) |
| The web dashboard, view by view | [Dashboard](DASHBOARD.md) |
| What a metric actually means | [Glossary](../reference/COMPUTED_GLOSSARY.md) |

## Table of contents

1. [Installation](#installation)
2. [The mental model](#the-mental-model)
3. [What gets created](#what-gets-created)
4. [The commands you will actually use](#the-commands-you-will-actually-use)
5. [Working with your agent](#working-with-your-agent)
6. [Keeping the index fresh](#keeping-the-index-fresh)
7. [Spending fewer tokens](#spending-fewer-tokens)
8. [The dashboard](#the-dashboard)
9. [Common workflows](#common-workflows)
10. [Troubleshooting](#troubleshooting)

## Installation

Install with `uv tool install repowise`, `pipx install repowise` or
`pip install repowise` (Python 3.11+ and Git). Every LLM provider SDK ships in
the base package. Step by step, including letting your agent do it:
[Quickstart](QUICKSTART.md).

Two optional extras exist, and neither is about providers:

```bash
pip install "repowise[postgres]"     # PostgreSQL + pgvector instead of SQLite
pip install "repowise[graph-extra]"  # optional graph algorithms via graspologic
```

For local development against a clone:

```bash
git clone https://github.com/repowise-dev/repowise.git
cd repowise
uv sync --all-packages
uv run repowise --version
```

## The mental model

Three things happen, and only the first one is slow:

1. **Index.** `repowise init` parses every file to an AST, builds the dependency
   graph, reads git history, scores code health and renders the wiki. With a
   provider, a model also writes the subsystem pages. This is the one slow step.
2. **Ask.** Everything after that is a read against the index: your agent through
   [MCP tools](../agent/MCP_TOOLS.md), you through the CLI or the dashboard.
   Nothing re-analyzes anything.
3. **Keep fresh.** `repowise update` catches the index up incrementally, in
   seconds. Automate it once and forget it.

The failure mode to avoid is a stale index, because a confidently wrong answer is
worse than no answer. Every MCP response carries the indexed commit and warns when
it has diverged from your live `HEAD`, but the real fix is step 3.

**Two modes, one upgrade path.** `--no-prose` gives you the graph, git
intelligence, code health, change risk and dead code, plus a complete wiki
rendered from the code's structure, with no LLM, no key and no network. Adding a
provider rewrites the subsystem pages as model-written prose and unlocks
decision mining and chat. Start keyless and upgrade later with
`repowise generate`, which reuses the stored graph; see
[Writing the docs with a model](#writing-the-docs-with-a-model). Semantic
search is separate: it needs an embedder, and `repowise reindex` builds the
vector store.

## What gets created

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

Outside the tree, `init` also registers the MCP server with Claude Code
(`~/.claude/settings.json`) and Claude Desktop, adds the Claude Code
PostToolUse and SessionStart hooks, and installs a git post-commit hook that
runs `repowise update`.

`--no-editor-setup` (or `REPOWISE_SKIP_EDITOR_SETUP=1`) skips all of it, the
project-local files and the machine-wide registration alike: only `.repowise/`
is written, and `repowise mcp .` prints the config to connect a client by hand.
`--no-hook` skips only the post-commit hook.

`.repowise/` is safe to delete and rebuild, and safe to gitignore. Committing it
is a reasonable choice for a team that wants everyone on the same index without
each person paying to generate it.

## The commands you will actually use

Grouped by what you are trying to do. Every flag for every command lives in the
[CLI Reference](../reference/CLI_REFERENCE.md).

**Index and keep it current**

| Command | What it is for |
|---|---|
| `repowise init` | First index, and the one command to start with. Bare `init` scans the repo and asks how to index it: everything, no prose, or advanced (every indexing and generation knob). Nothing is spent before an estimate is shown and confirmed. `--yes --no-prose` skips the questions and stays keyless, for scripts and agents. |
| `repowise generate` | Write wiki pages with a model, on demand. The upgrade path for a keyless repo: `--unwritten` (default) writes everything still on a template, `--path`/`--page` writes a subset, all behind a cost estimate. |
| `repowise update` | Incremental catch-up after pulling or committing. Seconds, not minutes. |
| `repowise watch` | File watcher that updates continuously while you work. |
| `repowise hook install` | Restore the post-commit hook `init` installs, if you removed or skipped it. |
| `repowise status` | What is indexed, and how far behind it is. |
| `repowise doctor` | Checks install, keys, index drift, store health. `--repair` fixes what it safely can. |

**Ask questions**

| Command | What it is for |
|---|---|
| `repowise search "<q>"` | Search the wiki. `--mode fulltext\|semantic\|symbol`. |
| `repowise ask "<q>"` | A synthesized answer with citations and a confidence rating. Costs an LLM call. |
| `repowise context <files>` | Triage card per file: layer, hotspot, bug-fix history, doc freshness. |
| `repowise symbol "<file>::<Name>"` | One symbol's body with live-verified line bounds. |
| `repowise why "<q>"` | Decisions and rationale behind the shape of the code. A path gets its origin story; no argument gets the decision health dashboard. |
| `repowise health` | Lowest-scoring files and why. `--trend` for direction, `--refactoring-targets` for concrete plans. |
| `repowise risk main..HEAD` | Repo-relative review percentile/classification plus a supporting 0-10 diff-shape score. |
| `repowise dead-code` | What nothing references any more, by confidence tier. |
| `repowise doc-drift` | Documentation whose claims about the tree no longer hold, by confidence. |
| `repowise decision list` | Architectural decisions, their evidence and status. |
| `repowise impacted-tests` | Only the tests a diff actually exercises. |

**Serve and connect**

| Command | What it is for |
|---|---|
| `repowise serve` | API, web dashboard and MCP server together. `--no-ui` for the API alone. |
| `repowise mcp` | MCP server on stdio, for editors and agents. |

**Spend fewer tokens**

| Command | What it is for |
|---|---|
| `repowise distill <cmd>` | Run a command, compress its output before the agent reads it. |
| `repowise expand <ref>` | Recover anything distill omitted. |
| `repowise saved` | Tokens and dollars saved so far. |

**More than one repo**

| Command | What it is for |
|---|---|
| `repowise workspace list` | Repos in the workspace and their status. |
| `repowise workspace add <path>` | Add a repo. |
| `repowise update --workspace` | Update every stale repo in one pass. |

**Occasional maintenance**

| Command | What it is for |
|---|---|
| `repowise reindex` | Rebuild the vector store from existing pages (embedding calls only, no LLM). |
| `repowise restyle` | Re-render the wiki in a different [style](../layers/WIKI.md#styles). |
| `repowise export` | Export the wiki, for static hosting or archival. |
| `repowise costs` | What indexing has cost you, by provider and operation. |
| `repowise generate-claude-md` | Regenerate `CLAUDE.md` / `AGENTS.md` on demand. |
| `repowise telemetry disable` | Turn off anonymous usage telemetry. |
| `repowise uninstall` | Remove what repowise wrote from this repo, and optionally this machine. It lists everything first, and says what it left and why. |

## Working with your agent

This is the main event, and it has its own docs. The short version:

**Connect once.** `repowise init` wires Claude Code (MCP server and hooks) and
VS Code itself. Codex needs `--codex` on `init`; Cursor, OpenCode and Hermes
need `repowise agents add --target=<id>`. The Claude Code plugin is optional
and adds slash commands and skills. The per-host table is in
[Quickstart](QUICKSTART.md#3-connect-your-agent); [Codex](../agent/CODEX.md),
[OpenCode](../agent/OPENCODE.md) and [Hermes](../agent/HERMES.md) have their
own guides.

**Ten tools, task-shaped.** Your agent gets architecture summaries, per-file
triage cards with callers and ownership, symbol source with exact bounds, risk
assessment for a set of changed files, decision lookups and health scores, each in
one call, not a chain of greps. What each one answers, and worked multi-tool
examples: [MCP Tools](../agent/MCP_TOOLS.md).

**Context that arrives unasked.** Hooks push the relevant thing into the session
at the right moment: a briefing at session start, the governing decision when your
agent edits a file that decision covers, a warning on files with a run of recent
bug fixes. They never call an LLM or the network, and they fail silently.
Inventory and exact settings: [Hooks](../agent/HOOKS.md).

**Agents that do not speak MCP** still benefit, because `init` generates
`CLAUDE.md` and `AGENTS.md` from the real index.

## Keeping the index fresh

`init` installs the post-commit hook by default, so most repos need nothing
more. The options, in rough order of how little thought they need. Full guide:
[Auto-Sync](../scale/AUTO_SYNC.md).

| Method | Command | Best for |
|--------|---------|----------|
| Post-commit hook | installed by `init`; `repowise hook install` restores it | Set-and-forget local dev |
| File watcher | `repowise watch` | Active development sessions |
| GitHub webhook | Server endpoint | Teams, CI/CD |
| GitLab webhook | Server endpoint | Teams, CI/CD |
| Polling fallback | Automatic with `repowise serve` | Safety net |

Both the hook and the watcher take `--workspace` to cover every repo at once.

Working in a `git worktree`? A new worktree seeds its index from your main
checkout on the first `init` or `update`, so there is no second full index and
nothing to configure. See [Worktrees](../scale/WORKTREES.md).

## Spending fewer tokens

Most of an agent's context goes to command output it never needed: 300 lines of
passing tests around 4 failures, a full `git log` for "what changed recently".

```bash
repowise distill pytest -x       # errors first, exit code preserved
repowise distill git log -50     # subjects and counts instead of full bodies
```

Nothing is lost. Omissions leave a `[repowise#<ref>: N lines omitted]` marker;
`repowise expand <ref>` returns the full output, and `-q <regex>` filters it.

**Getting your agent to use it.** `repowise init` adds a section to the managed
`CLAUDE.md` so the agent reaches for it voluntarily, which works in any agent that
runs shell commands. For Claude Code you can also opt into the command-rewrite
hook, which rewrites noisy commands automatically:

```bash
repowise hook rewrite install    # or answer Yes at the init prompt
```

It never rewrites compound commands, redirections or watch modes.

Track it with `repowise saved`, or the Costs page in the dashboard. Full guide:
[Distill](../agent/DISTILL.md).

## The dashboard

```bash
repowise serve
```

API on `http://localhost:7337`, dashboard on `http://localhost:3000`. Node.js
20+, `--no-ui` and the Docker image are covered in
[Quickstart](QUICKSTART.md#open-the-dashboard).

`Ctrl+K` / `Cmd+K` opens a command palette from any page, which is the fastest way
to move between views and repos.

An index-only repo has a full Docs section rendered from structure; `repowise
generate` rewrites any of it as model prose later. Chat needs a provider.
Everything else works off the parsed graph and git history alone.

Every view and what it answers: **[Dashboard](DASHBOARD.md)**.

## Common workflows

### Writing the docs with a model

First-time setup is in the [Quickstart](QUICKSTART.md). When you want the
subsystem pages written as prose, all at once or a piece at a time, each run
behind a cost estimate, put the key in `.repowise/.env` and run:

```bash
repowise generate                  # the unwritten subsystem pages, behind one estimate
repowise generate --path src/api   # or one area first
repowise generate --stale          # refresh pages the last update marked stale
repowise generate --all            # or rewrite every page
```

`repowise update --full` does the whole wiki in one pass instead. Add semantic
search with `repowise reindex` once an embedder is configured.

### First index, multi-repo workspace

```bash
cd /path/to/workspace/     # parent dir containing backend/, frontend/, ...
repowise init .            # finds the repos, asks which to index
```

The post-commit hook choice applies to every selected repo.

Cross-repo contracts and co-change come out of this automatically. See
[Workspaces](../scale/WORKSPACES.md).

### Day to day

The post-commit hook `init` installed keeps the index current on every commit.
For changes you pull, or if you skipped the hook:

```bash
git pull && repowise update   # catch up by hand
repowise watch                # or sync continuously while you code
```

### Before you open a pull request

```bash
repowise risk main..HEAD       # repo-relative review priority and supporting evidence
repowise impacted-tests main..HEAD   # the tests your changes exercise
repowise health --trend        # did anything you touched get worse
```

`get_risk` in PR mode also shows structural dependency reach (review candidates,
not proven runtime breakage), companion files that historically move with the
ones you touched, and where test evidence is missing.

### Reviewing someone else's pull request

```bash
repowise risk origin/main..their-branch
repowise health --file path/to/the/scariest/file.py
```

To run the same checks on every pull request, with gates, annotations and
SARIF, use the GitHub Action: see [CI](CI.md).

### Onboarding someone new

Index with a provider so they get the wiki, then hand them either the dashboard or
their editor:

```bash
repowise init --provider anthropic
repowise serve                 # browsable wiki, graph, health
```

With the MCP server connected, their agent can answer "why is this like this"
instead of them interrupting someone.

### In CI

```bash
export REPOWISE_SKIP_EDITOR_SETUP=1     # no hooks or editor files on the runner
repowise init --yes --no-prose          # free, no keys in CI, no questions
repowise risk "$BASE_SHA..$HEAD_SHA"    # gate or annotate on review priority
repowise export --format markdown --output ./docs/wiki/   # static hosting
```

To gate a pull request on the coverage of the lines it changed, run the tests
with a coverage report and add:

```bash
repowise coverage check "origin/$BASE_BRANCH...HEAD" --report coverage/lcov.info --fail-under 80
```

It needs no index and no key, only git and the report. A shallow clone needs
full history for the merge-base. Exit `1` means below the gate, `2` means it could not
run. See [Patch coverage in CI](../layers/TEST_INTELLIGENCE.md#patch-coverage-in-ci).

### Changing provider or model

```bash
repowise init --provider openai --model gpt-5.6-luna --force   # regenerate
repowise update --provider gemini                              # just future updates
```

Or edit `provider` / `model` in `.repowise/config.yaml`. The parse, graph, git and
health layers are provider-independent, so switching only affects generated prose.

### Cutting cost on a large repo

`repowise init --dry-run` shows the estimate before anything is spent; more
levers, including memory on very large repositories, are in
[Troubleshooting](TROUBLESHOOTING.md#indexing).

## Troubleshooting

Start with `repowise doctor`, and `repowise doctor --repair` to fix what it
safely can. Install problems, interrupted runs, very large repositories,
empty results and semantic search are covered in
[Troubleshooting](TROUBLESHOOTING.md).

## Where to go next

- **[MCP Tools](../agent/MCP_TOOLS.md)** for what your agent can actually ask
- **[Code Health](../layers/CODE_HEALTH.md)** for what the score measures and how it is validated
- **[Workspaces](../scale/WORKSPACES.md)** for multi-repo intelligence
- **[Config](../reference/CONFIG.md)** for every setting and environment variable
- **[CLI Reference](../reference/CLI_REFERENCE.md)** for every command and flag
- **[Architecture](../architecture/ARCHITECTURE.md)** for how repowise is built
