# Quickstart

Get repowise running on your codebase in under 5 minutes.

> For the full CLI reference, web UI docs, MCP integration, and troubleshooting, see the [User Guide](USER_GUIDE.md).

---

## 1. Install

```bash
pip install "repowise[anthropic]"
```

Or substitute `openai`, `gemini`, `litellm`, or `all` depending on your LLM provider.

For the Codex CLI subscription flow, no provider SDK or API key is required:

```bash
pip install repowise
npm install -g @openai/codex
codex login
```

**Requirements:** Python 3.11+, Git.

## 2. Set Your API Key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or `OPENAI_API_KEY`, `GEMINI_API_KEY` — whichever provider you installed.

If you use Codex as the LLM provider, authenticate the Codex CLI instead:

```bash
codex login status
```

On Windows PowerShell:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

## 3. Initialize

```bash
cd /path/to/your-repo
repowise init
```

Repowise will walk you through an interactive setup — choose a provider, review the cost estimate, and confirm. It parses every file, builds a dependency graph, indexes git history, and generates wiki pages.

Codex users can force project-local MCP/hooks setup and use the Codex CLI provider in one command:

```bash
repowise init --codex --provider codex_cli --yes
```

A typical run on a ~500-file codebase takes 5-15 minutes.

**Want to skip LLM costs?** Use `--index-only` to just parse and analyze without generating docs:

```bash
repowise init --index-only
```

## 4. Explore

Once init completes, you have several ways to use the wiki:

**Search from the terminal:**

```bash
repowise search "authentication"
repowise search "how are errors handled" --mode semantic
```

**Browse in a web UI:**

```bash
repowise serve
# API on http://localhost:7337, Web UI on http://localhost:3000
```

If Node.js 20+ is installed, the web UI starts automatically. Otherwise, use Docker (see below).

**Connect to your AI editor (Claude Code, Codex, Cursor, Cline, Windsurf):**

```bash
repowise mcp --transport stdio
```

> **Automatic for Claude Code:** `repowise init` already registers the MCP server and installs a PostToolUse hook in `~/.claude/settings.json`. Broad or zero-result `Grep`/`Glob` searches can receive graph context, and git operations can notify the agent when the wiki is stale.

> **Claude Code plugin (one-command):** install from the marketplace to get the MCP server, the hook, and `/repowise:*` slash commands together:
> ```text
> /plugin marketplace add repowise-dev/repowise
> /plugin install repowise@repowise
> ```

> **Automatic for Codex:** run `repowise init --codex` to write project-local `.codex/config.toml`, `.codex/hooks.json`, and managed `AGENTS.md`. See [Codex Integration](CODEX.md).

**Cut your agent's context spend (optional):**

```bash
repowise distill pytest -x      # compact errors-first output; raw recoverable via `repowise expand`
repowise saved                  # tokens & dollars saved so far
```

Distill compresses noisy command output (tests, builds, git, searches) before
the agent reads it — **60–90% fewer tokens on noisy commands, with zero
error-line loss** (measured on a public OSS repo). Opt into the Claude Code
rewrite hook at `init` (or `repowise hook rewrite install`) to apply it
automatically, with each rewrite shown for approval. See [Distill](DISTILL.md).

## 5. Keep It in Sync

After pulling changes or editing code:

```bash
repowise update
```

Or install a post-commit hook for automatic sync:

```bash
repowise hook install
```

Or run continuous sync while you work:

```bash
repowise watch
```

Working with `git worktree`? New worktrees seed their index from your main checkout automatically on the first `repowise init` or `repowise update`; no re-indexing. See [WORKTREES.md](WORKTREES.md).

See [Auto-Sync](AUTO_SYNC.md) for all sync methods (hooks, file watcher, GitHub/GitLab webhooks, polling).

---

## Multi-Repo Workspace

If your project spans multiple repos, initialize a workspace instead:

```bash
cd my-workspace/     # parent dir containing backend/, frontend/, shared-libs/
repowise init .      # scans for git repos, indexes each, runs cross-repo analysis
```

Repowise will scan for git repos, prompt you to select which to index, and run cross-repo analysis (co-changes, API contracts, package dependencies). The MCP server serves all repos from a single instance.

```bash
repowise workspace list              # show repos and their status
repowise workspace add ../new-svc    # add a repo
repowise update --workspace          # update all stale repos
repowise hook install --workspace    # install post-commit hooks for all repos
```

Full guide: [Workspaces](WORKSPACES.md)

---

## Web UI

Repowise includes a full web dashboard with a repository overview, wiki browser, interactive dependency graph, codebase chat, search, code ownership, hotspots, and dead code detection. In workspace mode, additional pages show a workspace dashboard, cross-repo API contracts, and co-change pairs. The overview page shows a health score, attention items, language breakdown, ownership treemap, quick actions, and a "Graph Intelligence" section with architectural communities and execution flow traces.

### With Node.js installed

If you have Node.js 20+, `repowise serve` auto-downloads and starts the web UI:

```bash
repowise serve
# API: http://localhost:7337
# Web UI: http://localhost:3000
```

The frontend is downloaded once (~50 MB) and cached in `~/.repowise/web/`.

To skip the web UI and only run the API: `repowise serve --no-ui`

### With Docker (no Node.js needed)

```bash
git clone https://github.com/repowise-dev/repowise.git
cd repowise
docker build -t repowise -f docker/Dockerfile .

docker run -p 7337:7337 -p 3000:3000 \
  -v /path/to/your-repo/.repowise:/data \
  -e GEMINI_API_KEY=your-key \
  -e REPOWISE_EMBEDDER=gemini \
  repowise
```

### From source (for development)

```bash
git clone https://github.com/repowise-dev/repowise.git
cd repowise && npm install

# Terminal 1: API
repowise serve --no-ui

# Terminal 2: Frontend (with hot reload)
REPOWISE_API_URL=http://localhost:7337 npm run dev --workspace packages/web
```

---

## Environment Variables

| Variable | When needed | Description |
|----------|-------------|-------------|
| `ANTHROPIC_API_KEY` | Using Anthropic | Anthropic API key |
| `OPENAI_API_KEY` | Using OpenAI | OpenAI API key |
| `GEMINI_API_KEY` | Using Gemini | Google Gemini API key |
| `REPOWISE_EMBEDDER` | Semantic search | Embedder: `gemini`, `openai`, or `mock` (default) |
| `REPOWISE_DB_URL` | Custom database | SQLite/PostgreSQL connection string (default: `.repowise/wiki.db`) |
| `REPOWISE_API_URL` | Frontend only | Backend URL for the web UI (default: `http://localhost:7337`) |

---

## What's Next

- **[User Guide](USER_GUIDE.md)** — full CLI reference, web UI features, MCP setup, common workflows, and troubleshooting
- **[CLI Reference](CLI_REFERENCE.md)** — every command with every flag
- **[MCP Tools](MCP_TOOLS.md)** — full tool reference, configuration, parameters, and examples
- **[Workspaces](WORKSPACES.md)** — multi-repo workspace setup and cross-repo intelligence
- **[Auto-Sync](AUTO_SYNC.md)** — hooks, file watcher, webhooks, polling
- **[Architecture](architecture/ARCHITECTURE.md)** — how repowise is built internally
