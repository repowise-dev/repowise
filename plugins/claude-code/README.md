# Repowise Plugin for Claude Code

Gives Claude Code deep understanding of your codebase — architecture, ownership, hotspots, dependencies, and architectural decisions.

## Install

### From Marketplace

```shell
/plugin marketplace add repowise-dev/repowise-plugin
/plugin install repowise@repowise
```

### Local Development

```shell
claude --plugin-dir ./plugins/claude-code
```

## Quick Start

After installing the plugin, just run:

```
/repowise:init
```

Claude will walk you through everything: installing repowise, choosing a mode, configuring your LLM provider, and indexing your codebase.

## What You Get

### Slash Commands

| Command | What it does |
|---------|-------------|
| `/repowise:init` | Interactive setup — installs repowise, asks your preferences, indexes your codebase |
| `/repowise:status` | Health check — sync state, page counts, provider info |
| `/repowise:update` | Incremental update — sync docs with recent code changes |
| `/repowise:search` | Search across the codebase wiki (fulltext, semantic, or symbol) |
| `/repowise:reindex` | Rebuild the vector store (re-embed, no LLM calls) |

### Automatic Skills

Claude automatically uses Repowise when relevant — no slash commands needed:

- **Codebase exploration** — uses `get_overview()` and `search_codebase()` before reading raw files
- **Pre-modification checks** — calls `get_risk()` before editing files to assess impact
- **Architectural decisions** — queries `get_why()` when encountering "why" questions
- **Dead code cleanup** — calls `get_dead_code()` during cleanup and refactoring tasks

### MCP Tools (8 total)

Registered automatically when the plugin is installed:

| Tool | Purpose |
|------|---------|
| `get_overview` | Architecture summary, module map, entry points |
| `get_context` | Docs + ownership + history + decisions for files/modules |
| `get_risk` | Hotspot score, dependents, co-change partners |
| `get_why` | Architectural decisions — search, path-based, or health dashboard |
| `search_codebase` | Semantic search over the full wiki |
| `get_dependency_path` | How two modules/files are connected |
| `get_dead_code` | Unused code findings sorted by confidence |
| `get_architecture_diagram` | Mermaid diagram for repo or module |

## Setup Modes

| Mode | What you get | Requirements | Time |
|------|-------------|-------------|------|
| **Full** | Graph + Git + Docs + Decisions + Search | LLM API key | ~25 min / 3k files |
| **Index-only** | Graph + Git + Dead Code | Nothing | < 60 seconds |
| **Local (Ollama)** | Full mode, fully offline | Ollama running | ~45 min / 3k files |

Run `/repowise:init` and Claude will guide you through choosing the right mode.

## Requirements

- Python 3.10+
- Git (for git intelligence features)
- Claude Code 1.0.33+

## Troubleshooting

**MCP tools not connecting:** Run `/repowise:init` — the plugin auto-registers the MCP server, but the `repowise` binary needs to be installed and on PATH.

**`pip install` fails on Windows:** Try `python -m pip install repowise` instead.

**Semantic search returns no results:** Your repo may be in index-only mode (no wiki pages). Run `/repowise:init` again with an LLM provider, or run `/repowise:reindex` if pages exist but embeddings are missing.

**Stale documentation:** Run `/repowise:update` to sync with recent code changes.
