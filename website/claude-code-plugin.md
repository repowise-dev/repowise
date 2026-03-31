---
layout: default
title: Claude Code Plugin
nav_order: 8
---

# Claude Code Plugin
{: .no_toc }

The fastest way to get repowise — Claude handles everything.
{: .fs-6 .fw-300 }

---

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

The Claude Code plugin integrates repowise directly into Claude Code. It handles installation, API key setup, MCP server registration, and teaches Claude to use repowise tools proactively — without manual configuration.

---

## Installation

Open Claude Code and run:

```
/plugin marketplace add repowise-dev/repowise-plugin
/plugin install repowise@repowise
```

That's the entire installation. The plugin:

- Installs `repowise` via pip if not already installed
- Registers the MCP server with Claude Code
- Loads the slash commands
- Configures Claude to use repowise tools automatically

---

## Slash commands

### `/repowise:init`

Interactive setup and indexing for the current repository.

Claude will guide you through:

1. **Mode selection** — choose between:
   - **Full** — complete wiki generation with LLM docs (requires API key)
   - **Index-only** — graph + git + dead code, no LLM (free)
   - **Advanced** — manual control over provider, concurrency, exclude patterns

2. **Provider selection** — Anthropic, OpenAI, Gemini, or local Ollama

3. **API key entry** — saved to `.repowise/.env` (gitignored)

4. **Indexing** — runs in the background with live progress updates

When done, Claude confirms the MCP server is active and the codebase is queryable.

---

### `/repowise:status`

Show the current state of the repowise index.

Output includes:
- Last sync commit and timestamp
- Total pages, symbols, decisions indexed
- Provider and model in use
- Pages marked stale (need regeneration)
- MCP server connection status

---

### `/repowise:update`

Incrementally sync the wiki after code changes.

Claude detects which files have changed since the last indexed commit, regenerates only the affected pages, updates CLAUDE.md, and confirms when done.

---

### `/repowise:search`

Search the indexed wiki from within Claude Code.

Claude will ask for your query and search mode (fulltext, semantic, or symbol), then display results inline with links to relevant pages.

---

### `/repowise:reindex`

Rebuild the vector search index without making LLM calls.

Use this after switching embedding providers, or if semantic search results seem off.

---

## Automatic behaviors

Beyond the slash commands, the plugin teaches Claude skills it uses automatically — without being asked.

### Codebase exploration

Before reading raw source files, Claude calls:

- `get_overview()` at the start of new tasks to orient itself
- `search_codebase(query)` to locate code instead of using grep
- `get_context(targets)` to get docs and ownership before opening files

### Pre-modification checks

Before editing any file, Claude calls `get_risk(targets)` to assess:

- Whether the file is a hotspot (high churn)
- How many other files depend on it
- Whether there are co-change patterns to be aware of

If the risk is high, Claude surfaces this before making changes.

### Architectural decision queries

When facing "why is this structured this way" questions, Claude calls `get_why(query)` to check decision records and git archaeology before suggesting changes that might conflict with existing decisions.

### Dead code awareness

During refactoring or cleanup tasks, Claude calls `get_dead_code()` to find confirmed unused code rather than guessing.

---

## How skills work

Skills in Claude Code are prompt instructions that modify Claude's behavior. The repowise plugin registers four skills that Claude loads when working in an indexed repo.

You don't need to trigger them manually. When Claude detects it's working in a repo with a connected repowise MCP server, the skills activate automatically.

The CLAUDE.md generator reinforces these skills by writing the mandatory MCP tool workflow directly into your project's context file — so even without the plugin, any Claude session that reads your CLAUDE.md will follow the same workflow.

---

## Requirements

- Claude Code (desktop app, CLI, or IDE extension)
- Python 3.11+
- An LLM API key (for full mode — not needed for index-only)
