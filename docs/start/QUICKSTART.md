# Quickstart

Index your repo and get your agent answering questions from it. The first three
steps need no API key and no configuration.

> Already indexed and looking for a specific command? See the
> [CLI Reference](../reference/CLI_REFERENCE.md). For everything else, the
> [User Guide](USER_GUIDE.md).

---

## 1. Install

```bash
pip install repowise
```

That is the whole install. Every LLM provider SDK (Anthropic, OpenAI, Gemini,
LiteLLM) ships in the base package, so there is nothing to pick at install time
and no extras to remember. You choose a provider later, at index time, and you
can change it whenever you want.

**Requirements:** Python 3.11+ and Git. On Windows use `python -m pip install repowise`.

Check it landed:

```bash
repowise --version
```

## 2. Index your repo

```bash
cd /path/to/your-repo
repowise init
```

Bare `init` scans the repo and asks how to index it: everything (the wiki written
by a model), no prose (the same wiki rendered from your code's structure, no key
and no spend), or advanced, which walks through the indexing and generation knobs.
Nothing is spent before you see an estimate and confirm it, so it is safe to run
and read.

If you would rather not answer questions, name the mode and add `--yes`. This is
the form to use in a script, in CI, or when an agent is setting repowise up:

```bash
repowise init --yes --no-prose   # free, no key, no questions
repowise init --yes --prose      # model-written subsystem pages, cost pre-approved
```

Bare `repowise init --yes` needs no API key either: without a resolvable provider
it renders the whole wiki from structure.

The keyless run is the one worth doing first, because it costs nothing and answers
the question "is this useful on my codebase". It parses every file to an AST, builds
the dependency graph, reads your git history, scores every file for code health,
and finds dead code. It also renders a complete wiki from that structure: file,
symbol, layer and cycle pages, the architecture diagram, the repo overview, API
and infra pages, and the onboarding collection. Without a key the subsystem
(concept) pages, the repo overview, the architecture diagram and the onboarding
collection are structural stubs until you point a model at them. No LLM calls,
no key, no network.

Those pages are honest about where they came from. Each one ends with a footer
saying it was derived from structure, and the repo overview covers composition,
entry points, clusters and dependencies rather than what the project does end to
end, because no template can derive that.

When it finishes you have a working index. Try it:

```bash
repowise health          # the lowest-scoring files, and why
repowise dead-code       # what nothing references any more
repowise risk HEAD~5..HEAD   # how risky your recent work looks
```

`repowise health` ends with a self-check against your own git history, something
like *"16 of the 20 lowest-health files had a bug fix in the last 6 months, 3.3x
the 24% baseline"*. That number is computed on your repo, not ours. If it looks
bad, the score is not working for your codebase and you should say so.

## 3. Connect your agent

This is the payoff: your agent reads the index instead of your codebase.

<details open><summary><b>Claude Code</b></summary>

**`repowise init` already did this.** It writes a repo-root `.mcp.json`
unconditionally, and unless you passed `--no-editor-setup` or set
`REPOWISE_SKIP_EDITOR_SETUP=1`, it also registers repowise with
`~/.claude/settings.json`. A Claude Code session opened in this repo already
sees the MCP server. Check with `repowise agents` (Claude Code should show as
wired) or `repowise doctor`.

Skipped editor setup, or setting up a machine where you did? Wire it up now:

```bash
repowise agents add --target=claude-code
```

**The plugin** additionally installs hooks and slash commands, which `init`
never writes because the plugin route is host-managed and only the plugin can
install it:

```text
/plugin marketplace add repowise-dev/repowise
/plugin install repowise@repowise
```

**A different MCP client, or wiring the server by hand:**

```bash
claude mcp add repowise -- repowise mcp
```

Or edit the project `.mcp.json` `init` already wrote (commit it if your team
should share it):

```json
{ "mcpServers": { "repowise": { "command": "repowise", "args": ["mcp"] } } }
```
</details>

<details><summary><b>Codex CLI</b></summary>

```bash
codex mcp add repowise -- repowise mcp
```

Or add to `~/.codex/config.toml`:

```toml
[mcp_servers.repowise]
command = "repowise"
args = ["mcp"]
```

`repowise init --codex` writes project-local `.codex/config.toml`,
`.codex/hooks.json` and a managed `AGENTS.md`. See
[Codex integration](../agent/CODEX.md).
</details>

<details><summary><b>Cursor</b></summary>

```bash
repowise agents add --target=cursor
```

Writes project-local `.cursor/mcp.json` and a managed `.cursor/rules/repowise.mdc`.
Cursor does not read `.vscode/mcp.json`, so this is separate from the VS Code
setup below. See [Agent integrations](../agent/INTEGRATIONS.md).
</details>

<details><summary><b>OpenCode</b></summary>

