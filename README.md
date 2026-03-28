# repowise

**Codebase intelligence for developers and AI.**

repowise generates and maintains a structured, hierarchical wiki for any codebase.
It keeps documentation accurate as code changes and exposes everything through an
MCP server so AI coding assistants can query it in real time.

[![PyPI version](https://img.shields.io/pypi/v/repowise.svg)](https://pypi.org/project/repowise/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://pypi.org/project/repowise/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-purple.svg)](https://github.com/RaghavChamadiya/repowise/blob/main/LICENSE)

## Features

- **Automatic documentation** — generates module, file, and symbol-level docs from source code
- **Git intelligence** — tracks churn hotspots, ownership, bus factor, and change patterns
- **Dead code detection** — finds confirmed unused exports, functions, and types
- **Decision intelligence** — captures *why* code is structured the way it is
- **MCP server** — 8 tools for AI assistants (Claude Code, Cursor, Windsurf, etc.)
- **REST API + Web UI** — browse the wiki, search, and explore architecture diagrams
- **Codebase chat** — ask questions about your codebase in natural language
- **Multi-language** — Python, TypeScript, JavaScript, Go, Rust, Java, C/C++, Kotlin, Ruby

## Install

```bash
pip install repowise
```

LLM providers are optional — install only the one you need:

```bash
pip install "repowise[anthropic]"    # Claude models
pip install "repowise[openai]"       # GPT models
pip install "repowise[gemini]"       # Gemini models
pip install "repowise[litellm]"      # 100+ providers via LiteLLM
pip install "repowise[all]"          # Everything
```

## Quick Start

```bash
# Set your API key
export ANTHROPIC_API_KEY="sk-ant-..."   # or OPENAI_API_KEY, GEMINI_API_KEY

# Generate documentation for your codebase
cd /path/to/your-repo
repowise init

# Keep docs in sync after code changes
repowise update

# Start the MCP server for AI assistants
repowise mcp

# Browse the wiki in your browser
repowise serve
```

## How It Works

1. **Ingestion** — parses every file using tree-sitter, extracts symbols, imports, and builds a dependency graph
2. **Analysis** — computes git signals (churn, ownership, recency), detects dead code, identifies architectural decisions
3. **Generation** — sends structured prompts to an LLM to produce wiki pages at every level of the hierarchy
4. **Persistence** — stores everything in SQLite (or PostgreSQL) with full-text and vector search
5. **Serving** — exposes the wiki through REST API, MCP server, and web UI

## CLI Commands

| Command | Description |
|---------|-------------|
| `repowise init` | Generate full wiki documentation for a codebase |
| `repowise update` | Incrementally sync wiki after code changes |
| `repowise watch` | Auto-update wiki on file saves |
| `repowise search` | Full-text, semantic, or symbol search |
| `repowise mcp` | Start MCP server for AI editors |
| `repowise serve` | Start web UI server |
| `repowise dead-code` | Detect unused/unreachable code |
| `repowise decision` | Manage architectural decision records |
| `repowise generate-claude-md` | Generate CLAUDE.md for editor context |
| `repowise export` | Export pages to markdown/html/json |
| `repowise reindex` | Rebuild vector search index |
| `repowise status` | Show sync state and page counts |
| `repowise doctor` | Run health checks on wiki setup |

## MCP Tools for AI Editors

Once connected via `repowise mcp`, your AI editor gets 8 tools:

| Tool | What it does |
|------|-------------|
| `get_overview` | Architecture summary, key modules, entry points, git health |
| `get_context` | Rich context for files/symbols — docs, ownership, decisions, freshness |
| `get_risk` | Modification risk — hotspot score, dependents, bus factor, trend |
| `get_why` | Why code is structured this way — decisions, git archaeology |
| `search_codebase` | Semantic search with git freshness boosting |
| `get_dependency_path` | How two modules connect through the dependency graph |
| `get_dead_code` | Tiered dead code report grouped by confidence |
| `get_architecture_diagram` | Mermaid diagram with optional churn heat map |

Works with Claude Code, Cursor, Windsurf, Cline, and any MCP-compatible editor.

## Web UI

repowise includes a full web dashboard (Next.js + React + D3.js) with:

- **Wiki browser** — AI-generated docs with syntax highlighting, Mermaid diagrams, and git history sidebar
- **Dependency graph** — interactive force-directed visualization (handles 2000+ nodes)
- **Codebase chat** — ask questions about your code in natural language
- **Search** — full-text and semantic search with global command palette (Ctrl+K)
- **Symbol index** — searchable table of every function, class, and method
- **Coverage dashboard** — freshness breakdown with one-click regeneration
- **Ownership view** — contributor attribution and bus factor risk detection
- **Hotspots** — ranked high-churn files with commit history
- **Dead code finder** — unused code with confidence scores and bulk actions
- **Decision tracker** — architectural decisions with health monitoring

**Starts automatically with `repowise serve`** if Node.js 20+ is installed. No separate setup — the frontend is downloaded and cached on first run.

No Node.js? Use Docker instead:

```bash
docker build -t repowise https://github.com/RaghavChamadiya/repowise.git
docker run -p 7337:7337 -p 3000:3000 -v .repowise:/data repowise
```

See the [User Guide](https://github.com/RaghavChamadiya/repowise/blob/main/docs/USER_GUIDE.md#web-ui) for more options.

## Requirements

- Python 3.11+
- Git (for repository analysis)
- An LLM API key (for documentation generation — not needed for analysis-only mode)
- Node.js 20+ or Docker (optional, for the web UI)

## Documentation

- [User Guide](https://github.com/RaghavChamadiya/repowise/blob/main/docs/USER_GUIDE.md) — complete CLI reference, web UI guide, workflows, and troubleshooting
- [Architecture Guide](https://github.com/RaghavChamadiya/repowise/blob/main/docs/ARCHITECTURE.md) — how the system is built and why each piece exists
- [Core Library](https://github.com/RaghavChamadiya/repowise/blob/main/packages/core/README.md) — ingestion, generation, persistence, providers
- [CLI Package](https://github.com/RaghavChamadiya/repowise/blob/main/packages/cli/README.md) — all commands with every flag documented
- [Server & MCP Tools](https://github.com/RaghavChamadiya/repowise/blob/main/packages/server/README.md) — REST API endpoints, MCP tools, webhooks, scheduler
- [Web Frontend](https://github.com/RaghavChamadiya/repowise/blob/main/packages/web/README.md) — every page and component

## Contributing

Contributions are welcome. Please read the [Architecture Guide](https://github.com/RaghavChamadiya/repowise/blob/main/docs/ARCHITECTURE.md) before submitting PRs.

```bash
# Development setup
git clone https://github.com/RaghavChamadiya/repowise.git
cd repowise
uv sync                                    # Python dependencies
npm install                                # Web frontend dependencies
pytest                                     # Run tests
ruff check packages/ tests/                # Lint
```

## License

AGPL-3.0 — see [LICENSE](https://github.com/RaghavChamadiya/repowise/blob/main/LICENSE) for details.
