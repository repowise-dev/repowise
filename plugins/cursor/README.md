# Repowise for Cursor

Cursor plugin that connects the agent to [Repowise](https://repowise.dev)
through the hosted Repowise MCP server.

Repowise indexes a repository into a dependency graph, git history, ownership,
code health and generated documentation. With this plugin, Cursor's agent asks
that index instead of grepping and reading file after file, and its answers cite
real files, symbols and line ranges.

## Install

1. Open **Cursor Settings > Plugins**, search for **Repowise** and click
   **Install**. Or run `/add-plugin repowise` in chat.
2. Sign in with your repowise.dev account when Cursor asks. Auth is OAuth;
   leave any client ID and secret blank.
3. Index a repository from https://repowise.dev/dashboard. Public repositories
   already indexed on repowise.dev work without this step.

## What it includes

| Component | Path | What it does |
|---|---|---|
| MCP server | `mcp.json` | `https://api.repowise.dev/mcp`, streamable HTTP with OAuth |
| Skill | `skills/codebase-questions/` | Picks the repository and the right tool for a question |

```json
{
  "mcpServers": {
    "repowise": {
      "type": "http",
      "url": "https://api.repowise.dev/mcp"
    }
  }
}
```

## What the agent can do

| Ask | Tool |
|---|---|
| Which repositories can I query? | `list_repos` |
| What is this repository and how is it laid out? | `get_overview` |
| How does a feature work end to end? | `get_answer` |
| Where is a symbol defined and what calls it? | `search_codebase`, `get_symbol`, `get_context` |
| How risky is it to change this file or commit? | `get_risk`, `get_change_risk` |
| Why is this code shaped this way? | `get_why` |
| Code health, unused code, dependency and secret findings | `get_health`, `get_dead_code`, `get_security` |

Every tool is read-only. Repowise does not edit code, open pull requests or
change settings.

## Run it locally instead

Repowise is open source and runs on your machine with no account:

```bash
uv tool install repowise              # or: pipx install repowise / pip install repowise
cd /path/to/your/repo
repowise init --yes --no-prose        # index the repo, no API key needed
repowise agents add --target=cursor   # writes .cursor/mcp.json and the Repowise rule
```

The local server runs as `repowise mcp <repo path>` over stdio and reads the
index in `.repowise/` in that repository.

## Links

- Docs: https://docs.repowise.dev
- Source: https://github.com/repowise-dev/repowise
- Issues: https://github.com/repowise-dev/repowise/issues
- Privacy: https://repowise.dev/legal/privacy
