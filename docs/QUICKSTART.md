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

## 5. Keep It in Sync

After pulling changes or editing code:

```bash
repowise update
```

Or run continuous sync while you work:

```bash
repowise watch
```

---

## Web UI

Repowise includes a full web dashboard with a repository overview, wiki browser, interactive dependency graph, codebase chat, search, code ownership, hotspots, and dead code detection. The overview page shows a health score, attention items, language breakdown, ownership treemap, and quick actions.

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
docker build -t repowise https://github.com/RaghavChamadiya/repowise.git

docker run -p 7337:7337 -p 3000:3000 \
  -v /path/to/your-repo/.repowise:/data \
  -e GEMINI_API_KEY=your-key \
  -e REPOWISE_EMBEDDER=gemini \
  repowise
```

### From source (for development)

```bash
git clone https://github.com/RaghavChamadiya/repowise.git
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

- **[User Guide](USER_GUIDE.md)** — full CLI reference (all 13 commands with every flag), web UI features, MCP setup, common workflows, and troubleshooting
- **[Architecture](ARCHITECTURE.md)** — how repowise is built internally