```bash
repowise agents add --target=opencode                  # this repo and this machine
repowise agents add --target=opencode --scope=project  # this repo only
```

Writes `opencode.jsonc` (or an existing `opencode.json`) and a managed section in
`AGENTS.md`, in both scopes by default. The repo-local pair sits at the repo root;
the per-machine pair goes to `$XDG_CONFIG_HOME/opencode`, falling back to
`~/.config/opencode`. That path is the same on Windows: OpenCode does not read
`%APPDATA%`. The repo-local entry names its repo outright, while the per-machine one
resolves whichever repo OpenCode was launched in.

OpenCode accepts comments in its config. Repowise does not rewrite a file it cannot
parse as strict JSON, so if yours has comments it prints the entry to paste instead:
`repowise agents print-config opencode`. See
[Agent integrations](../agent/INTEGRATIONS.md).
</details>

<details><summary><b>Hermes</b></summary>

```bash
repowise agents add --target=hermes                  # this repo and this machine
repowise agents add --target=hermes --scope=user     # the MCP server only
```

Hermes reads one `config.yaml` per machine, so the MCP server is registered there
and serves every repo: `%LOCALAPPDATA%\hermes\config.yaml` on Windows,
`~/.hermes/config.yaml` elsewhere, or `$HERMES_HOME/config.yaml` when that is set.
Project scope writes a managed section in `AGENTS.md`, which Hermes loads per repo.

Repowise edits that config in place rather than rewriting it, so your comments, key
order and any anchors survive. If it cannot parse the file it leaves it alone and
prints the entry to paste instead: `repowise agents print-config hermes`.

`platform_toolsets.cli` is deliberately left alone. Hermes exposes every enabled MCP
server to the CLI by default, and that list only becomes an allowlist once it already
names one. So repowise adds itself there **only** when the list is already an
allowlist, and never converts a permissive config into a restrictive one. See
[Hermes](../agent/HERMES.md).
</details>

<details><summary><b>Cline, Windsurf, and other MCP clients</b></summary>

Print the server entry and paste it into whatever config the host reads:

```bash
repowise agents print-config claude-code
```

Or point the client at `repowise mcp`, run from the repo directory, over stdio.
</details>

<details><summary><b>VS Code</b></summary>

Install the **Repowise** extension from the Marketplace or Open VSX, then run
**Repowise: Set Up This Repository**. It registers the MCP server with VS Code
too, so the same index serves you and your agent. See [VS Code](../agent/VSCODE.md).
</details>

**Now ask your agent something it would normally grep for:**

> *"Use repowise `get_overview` to summarize this repo."*
>
> *"What's the blast radius if I change `src/auth.py`? Use `get_context` with
> `include: ["callers"]`."*
>
> *"Review my branch with `get_change_risk` for `main..HEAD`."*

You should get a graph-grounded answer immediately, instead of a run of greps and
file reads. That is the whole point.

> **What works without a key:** `get_overview`, `get_context`, `get_symbol`,
> `get_risk`, `get_change_risk`, `get_dead_code` and `get_health` all synthesize
> from the graph, git and health layers. `search_codebase` and `get_answer` read
> the wiki, which step 2 already built, so they answer from pages rendered from
> structure. `search_codebase` is full-text only until you configure an embedder.

## 4. Optional: write the subsystem pages as model prose

Every file page was already deterministic, and stays that way. The one layer a
model adds is the subsystem (concept) tree: the numbered pages that describe how
the codebase fits together above the file level. Without a key they are
structural stubs; `repowise generate` fills them with prose (and unlocks
architectural decision mining and chat). You do not have to redo anything: it
reuses the index you already built, so the graph is not re-parsed.

Set a key, preview the cost, then write:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."        # or OPENAI_API_KEY / GEMINI_API_KEY

