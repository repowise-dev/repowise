# repowise-server

FastAPI REST API, webhook handlers, MCP server, and background job scheduler for repowise. This package powers the `repowise serve` command and the Next.js web UI backend.

**Python >= 3.11 · Apache-2.0**

---

## What's in this package

| Component | Description |
|-----------|-------------|
| **REST API** | FastAPI application with full CRUD for repos, pages, symbols, jobs, git analytics, dead code |
| **MCP Server** | 8 MCP tools for AI coding assistants (Claude Code, Cursor, Cline) |
| **Webhooks** | GitHub and GitLab push event handlers — trigger incremental updates automatically |
| **Scheduler** | APScheduler background jobs — polling fallback, stale page decay, periodic re-sync |

---

## Installation

```bash
pip install repowise-server

# Recommended: use uv
uv pip install repowise-server
```

Installs `fastapi`, `uvicorn[standard]`, `mcp`, `apscheduler`, `cryptography`, and `repowise-core` automatically.

---

## Running the Server

```bash
# Via the CLI (recommended)
repowise serve                             # localhost:7337
repowise serve --host 0.0.0.0 --port 8080

# Directly with uvicorn
uvicorn repowise.server.app:create_app --factory --port 7337

# With hot reload (development)
uvicorn repowise.server.app:create_app --factory --reload --port 7337
```

Interactive API docs are available at `http://localhost:7337/docs` once the server is running.

---

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `REPOWISE_DB_URL` | `sqlite+aiosqlite:///.repowise/wiki.db` | Database URL (SQLite or PostgreSQL) |
| `REPOWISE_API_KEY` | _(none)_ | Bearer token required on all API requests (leave unset to disable auth) |
| `REPOWISE_EMBEDDER` | `mock` | Embedder backend: `mock` (FTS only) or `gemini` (real semantic search) |
| `REPOWISE_WEBHOOK_SECRET` | _(none)_ | HMAC-SHA256 secret for verifying GitHub/GitLab webhook signatures |
| `ANTHROPIC_API_KEY` | _(none)_ | Anthropic API key (required for Anthropic provider jobs) |
| `OPENAI_API_KEY` | _(none)_ | OpenAI API key (required for OpenAI provider jobs) |

---

## REST API

All endpoints are prefixed with `/api/`. When `REPOWISE_API_KEY` is set, every request must include an `Authorization: Bearer <key>` header.

### Repositories

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/repos` | List all registered repositories |
| `POST` | `/api/repos` | Register a new repository |
| `GET` | `/api/repos/{id}` | Get repository details and sync state |
| `PATCH` | `/api/repos/{id}` | Update repository settings (name, branch, provider) |
| `POST` | `/api/repos/{id}/sync` | Trigger incremental sync (equivalent to `repowise update`) |
| `POST` | `/api/repos/{id}/full-resync` | Trigger full re-generation (equivalent to `repowise init`) |

### Pages

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/pages` | List wiki pages, with optional `repo_id` and `page_type` filters |
| `GET` | `/api/pages/lookup` | Look up a page by `repo_id` + `page_id` query params |
| `POST` | `/api/pages/lookup/regenerate` | Regenerate a single page on demand |
| `GET` | `/api/pages/{id}/versions` | Full version history for a page |

### Jobs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs` | List generation jobs (supports `repo_id` filter) |
| `GET` | `/api/jobs/{id}` | Get job status and progress |
| `GET` | `/api/jobs/{id}/stream` | SSE stream of real-time job progress events |

Job progress events (`JobProgressEvent`) carry: `event` type, `file` currently being processed, `level` in the generation hierarchy, `completed` and `total` counts, elapsed time, and a final summary on completion.

### Symbols

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/symbols` | List/search symbols with `repo_id`, `kind`, `language`, `query` filters |
| `GET` | `/api/symbols/{id}` | Get symbol details including signature and source location |

### Graph

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/graph/{repo_id}` | Export dependency graph as nodes + edges (supports language and test filters) |
| `GET` | `/api/graph/{repo_id}/path` | Shortest dependency path between two modules |

