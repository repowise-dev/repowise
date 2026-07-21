# User Guide

How repowise fits into a normal working week: what to run, when to run it, and
what to do when something looks wrong.

This guide is deliberately not a flag reference. When you need the exact options
for a command, [CLI Reference](../reference/CLI_REFERENCE.md) has every one of
them, and it is the file that stays in sync with the code.

| If you want | Go to |
|---|---|
| To get running in five minutes | [Quickstart](QUICKSTART.md) |
| Every command and flag | [CLI Reference](../reference/CLI_REFERENCE.md) |
| Every config key and env var | [Config](../reference/CONFIG.md) |
| What each MCP tool answers | [MCP Tools](../agent/MCP_TOOLS.md) |
| The web dashboard, view by view | [Dashboard](DASHBOARD.md) |
| What a metric actually means | [Glossary](../reference/COMPUTED_GLOSSARY.md) |

---

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

---

## Installation

```bash
pip install repowise
```

That is the complete install. Every LLM provider SDK (Anthropic, OpenAI, Gemini,
and LiteLLM for 100+ others) ships in the base package, so there is no provider
extra to choose. You pick a provider at index time and can change it later.

Two extras exist, and neither is about providers:

```bash
pip install "repowise[postgres]"     # PostgreSQL + pgvector instead of SQLite
pip install "repowise[graph-extra]"  # optional graph algorithms via graspologic
```

**Requirements:** Python 3.11+ and Git. On Windows, use `python -m pip install repowise`.

For local development against a clone:

```bash
git clone https://github.com/repowise-dev/repowise.git
cd repowise
uv sync --all-packages
uv run repowise --version
```

Step-by-step first run: [Quickstart](QUICKSTART.md).

---

## The mental model

Three things happen, and only the first one is slow:

1. **Index.** `repowise init` parses every file to an AST, builds the dependency
   graph, reads git history, and scores code health. With a provider it also
   generates a wiki page per file and module. This is the one expensive step.
2. **Ask.** Everything after that is a read against the index: your agent through
   [MCP tools](../agent/MCP_TOOLS.md), you through the CLI or the dashboard.
   Nothing re-analyzes anything.
3. **Keep fresh.** `repowise update` catches the index up incrementally, in
   seconds. Automate it once and forget it.

The failure mode to avoid is a stale index, because a confidently wrong answer is
worse than no answer. Every MCP response carries the indexed commit and warns when
it has diverged from your live `HEAD`, but the real fix is step 3.

**Two modes.** `--index-only` gives you the graph, git intelligence, code health,
change risk and dead code, plus a complete wiki rendered from the code's
structure, with no LLM, no key and no network. Adding a provider rewrites those
pages as model-written prose and adds semantic search, decision mining and chat.
You can start index-only and upgrade later with `repowise update --full`, which
reuses the persisted graph instead of re-parsing it.

---

## What gets created

```
your-repo/
├── .repowise/
│   ├── wiki.db           # pages, symbols, graph, git data, health scores
│   ├── state.json        # sync metadata (last commit, pages, tokens used)
│   ├── config.yaml       # provider, model, embedder, excludes
│   ├── .env              # saved API keys (gitignored)
│   └── lancedb/          # vector store for semantic search
├── .claude/CLAUDE.md     # generated Claude Code context
├── AGENTS.md             # generated Codex context, when enabled
└── .codex/               # project-local Codex MCP/hooks config, with --codex
```

`.repowise/` is safe to delete and rebuild, and safe to gitignore. Committing it
is a reasonable choice for a team that wants everyone on the same index without
each person paying to generate it.

---

## The commands you will actually use

Grouped by what you are trying to do. Every flag for every command lives in the
[CLI Reference](../reference/CLI_REFERENCE.md).

**Index and keep it current**

| Command | What it is for |
|---|---|
| `repowise init` | First index. Interactive: asks for a provider, shows a cost estimate, waits for confirmation. `--index-only` builds the same wiki from structure, with no LLM. |
| `repowise update` | Incremental catch-up after pulling or committing. Seconds, not minutes. |
| `repowise watch` | File watcher that updates continuously while you work. |
| `repowise hook install` | Post-commit hook so syncing happens without you. |
| `repowise status` | What is indexed, and how far behind it is. |
| `repowise doctor` | Checks install, keys, index drift, store health. `--repair` fixes what it safely can. |

**Ask questions**

