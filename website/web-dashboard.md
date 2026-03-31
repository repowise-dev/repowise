---
layout: default
title: Web Dashboard
nav_order: 6
---

# Web Dashboard
{: .no_toc }

Browse your codebase wiki, explore architecture, and search from a browser.
{: .fs-6 .fw-300 }

---

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Starting the dashboard

```bash
repowise serve
```

This starts two servers:

| Server | URL | Purpose |
|--------|-----|---------|
| API | `http://localhost:7337` | REST API (FastAPI) |
| Web UI | `http://localhost:3000` | Next.js frontend |

Open [http://localhost:3000](http://localhost:3000) in your browser.

On first run, repowise downloads and caches the frontend automatically. Node.js 20+ must be installed. For Docker-based setup without Node.js, see [Self-hosting →](self-hosting).

### Custom ports

```bash
repowise serve --port 8080 --ui-port 4000
```

### API only (no UI)

```bash
repowise serve --no-ui
```

### Expose on your network

```bash
repowise serve --host 0.0.0.0
```

---

## Dashboard views

### Wiki browser

The main view. Shows all indexed pages organized by module and file. Each page contains:

- AI-generated documentation for the file or module
- Key symbols (functions, classes, interfaces) with signatures
- Dependency list (what this file imports and what imports it)
- Git sidebar: last commit, contributors, churn percentile, change history

Pages marked stale (underlying file has changed since last generation) show a regeneration prompt.

### Dependency graph

An interactive force-directed graph built with D3.js. Handles repositories with 2,000+ nodes without degrading.

- **Zoom and pan** to navigate large graphs
- **Click a node** to open its wiki page
- **Hover** to see the file name and module
- **Filter by module** to isolate a subsystem
- **Toggle heat map** to color nodes by churn intensity (red = high churn, green = stable)
- **Search** to highlight matching nodes

### Search

Full-text and semantic search across the entire wiki. Press `Ctrl+K` (or `Cmd+K`) from anywhere in the dashboard to open the global command palette.

Search modes:
- **Full-text** — keyword matching against page content
- **Semantic** — natural language queries using vector embeddings
- **Symbol** — search by function name, class name, or method signature

### Symbol index

A filterable table of every indexed symbol: functions, classes, methods, interfaces, types. Columns include:

- Name and signature
- File and module
- Kind (function, class, method, etc.)
- Usage count (how many other files reference it)

Click any symbol to jump to its wiki page.

### Coverage dashboard

Shows documentation freshness across the codebase:

- **Fresh** — page is current with the source file
- **Stale** — source file has changed since the page was generated
- **Missing** — file exists but has no wiki page

One-click regeneration for individual stale pages, or bulk-regenerate all with `repowise update`.

### Ownership view

Shows contributor attribution across modules:

- Per-file owner (contributor with the most commit weight)
- Bus factor risk detection (files where one person holds >80% of knowledge)
- Contributor activity over time
- Module-level ownership breakdown

### Hotspots

A ranked list of high-churn files. Each entry shows:

- File path and module
- Churn percentile (e.g., "99.8th %ile")
- Number of commits in the last 90 days
- Primary owner
- Trend direction (increasing / stable / decreasing)

### Dead code finder

Lists unused code with confidence scores and bulk actions:

- **Unreachable files** — files with no inbound imports
- **Unused exports** — exported symbols with no external consumers
- **Unused internals** — internal symbols never called
- **Zombie packages** — installed dependencies never imported

Each finding shows confidence score and a "safe to delete" flag for high-confidence cases. Findings can be exported as a Markdown checklist.

### Decision tracker

All architectural decisions captured by repowise, organized by:

- **Status** — proposed, active, deprecated, superseded
- **Source** — git archaeology, inline markers, README mining, manually added
- **Health score** — staleness relative to how much the affected code has changed

Proposed decisions (mined automatically) can be confirmed or dismissed from this view.

### Codebase chat

Ask natural language questions about your codebase. The chat endpoint uses the wiki index as retrieval context, so answers are grounded in your actual code — not a hallucination.

Example questions:
- "How does authentication work?"
- "Which module handles database migrations?"
- "What are the entry points for the CLI?"
- "Show me all files that depend on the session module"

---

## REST API

The API at `http://localhost:7337` exposes everything in the dashboard programmatically. Key endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Server health check |
| `GET /repos/{id}/pages` | List all wiki pages |
| `GET /repos/{id}/pages/{page_id}` | Get a wiki page |
| `GET /repos/{id}/search?q=...` | Full-text search |
| `GET /repos/{id}/symbols` | List all symbols |
| `GET /repos/{id}/graph` | Dependency graph data |
| `GET /repos/{id}/dead-code` | Dead code findings |
| `GET /repos/{id}/decisions` | Decision records |
| `POST /repos/{id}/chat` | Codebase chat |
| `GET /repos/{id}/generate-claude-md` | Generate CLAUDE.md content |

Full API docs are available at `http://localhost:7337/docs` (Swagger UI) when the server is running.