### Git Analytics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/git/{repo_id}/metadata` | Per-file git metadata (ownership, commit count, co-change partners) |
| `GET` | `/api/git/{repo_id}/hotspots` | Files ranked by churn rate (most frequently changed) |
| `GET` | `/api/git/{repo_id}/ownership` | Code ownership breakdown by file or module granularity |
| `GET` | `/api/git/{repo_id}/co-changes` | Files that frequently change together (temporal coupling) |
| `GET` | `/api/git/{repo_id}/summary` | Recent commit activity summary |

### Dead Code

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dead-code/{repo_id}` | List dead code findings (supports `kind` and `safe_only` filters) |
| `POST` | `/api/dead-code/{repo_id}/analyze` | Trigger dead code analysis |
| `GET` | `/api/dead-code/{repo_id}/summary` | Summary stats (total findings, deletable lines, by kind) |
| `PATCH` | `/api/dead-code/{id}` | Update a finding's status: `resolved`, `acknowledged`, or `dismissed` |

### Search

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/search` | Search wiki pages. Body: `{"query": "...", "repo_id": "...", "mode": "fts|semantic|hybrid"}` |

### Webhooks

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/webhooks/github` | GitHub push event handler |
| `POST` | `/api/webhooks/gitlab` | GitLab push event handler |

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Server health — db connectivity, embedder info, scheduler status |

---

## MCP Server

repowise exposes 8 MCP tools for AI coding assistants. Start the MCP server via:

```bash
repowise mcp                          # stdio transport (Claude Code, Cursor, Cline)
repowise mcp --transport sse          # SSE transport on port 7338
```

| Tool | What It Answers | When to Call |
|------|----------------|-------------|
| `get_overview` | Architecture summary, module map, entry points | First call when exploring an unfamiliar codebase |
| `get_context(targets)` | Docs, ownership, history, decisions, freshness for files/modules/symbols | When you need to understand specific code before reading or modifying it |
| `get_risk(targets)` | Hotspot score, dependents, co-change partners, risk summary | Before modifying files — assess what could break |
| `get_why(query?)` | Architectural decisions, rationale, constraints | Before making architectural changes — understand existing intent |
| `search_codebase(query)` | Semantic search over full wiki | When you don't know where something lives |
| `get_dependency_path(from, to)` | Connection path between two files/modules | When you need to understand how two things are connected |
| `get_dead_code` | Unused/unreachable code sorted by cleanup impact | Before cleanup tasks |
| `get_architecture_diagram` | Mermaid diagram for repo or module | For documentation or presentation |

**Claude Code / Cursor / Cline setup** — add to your MCP config:

```json
{
  "mcpServers": {
    "repowise": {
      "command": "repowise",
      "args": ["mcp", "/absolute/path/to/your/repo"]
    }
  }
}
```

---

## Webhooks

Register the webhook URL with GitHub or GitLab so `repowise update` runs automatically on every push.

**GitHub setup:**

1. Go to `Settings → Webhooks → Add webhook` in your GitHub repository
2. Set **Payload URL** to `https://your-server.example.com/api/webhooks/github`
3. Set **Content type** to `application/json`
4. Set **Secret** to the value of `REPOWISE_WEBHOOK_SECRET`
5. Select **Just the push event**

**GitLab setup:**

1. Go to `Settings → Webhooks` in your GitLab project
2. Set **URL** to `https://your-server.example.com/api/webhooks/gitlab`
3. Set **Secret token** to the value of `REPOWISE_WEBHOOK_SECRET`
4. Enable **Push events**

The server verifies HMAC-SHA256 signatures, deduplicates events (stored in the `webhook_events` table), and queues an incremental sync job via the scheduler.

---

## Background Jobs

The APScheduler instance manages the following recurring tasks:

| Job | Schedule | Description |
|-----|----------|-------------|
| Stale page decay | Every 6 hours | Reduces confidence scores on pages whose source hash has changed |
| Polling fallback | Every 15 minutes | Checks for new commits on repos without webhook integration |
| Dead code re-analysis | Daily | Re-runs dead code analysis after large syncs |

---

## Development

```bash
# Install for development (from repo root)
uv pip install -e packages/server -e packages/core

# Start with hot reload
uvicorn repowise.server.app:create_app --factory --reload --port 7337

# Run tests
pytest tests/unit/server/ tests/integration/
```
