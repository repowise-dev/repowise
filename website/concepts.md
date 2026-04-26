---
layout: default
title: Core Concepts
nav_order: 3
---

# Core Concepts
{: .no_toc }

The four layers that make up repowise, and why each one exists.
{: .fs-6 .fw-300 }

---

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Overview

Every time you run `repowise init`, it runs through four layers in sequence. Each layer builds on the previous one. Understanding what each layer captures helps you interpret what you're getting — and what to do when something looks wrong.

```
Source code
     │
     ▼
┌─────────────┐
│  Ingestion  │  Parse, extract, build graph
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Analysis   │  Git signals, dead code, decisions
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Generation  │  LLM-written wiki pages
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Persistence │  SQLite, vector index, REST, MCP
└─────────────┘
```

---

## Layer 1: Ingestion

**What it captures:** The static structure of your code.

Ingestion parses every source file using [tree-sitter](https://tree-sitter.github.io/), a fast incremental parser that understands the syntax of each language. For each file, it extracts:

- **Symbols** — functions, classes, methods, interfaces, types
- **Imports and exports** — what each file depends on and exposes
- **Call sites** — which functions call which
- **File metadata** — size, language, path

From this, repowise builds a **dependency graph** using NetworkX. Nodes are files and modules. Edges are imports and call relationships. This graph is used by every subsequent layer.

**Why it matters for you:** The dependency graph powers risk analysis, cascade detection, and architecture diagrams. When you ask "what does this module affect?", the answer comes from this layer.

**Supported languages:** Python, TypeScript, JavaScript, Go, Rust, Java, C, C++, Kotlin, Ruby, C#, Swift, Scala, PHP. 14 languages with full AST support.

---

## Layer 2: Analysis

**What it captures:** Everything your code history knows that the source doesn't.

Analysis runs after ingestion and enriches each file with three types of signals:

### Git intelligence

For each file, repowise runs `git log` to extract:

- **Churn score** — how often this file changes relative to others (percentile rank)
- **Ownership** — which contributors have touched this file and by how much
- **Bus factor** — whether a file depends on knowledge held by too few people
- **Recency** — how recently it was last changed
- **Co-change partners** — which other files tend to change at the same time

High-churn files appear in the hotspots view and receive a warning in the MCP `get_risk` tool. Files with bus factor 1 are flagged as single points of failure.

### Dead code detection

repowise compares what your dependency graph says is reachable against what is exported or imported. Files and symbols with no inbound edges from reachable code are flagged as potentially dead.

Dead code findings are tiered by confidence:

| Tier | Meaning |
|------|---------|
| **High** | Confirmed unreachable — no imports from anywhere |
| **Medium** | No external imports, but exported (may be a public API) |
| **Low** | Referenced only in tests or dead code itself |

### Decision mining

repowise scans your git history, README files, and inline comments for patterns that indicate architectural decisions: `"we chose X because"`, `"ADR:"`, `"decision:"`, and similar markers. These are surfaced in the decision tracker and made queryable via `get_why`.

**Why it matters for you:** When an AI assistant edits a hotspot file without knowing it's high-churn, the change is risky. When it deletes an unused export without knowing it's a public API, that's a bug. The analysis layer gives AI tools the context to avoid these mistakes.

---

## Layer 3: Generation

**What it captures:** Human-readable documentation at every level of the hierarchy.

Generation is the only layer that requires an LLM API key. repowise sends structured prompts to your chosen provider (Anthropic, OpenAI, Gemini, Ollama, or LiteLLM) and generates wiki pages at three levels:

| Level | What it contains |
|-------|-----------------|
| **Repository** | High-level architecture overview, module map, tech stack |
| **Module** | Purpose, responsibilities, key dependencies, design patterns |
| **File** | What this file does, how it fits in the module, key symbols |

Each page is stored in the database and linked to the file it describes. Pages have a freshness score — if the underlying file changes, the page is marked stale and queued for regeneration.

**Skipping generation:** You can skip this layer entirely with `repowise init --index-only`. You'll still get the full dependency graph, git intelligence, dead code, and decision data — just no narrative docs. You can add generation later by running `repowise init` on an existing index.

**Cost:** For a 200-file codebase, generation typically uses 150,000–300,000 tokens. The `--dry-run` flag shows you the estimated cost before committing.

---

## Layer 4: Persistence

**What it captures:** Everything, queryable in three complementary ways.

All data from the previous three layers is stored across three stores:

### SQL (SQLite or PostgreSQL)

The primary store. Contains:
- All wiki pages with full text and metadata
- Symbol index (every function, class, method)
- Dependency graph (nodes and edges)
- Git metadata (churn, ownership, recency per file)
- Architectural decisions
- Dead code findings

Default: SQLite at `.repowise/wiki.db`. Switch to PostgreSQL by setting `REPOWISE_DB_URL`.

### Vector store (LanceDB)

An embedding index on top of the wiki pages. Powers semantic search — when you run `repowise search "authentication flow"` or ask Claude `search_codebase("how is auth handled")`, this is what responds.

Stored at `.repowise/lancedb/`. You can rebuild it at any time with `repowise reindex`.

### In-memory graph (NetworkX)

The dependency graph is loaded into memory at server startup for fast traversal. Used by `get_dependency_path`, `get_architecture_diagram`, and cascade analysis during `update`.

**Why it matters for you:** Three stores means three access patterns. Simple lookups hit SQL. Natural language queries hit the vector store. Graph traversal hits NetworkX. The MCP server abstracts all of this — you ask one question, it queries the right store.

---

## The MCP server

The MCP server sits on top of the persistence layer and exposes everything to AI coding assistants via 10 tools. It's the primary interface between repowise and Claude Code, Cursor, Cline, or any other MCP-compatible editor.

When you run `repowise mcp`, the server starts in stdio mode and your editor can begin calling tools. The tools are designed to answer the questions an AI needs to make good decisions about your code — not just "what is this file" but "should I edit it", "why is it structured this way", and "what will break if I change it".

See [MCP Server →](mcp-server) for the full tool reference.

---

## The `.repowise/` directory

Everything repowise knows about your repo lives in `.repowise/`:

```
.repowise/
├── wiki.db          # SQLite database
├── lancedb/         # Vector search index
├── config.yaml      # Provider, model, embedder settings
├── state.json       # Last sync commit, page counts
├── mcp.json         # MCP server config
└── .env             # API keys (gitignored)
```

This directory is created on first `init`. It should be added to `.gitignore` — repowise does this automatically.