repowise generate                  # shows the wiki state, then writes the unwritten subsystem pages
```

Bare `generate` prints the wiki's state and writes every unwritten subsystem
page behind a single cost estimate. On Windows PowerShell:
`$env:ANTHROPIC_API_KEY = "sk-ant-..."`

You do not have to write them all at once. Restrict to an area, a single page, or
refresh what is stale, each run behind its own cost estimate:

```bash
repowise generate --path src/api                    # just the subsystem pages under one area
repowise generate --page module_page:src/api        # or a single subsystem page
repowise generate --stale                            # refresh pages the last update marked stale
repowise generate --all                              # or rewrite every page, structural ones included
```

Semantic search is a separate step: configure an embedder and run `repowise
reindex` to build the vector store.

Prefer to write the whole wiki as part of a fresh index instead? `repowise init`
on its own is interactive: it asks which provider to use, shows a cost estimate,
and waits for you to confirm before spending anything.

Three ways to avoid paying a provider at all:

- **Codex subscription:** `repowise init --provider codex_cli` uses your existing
  Codex CLI login, no API key. Run `codex login` first.
- **Fully local:** point it at Ollama with a local embedding model for zero
  external calls. See [Config](../reference/CONFIG.md).
- **Stay in index-only mode.** The graph, git, health, risk and dead-code layers
  never needed a provider, and the wiki you already have was rendered without
  one. Full-text search works on it; semantic search is the part that needs an
  embedder, and Ollama is the keyless one.

## 5. Keep it in sync

An index that drifts is worse than no index, because your agent will not know it
is stale. Every response carries the indexed commit and warns when it diverges
from your live `HEAD`, but the fix is cheap:

```bash
repowise update          # incremental, seconds
```

Better, make it automatic:

```bash
repowise hook install    # re-index on every commit
repowise watch           # or run a file watcher while you work
```

Working with `git worktree`? A new worktree seeds its index from your main
checkout on the first `init` or `update`, so there is no second full index and
nothing to configure. See [Worktrees](../scale/WORKTREES.md).

All the sync options (hooks, watcher, GitHub/GitLab webhooks, polling):
[Auto-Sync](../scale/AUTO_SYNC.md).

---

## See it

```bash
repowise serve
```

Starts the API on `http://localhost:7337` and the web dashboard on
`http://localhost:3000`, alongside the MCP server. If Node.js 20+ is installed
the dashboard starts automatically; the frontend downloads once (~50 MB) and
caches in `~/.repowise/web/`. Use `repowise serve --no-ui` for the API alone, or
run the [Docker image](../../docker/README.md) if you would rather not install Node.

Every view and what it answers: [Dashboard](DASHBOARD.md).

## Spend fewer tokens on command output

```bash
repowise distill pytest -x   # errors first, raw output recoverable via `repowise expand`
repowise saved               # tokens and dollars saved so far
```

Distill compresses noisy command output before your agent reads it, 60-90% fewer
tokens on noisy commands with no error lines dropped. Opt into the rewrite hook
during `init` (or `repowise hook rewrite install`) to have it applied
automatically. Rewrites run without a prompt and only ever wrap a recognized
command; set `permission: ask` to review each one.
See [Distill](../agent/DISTILL.md).

## More than one repo

If your project spans several repositories, index the parent directory instead:

```bash
cd my-workspace/         # contains backend/, frontend/, shared-libs/
repowise init .
```

repowise finds the git repos, asks which to index, and then runs the analysis that
only makes sense across repos: co-change pairs, API contracts between a producer
and its consumers, and package dependencies. One MCP server serves all of them.

```bash
repowise workspace list              # repos and their status
repowise workspace add ../new-svc    # add one
repowise update --workspace          # update every stale repo
```

Full guide: [Workspaces](../scale/WORKSPACES.md).

---

## If something looks wrong

```bash
repowise doctor          # checks install, API keys, index drift, store health
repowise doctor --repair # fixes what it safely can
repowise status          # what is indexed, and how stale it is
```

## Taking it back out

```bash
repowise uninstall             # list what repowise wrote, then ask what to remove
repowise uninstall --all       # everything: wiring, index, and machine-wide state
repowise uninstall --dry-run   # the same list, changing nothing
```

It reports every path it removed and every path it left, with the reason on the
row. The index is not selected by default, because rebuilding it is the one
expensive thing here. Full flags in the
[CLI Reference](../reference/CLI_REFERENCE.md#repowise-uninstall-path).

## Environment variables

None of these are required. Every one of them is only needed for a model-written
wiki.

| Variable | When needed | Description |
|----------|-------------|-------------|
| `REPOWISE_PROVIDER` | Optional | Provider name. An empty value is treated as unset. |
| `ANTHROPIC_API_KEY` | Using Anthropic | Anthropic API key |
| `OPENAI_API_KEY` | Using OpenAI | OpenAI API key |
| `GEMINI_API_KEY` | Using Gemini | Google Gemini API key |
| `REPOWISE_EMBEDDER` | Semantic search | Embedder: `gemini`, `openai`, or `mock` (default) |
| `REPOWISE_DB_URL` | Custom database | SQLite/PostgreSQL connection string (default: `.repowise/wiki.db`) |
| `REPOWISE_API_URL` | Frontend only | Backend URL for the web UI (default: `http://localhost:7337`) |

Full list, plus `.repowise/config.yaml`: [Config](../reference/CONFIG.md).

---

## Where to go next

- **[User Guide](USER_GUIDE.md)** for the everyday workflows
- **[MCP Tools](../agent/MCP_TOOLS.md)** for what each tool answers, with worked examples
- **[Hooks](../agent/HOOKS.md)** to have context arrive without the agent asking for it
- **[Code Health](../layers/CODE_HEALTH.md)** for what the score measures and how it is validated
- **[Dashboard](DASHBOARD.md)** for the web UI, view by view
