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

## 2. Index your repo, with no API key

```bash
cd /path/to/your-repo
repowise init --index-only -y
```

This is the step worth doing first, because it costs nothing and answers the
question "is this useful on my codebase". It parses every file to an AST, builds
the dependency graph, reads your git history, scores every file for code health,
and finds dead code. It also renders a complete wiki from that structure: file,
module, layer and cycle pages, the architecture diagram, the repo overview, API
and infra pages, and the onboarding collection. No LLM calls, no key, no network.

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

The plugin wires up the MCP server, the hooks and the slash commands together:

```text
/plugin marketplace add repowise-dev/repowise
/plugin install repowise@repowise
```

Or wire just the MCP server:

```bash
claude mcp add repowise -- repowise mcp
```

Or commit a project `.mcp.json` so your whole team gets it:

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

<details><summary><b>Cursor, Cline, Windsurf, and other MCP clients</b></summary>

Point the client at `repowise mcp`, run from the repo directory, over stdio:

```bash
repowise mcp --transport stdio
```
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
> *"Score my branch with `get_change_risk` for `main..HEAD`."*

You should get a graph-grounded answer immediately, instead of a run of greps and
file reads. That is the whole point.

> **What works without a key:** `get_overview`, `get_context`, `get_symbol`,
> `get_risk`, `get_change_risk`, `get_dead_code` and `get_health` all synthesize
> from the graph, git and health layers. `search_codebase` and `get_answer` read
> the wiki, which step 2 already built, so they answer from pages rendered from
> structure. `search_codebase` is full-text only until you configure an embedder.

## 4. Optional: add a provider for model-written prose and semantic search

Everything so far was deterministic. A provider rewrites the wiki pages as prose
and adds semantic search, architectural decision mining, and codebase chat.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."        # or OPENAI_API_KEY / GEMINI_API_KEY
repowise init --provider anthropic
```

On Windows PowerShell: `$env:ANTHROPIC_API_KEY = "sk-ant-..."`

`repowise init` on its own is interactive: it asks which provider to use, shows a
cost estimate, and waits for you to confirm before spending anything. How long it
runs and what it costs depend on repo size and the model you pick, both of which
the estimate shows you up front.

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
automatically, with each rewrite shown to you for approval.
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

## Environment variables

| Variable | When needed | Description |
|----------|-------------|-------------|
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