| Command | What it is for |
|---|---|
| `repowise search "<q>"` | Search the wiki. `--mode fulltext\|semantic\|symbol`. |
| `repowise health` | Lowest-scoring files and why. `--trend` for direction, `--refactoring-targets` for concrete plans. |
| `repowise risk main..HEAD` | Defect risk for a commit or range, scored 0-10. |
| `repowise dead-code` | What nothing references any more, by confidence tier. |
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
| `repowise restyle` | Re-render the wiki in a different [style](../layers/WIKI_STYLES.md). |
| `repowise export` | Export the wiki, for static hosting or archival. |
| `repowise costs` | What indexing has cost you, by provider and operation. |
| `repowise generate-claude-md` | Regenerate `CLAUDE.md` / `AGENTS.md` on demand. |
| `repowise telemetry disable` | Turn off anonymous usage telemetry. |

---

## Working with your agent

This is the main event, and it has its own docs. The short version:

**Connect once.** `repowise mcp`, run from the repo directory, over stdio. For
Claude Code the plugin wires the MCP server, hooks and slash commands together;
for Codex, `repowise init --codex` writes project-local config. Setup per client
is in [Quickstart](QUICKSTART.md#3-connect-your-agent), and
[Codex](../agent/CODEX.md) / [opencode](../agent/OPENCODE.md) have their own guides.

**Ten tools, task-shaped.** Your agent gets architecture summaries, per-file
triage cards with callers and ownership, symbol source with exact bounds, risk
assessment for a set of changed files, decision lookups and health scores, each in
one call rather than a chain. What each one answers, and worked multi-tool
examples: [MCP Tools](../agent/MCP_TOOLS.md).

**Context that arrives unasked.** Hooks push the relevant thing into the session
at the right moment: a briefing at session start, the governing decision when your
agent edits a file that decision covers, a warning on files with a run of recent
bug fixes. They never call an LLM or the network, and they fail silently.
Inventory and exact settings: [Hooks](../agent/HOOKS.md).

**Agents that do not speak MCP** still benefit, because `init` generates
`CLAUDE.md` and `AGENTS.md` from the real index.

---

## Keeping the index fresh

Five ways, in rough order of how little thought they need. Full guide:
[Auto-Sync](../scale/AUTO_SYNC.md).

| Method | Command | Best for |
|--------|---------|----------|
| Post-commit hook | `repowise hook install` | Set-and-forget local dev |
| File watcher | `repowise watch` | Active development sessions |
| GitHub webhook | Server endpoint | Teams, CI/CD |
| GitLab webhook | Server endpoint | Teams, CI/CD |
| Polling fallback | Automatic with `repowise serve` | Safety net |

Both the hook and the watcher take `--workspace` to cover every repo at once.

Working in a `git worktree`? A new worktree seeds its index from your main
checkout on the first `init` or `update`, so there is no second full index and
nothing to configure. See [Worktrees](../scale/WORKTREES.md).

---

## Spending fewer tokens

Most of an agent's context goes to command output it never needed: 300 lines of
passing tests around 4 failures, a full `git log` for "what changed recently".

```bash
repowise distill pytest -x       # errors first, exit code preserved
repowise distill git log -50     # subjects and counts instead of full bodies
```

Nothing is lost. Omissions leave a marker that is always recoverable:

```
[repowise#a1b2c3d4e5f6: 230 lines omitted (~6.1k tokens); restore: repowise expand a1b2c3d4e5f6]
```

```bash
repowise expand a1b2c3d4e5f6              # the full original output
repowise expand a1b2c3d4e5f6 -q "FAILED"  # just the matching lines
```

**Getting your agent to use it.** `repowise init` adds a section to the managed
`CLAUDE.md` so the agent reaches for it voluntarily, which works in any agent that
runs shell commands. For Claude Code you can also opt into the command-rewrite
hook, which rewrites noisy commands automatically:

```bash
repowise hook rewrite install    # or answer Yes at the init prompt
```

It never rewrites pipes, compound commands or watch modes, and defaults to `ask`
so you see every rewrite before it runs.

Track it with `repowise saved`, or the Costs page in the dashboard. Full guide:
[Distill](../agent/DISTILL.md).

---

## The dashboard

```bash
repowise serve
```

API on `http://localhost:7337`, dashboard on `http://localhost:3000`, MCP server
alongside both. With Node.js 20+ the frontend downloads once (~50 MB), caches in
`~/.repowise/web/` and starts automatically. Use `--no-ui` for the API alone, or
the [Docker image](../../docker/README.md) if you would rather not install Node.

`Ctrl+K` / `Cmd+K` opens a command palette from any page, which is the fastest way
to move between views and repos.

An index-only repo has a full Docs section, rendered from structure rather than
written by a model, so the pages read as structural summaries. Chat still needs a
provider. Everything else, including Architecture, Code Health, Files, Commits and
Contributors, works off the parsed graph and git history alone.

Every view and what it answers: **[Dashboard](DASHBOARD.md)**.

---

## Common workflows

### First index, single repo

```bash
pip install repowise
cd /path/to/your-project
repowise init --index-only -y      # free, no key, seconds
repowise hook install              # keep it current from here on
```

Rewrite the wiki with a model and add semantic search when you want them:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
repowise init --provider anthropic
```

### First index, multi-repo workspace

```bash
cd /path/to/workspace/     # parent dir containing backend/, frontend/, ...
repowise init .            # finds the repos, asks which to index
repowise hook install --workspace
```

Cross-repo contracts and co-change come out of this automatically. See
[Workspaces](../scale/WORKSPACES.md).

### Day to day

Pick one and stop thinking about it:

```bash
repowise hook install    # A: syncs on every commit, nothing else to do
repowise watch           # B: syncs continuously while you code
git pull && repowise update   # C: manual, when you prefer control
```

### Before you open a pull request

```bash
repowise risk main..HEAD       # how risky does this change look, and why
repowise impacted-tests        # the tests this diff actually exercises
repowise health --trend        # did anything you touched get worse
```

`repowise risk` in PR mode also tells you what a change is likely to break, which
companion files usually move with the ones you touched, and where tests are
missing.

### Reviewing someone else's pull request

```bash
repowise risk origin/main..their-branch
repowise health --file path/to/the/scariest/file.py
```

Or let the [PR bot](https://github.com/apps/repowise-bot) post it automatically on
every pull request, with no LLM calls.

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
repowise init --index-only              # free, no keys in CI
repowise risk "$BASE_SHA..$HEAD_SHA"    # gate or annotate on risk
repowise export --format markdown --output ./docs/wiki/   # static hosting
```

### Changing provider or model

```bash
repowise init --provider openai --model gpt-5.4-nano --force   # regenerate
repowise update --provider gemini                              # just future updates
```

Or edit `provider` / `model` in `.repowise/config.yaml`. The parse, graph, git and
health layers are provider-independent, so switching only affects generated prose.

### Cutting cost on a large repo

```bash
repowise init --dry-run                    # see the estimate before spending
repowise init --test-run                   # generate the top 10 files only
repowise init --skip-tests --skip-infra    # narrow the scope
repowise init --provider ollama            # or spend nothing at all
```

---

## Troubleshooting

Start with `repowise doctor`, which checks the install, API keys, index drift and
store health, and `repowise doctor --repair` to fix what it safely can.

**"Provider X requires the 'Y' package"**
Every provider SDK ships with `repowise`, so this points at a broken or partial
install rather than a missing extra. The error names the package it wants, so
`pip install <package>` clears it immediately, and
`pip install --force-reinstall repowise` fixes the underlying install.

**Empty results in semantic search mode**
An embedder is probably not configured. Set `REPOWISE_EMBEDDER=gemini` (or
`openai`) and rebuild the vector store with `repowise reindex --embedder gemini`.

**"embedder.mock_active" warning**
The mock embedder produces random vectors, so semantic search cannot work
meaningfully. Set a real embedder as above.

**Pages look stale after code changes**
`repowise update`. To stop it happening, `repowise hook install` or
`repowise watch`.

**The agent is answering from an old version of the code**
Check `repowise status` for drift. MCP responses carry the indexed commit and a
staleness warning, so if your agent is not surfacing that, it is worth asking it
to.

**Indexing cost more than expected**
Use `--dry-run` for the estimate before committing to a run, `--test-run` to
validate on 10 files, and `--skip-tests --skip-infra` to cut scope. Lower
`--concurrency` if you are hitting rate limits.

**init was interrupted**
`repowise init --resume` picks up from the last checkpoint.

**Vector store looks corrupted**
`repowise reindex` rebuilds it from the existing wiki pages, with no LLM calls.

**Doctor reports 0 pages**
init failed or was interrupted. Even `--index-only` writes pages, so an empty
wiki means the run did not finish. Try `repowise init --resume`.

**Dashboard shows an empty repo list**
The backend and frontend have to point at the same database. Check `REPOWISE_DB_URL`
on the backend and `REPOWISE_API_URL` on the frontend.

**CORS errors in the browser**
`repowise serve` runs the API and the dashboard together, so this should not come
up in normal use; the backend allows all origins by default. If you see it, the
API is probably not up, or you are running a frontend separately from source and
pointing it somewhere else with `REPOWISE_API_URL`.

---

## Where to go next

- **[MCP Tools](../agent/MCP_TOOLS.md)** for what your agent can actually ask
- **[Code Health](../layers/CODE_HEALTH.md)** for what the score measures and how it is validated
- **[Workspaces](../scale/WORKSPACES.md)** for multi-repo intelligence
- **[Config](../reference/CONFIG.md)** for every setting and environment variable
- **[CLI Reference](../reference/CLI_REFERENCE.md)** for every command and flag
- **[Architecture](../architecture/ARCHITECTURE.md)** for how repowise is built
