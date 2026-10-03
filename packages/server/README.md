# repowise-server

The FastAPI REST API, the MCP server, webhook handlers and the background
scheduler. `repowise serve` and `repowise mcp` start it, and the web UI reads
from its API (port 7337 by default).

**Python >= 3.11 · AGPL-3.0-or-later**

## Where it sits

This is one of the Python packages in the repowise monorepo. Code lives under
`src/repowise/server/`: `app.py` builds the FastAPI app, `routers/` holds the
REST routes, and `mcp_server/` holds the tools: 18 registered MCP tools
(10 advertised by default in single-repo mode). It reads indexes built by
`packages/core`.

Seen from an MCP client, repowise registers 18 MCP tools and advertises **10 by default**.
Workspace mode adds `list_repos`, and the other seven are opt-in through
`repowise mcp --tools` or the `mcp:` config block. See
[docs/agent/MCP_TOOLS.md](../../docs/agent/MCP_TOOLS.md).

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
