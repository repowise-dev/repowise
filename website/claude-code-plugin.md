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
/plugin marketplace add repowise-dev/repowise
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
   - **Index-only** — graph + git + dead code + code health, no LLM (free)
   - **Advanced** — manual control over provider, concurrency, exclude patterns

2. **Provider selection** — Anthropic, OpenAI, Gemini, Codex CLI, OpenCode, or local Ollama

3. **API key entry** — saved to `.repowise/.env` (gitignored) when a hosted provider needs one

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

### `/repowise:health`

Code-health KPIs, lowest-scoring files, refactoring targets, or trends
(`repowise health`).

---

### `/repowise:risk`

Defect-risk score for a commit or `base..head` range (`repowise risk`).

---

### `/repowise:security`

Full-history secret scan (`repowise security scan --history`). Working-tree
scanning already runs during init/update; without `--history` the CLI only
prints a hint. Default history mode is secrets-only; `--all-patterns` also
reports code-smell patterns.

---

### `/repowise:coverage`

Ingest or inspect coverage reports (`repowise coverage add` / `status`).
Lights up untested-hotspot markers and builds the per-test map when contexts
are present.

---

### `/repowise:impacted-tests`

Tests whose coverage intersects a change (`repowise impacted-tests`).

---

### `/repowise:dead-code`

Unreachable files, unused exports, and zombie packages by confidence.

---

### `/repowise:decision`

List, inspect, add, or confirm architectural decisions.

---

### `/repowise:doctor`

Diagnose (and optionally repair) setup, keys, and index drift.

---

## Automatic behaviors

Beyond the slash commands, the plugin teaches Claude skills it uses automatically — without being asked.

### Codebase exploration

Before reading raw source files, Claude calls:

- `get_overview()` at the start of new tasks to orient itself
- `get_answer(question)` for direct how/where/why questions
- `search_codebase(query)` to locate code instead of using grep
- `get_context(targets)` to get docs and ownership before opening files

### Pre-modification checks

Before editing any file, Claude calls `get_risk(targets)` to assess:

- Bug-fix history (`defect_profile` / `bug_magnet`) when present
- Whether the file is a hotspot (high churn)
- How many other files depend on it
- Whether there are co-change patterns to be aware of

If the risk is high, Claude surfaces this before making changes.

### Change review

For a PR / branch / working-tree diff, Claude combines `get_change_risk` (whole-change score) with `get_risk`'s per-file `directive` block.

### Code health

Quality / complexity / "what to refactor" questions go through `get_health`.

### Architectural decision queries

When facing "why is this structured this way" questions, Claude calls `get_why(query)` to check decision records and git archaeology before suggesting changes that might conflict with existing decisions.

### Dead code awareness

During refactoring or cleanup tasks, Claude calls `get_dead_code()` to find confirmed unused code rather than guessing.

---

## How skills work

Skills in Claude Code are prompt instructions that modify Claude's behavior. The repowise plugin registers six skills that Claude loads when working in an indexed repo.

You don't need to trigger them manually. When Claude detects it's working in a repo with a connected repowise MCP server, the skills activate automatically.

The CLAUDE.md generator reinforces these skills by writing the mandatory MCP tool workflow directly into your project's context file — so even without the plugin, any Claude session that reads your CLAUDE.md will follow the same workflow.

---

## Requirements

- Claude Code (desktop app, CLI, or IDE extension)
- Python 3.11+
- An LLM API key (for full mode — not needed for index-only)
