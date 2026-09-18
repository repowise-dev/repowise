# repowise-server

The FastAPI REST API, the MCP server, webhook handlers and the background
scheduler. `repowise serve` and `repowise mcp` start it, and the web UI reads
from its API (port 7337 by default).

**Python >= 3.11 · AGPL-3.0-or-later**

## Where it sits

This is one of the Python packages in the repowise monorepo. Code lives under
`src/repowise/server/`: `app.py` builds the FastAPI app, `routers/` holds the
REST routes, and `mcp_server/` holds the tools: 19 registered MCP tools
(10 advertised by default in single-repo mode). It reads indexes built by
`packages/core`.

<<<<<<< HEAD
Seen from an MCP client, repowise registers 19 MCP tools and advertises **10 by default**.
Workspace mode adds `list_repos`, and the other eight are opt-in through
`repowise mcp --tools` or the `mcp:` config block. See
[docs/agent/MCP_TOOLS.md](../../docs/agent/MCP_TOOLS.md).
=======
| Component | Description |
|-----------|-------------|
| **REST API** | FastAPI application with full CRUD for repos, pages (with version history), symbols, jobs, git analytics, dead code, decisions, graph intelligence, blast radius, costs, knowledge map, security findings, providers, chat, CLAUDE.md generation, and multi-repo workspace |
| **MCP Server** | 19 registered MCP tools (10 advertised by default in single-repo mode: the canonical set) for AI coding assistants (Claude Code, Cursor, Cline) |
| **Webhooks** | GitHub and GitLab push event handlers — trigger sync jobs automatically on push |
| **Scheduler** | APScheduler background jobs — polling fallback (auto-syncs diverged repos), stale page detection |
>>>>>>> 3dc841ddd (fix(glossary): move every published tool count with the new tool)

It is not published to PyPI on its own. Users get it inside the single
`repowise` distribution built from the root `pyproject.toml`.

## Install the product

```bash
uv tool install repowise      # or: pipx install repowise / pip install repowise
cd your-repo
repowise init
repowise serve                # API on :7337, web UI on :3000
```

## Develop on it

From the repository root, `uv sync --all-packages` installs every package in
editable mode. See [CONTRIBUTING.md](../../.github/CONTRIBUTING.md).

## Documentation

- MCP tools: [docs/agent/MCP_TOOLS.md](../../docs/agent/MCP_TOOLS.md)
- CLI commands, including `serve` and `mcp`: [docs/reference/CLI_REFERENCE.md](../../docs/reference/CLI_REFERENCE.md)
- How the system is built: [docs/architecture/](../../docs/architecture/)
