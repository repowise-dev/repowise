# Quickstart

Get repowise running on your codebase in under 5 minutes.

> For the full CLI reference, web UI docs, MCP integration, and troubleshooting, see the [User Guide](USER_GUIDE.md).

---

## 1. Install

```bash
pip install "repowise[anthropic]"
```

Or substitute `openai`, `gemini`, `litellm`, or `all` depending on your LLM provider.

**Requirements:** Python 3.11+, Git.

## 2. Set Your API Key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or `OPENAI_API_KEY`, `GEMINI_API_KEY` — whichever provider you installed.

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

**Connect to your AI editor (Claude Code, Cursor, Cline, Windsurf):**

```bash
repowise mcp --transport stdio
```

> **Automatic for Claude Code:** `repowise init` already registers the MCP server and installs PreToolUse/PostToolUse hooks in `~/.claude/settings.json`. Every `Grep`/`Glob` call is automatically enriched with graph context (importers, dependencies, symbols, git signals). After git commits, the agent is notified when the wiki is stale.

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
- **[MCP Tools](MCP_TOOLS.md)** — all 7 MCP tools with parameters and examples
- **[Workspaces](WORKSPACES.md)** — multi-repo workspace setup and cross-repo intelligence
- **[Auto-Sync](AUTO_SYNC.md)** — hooks, file watcher, webhooks, polling
- **[Architecture](architecture/ARCHITECTURE.md)** — how repowise is built internally
