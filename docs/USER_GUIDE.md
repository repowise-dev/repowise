# Repowise User Guide

Everything you need to know to install, use, and get the most out of repowise.

---

## Table of Contents

1. [Installation](#installation)
2. [Getting Started](#getting-started)
3. [CLI Command Reference](#cli-command-reference)
   - [init](#repowise-init)
   - [update](#repowise-update)
   - [watch](#repowise-watch)
   - [search](#repowise-search)
   - [mcp](#repowise-mcp)
   - [serve](#repowise-serve)
   - [dead-code](#repowise-dead-code)
   - [decision](#repowise-decision)
   - [generate-claude-md](#repowise-generate-claude-md)
   - [export](#repowise-export)
   - [reindex](#repowise-reindex)
   - [status](#repowise-status)
   - [doctor](#repowise-doctor)
   - [workspace](#repowise-workspace)
   - [hook](#repowise-hook)
4. [Web UI](#web-ui)
5. [MCP Integration with AI Editors](#mcp-integration-with-ai-editors)
6. [Proactive Context Enrichment (Hooks)](#proactive-context-enrichment-hooks)
7. [Auto-Sync](#auto-sync)
8. [Environment Variables](#environment-variables)
9. [Common Workflows](#common-workflows)
10. [Troubleshooting](#troubleshooting)

---

## Installation

### From PyPI

```bash
pip install repowise
```

This installs the core engine, CLI, server, and MCP tools. No LLM provider SDK is included by default — install only the one you need:

```bash
pip install "repowise[anthropic]"    # Claude (Anthropic)
pip install "repowise[openai]"       # GPT (OpenAI)
pip install "repowise[gemini]"       # Gemini (Google)
pip install "repowise[litellm]"      # 100+ providers via LiteLLM (Together, Groq, Azure, Bedrock, etc.)
pip install "repowise[all]"          # All LLM providers + PostgreSQL support
```

If you plan to use PostgreSQL instead of the default SQLite:

```bash
pip install "repowise[postgres]"
```

### Requirements

- Python 3.11 or later
- Git (repowise analyzes your repository's git history)
- An LLM API key (for documentation generation — not needed for analysis-only mode)

### Verify Installation

```bash
repowise --version
repowise --help
```

---

## Getting Started

### 1. Set your API key

```bash
# Pick one:
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GEMINI_API_KEY="..."
```

On Windows PowerShell:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

### 2. Initialize your codebase

```bash
cd /path/to/your-repo
repowise init
```

In interactive mode, repowise asks you to choose:

- **Index-only** — free, no LLM. Parses code, builds dependency graph, indexes git history. Useful for analysis without documentation generation.
- **Full** — uses your chosen LLM to generate human-readable wiki pages for every file and module.
- **Advanced** — fine-tune every option (concurrency, exclusions, commit limits, etc.)

A typical first run on a medium codebase (~500 files) takes 5-15 minutes and costs $1-5 depending on the provider.

### 3. Start using the wiki

After init completes, you have several ways to access the generated documentation:

```bash
repowise search "authentication"     # Search from the terminal
repowise serve                       # Browse in a web UI at localhost:7337
repowise mcp                         # Connect to Claude Code, Cursor, etc.
```

### What gets created

```
your-repo/
├── .repowise/
│   ├── wiki.db           # SQLite database with all pages, symbols, graph, git data
│   ├── state.json        # Sync metadata (last commit, pages, tokens used)
│   ├── config.yaml       # Saved configuration (provider, model, excludes)
│   ├── .env              # Saved API keys (gitignored)
│   └── lancedb/          # Vector store for semantic search
└── CLAUDE.md             # Auto-generated codebase context for AI editors
```

---

## CLI Command Reference

### `repowise init`

Generate complete wiki documentation for a codebase. This is the starting point.

```bash
repowise init [PATH]
```

**What it does (4 phases):**

1. **Ingestion** — walks every file, parses AST with tree-sitter, builds a dependency graph, indexes git history (churn, hotspots, ownership, bus factor)
2. **Analysis** — detects dead code, extracts architectural decisions from inline markers, READMEs, and git history
3. **Generation** — sends structured prompts to the LLM, generates file-level, module-level, and repo-level wiki pages, plus architecture diagrams
4. **Persistence** — stores everything in `.repowise/wiki.db`, builds search indexes, generates `CLAUDE.md`

**Options:**

| Flag | Description |
|------|-------------|
| `--provider` | LLM provider: `anthropic`, `openai`, `gemini`, `ollama`, `mock`. Auto-detected from env vars if not set. |
| `--model` | Model name override (e.g., `claude-sonnet-4-6`, `gpt-5.4-nano`) |
| `--embedder` | Embedder for semantic search: `gemini`, `openai`, `mock`. Auto-detected from env vars. |
| `--index-only` | Skip LLM generation entirely. Only parse, build graph, and index git. Free. |
| `--dry-run` | Show generation plan and cost estimate without running anything. |
| `--test-run` | Generate docs for only the top 10 files (by PageRank) — quick validation. |
| `--skip-tests` | Exclude test files from documentation generation. |
| `--skip-infra` | Exclude infrastructure files (Dockerfiles, Makefiles, Terraform, shell scripts). |
| `--exclude / -x` | Gitignore-style exclusion patterns. Repeatable: `-x vendor/ -x "*.generated.*"` |
| `--concurrency` | Max concurrent LLM calls (default: 5). Higher = faster but more API pressure. |
| `--resume` | Resume from the last checkpoint if a previous run was interrupted. |
| `--force` | Regenerate all pages even if they already exist. |
| `--commit-limit` | Max commits to analyze per file (default: 500, max: 5000). Saved to config. |
| `--follow-renames` | Track file renames in git history (slower but more accurate). |
| `--no-claude-md` | Don't generate `CLAUDE.md` at the end. |
| `--yes / -y` | Skip cost confirmation prompt (auto-confirms if cost > $2). |

**Examples:**

```bash
# Interactive mode (asks questions)
repowise init

# Fully automated
repowise init --provider anthropic --model claude-sonnet-4-6 --yes

# Just index, no LLM cost
repowise init --index-only

# Preview what will happen
repowise init --provider openai --dry-run

# Quick test with 10 files
repowise init --provider gemini --test-run

# Exclude vendor and generated code
repowise init -x vendor/ -x "*.gen.go" -x "**/__generated__/**"
```

---

### `repowise update`

Incrementally update wiki pages for files that changed since the last sync.

```bash
repowise update [PATH]
```

Much faster and cheaper than a full `init` — only regenerates pages for changed files and their dependents.

**How it works:**

1. Diffs `HEAD` against the last sync commit (stored in `state.json`)
2. Re-parses changed files and rebuilds the dependency graph
3. Determines affected pages (direct changes + dependents via cascade analysis)
4. Regenerates only those pages
5. Updates `state.json` and `CLAUDE.md`

**Options:**

| Flag | Description |
|------|-------------|
| `--provider` | Override LLM provider for this run |
| `--model` | Override model |
| `--since` | Git ref to diff from (overrides `state.json`). Example: `--since v1.0.0` |
| `--cascade-budget` | Max pages to regenerate per run (default: 30). Prevents runaway regeneration. |
| `--dry-run` | Show what would be updated without regenerating. |

**Examples:**

```bash
# Update after pulling changes
git pull
repowise update

# See what changed without regenerating
repowise update --dry-run

# Update since a specific tag
repowise update --since v2.0.0

# Limit regeneration scope
repowise update --cascade-budget 10
```

---

### `repowise watch`

Watch for file changes and automatically update wiki pages.

```bash
repowise watch [PATH]
```

Runs continuously. Uses filesystem events (via watchdog) to detect saves, debounces them, and triggers `repowise update` automatically. Press `Ctrl+C` to stop.

**Options:**

| Flag | Description |
|------|-------------|
| `--provider` | LLM provider |
| `--model` | Model override |
| `--debounce` | Debounce delay in milliseconds (default: 2000). Collects changes for this long before triggering an update. |

**Example:**

```bash
# Start watching — wiki syncs as you code
repowise watch --debounce 3000
```

---

### `repowise search`

Search wiki pages by keyword, meaning, or symbol name.

```bash
repowise search QUERY [PATH]
```

**Options:**

| Flag | Description |
|------|-------------|
| `--mode` | Search mode: `fulltext` (default), `semantic`, `symbol` |
| `--limit` | Max results (default: 10) |

**Search modes:**

- **fulltext** — SQLite FTS. Fast, exact keyword matching.
- **semantic** — Vector similarity search via LanceDB. Understands meaning ("how does auth work?" finds authentication code even without the word "auth"). Falls back to fulltext if vector store is unavailable.
- **symbol** — Searches the symbol index (function names, class names, etc.) with fuzzy matching.

**Examples:**

```bash
# Keyword search
repowise search "rate limiting"

# Semantic search — understands intent
repowise search "how are errors handled" --mode semantic

# Find a symbol
repowise search "AuthService" --mode symbol --limit 20
```

---

### `repowise mcp`

Start the MCP (Model Context Protocol) server for AI editor integration.

```bash
repowise mcp [PATH]
```

This is how you connect repowise to Claude Code, Cursor, Cline, Windsurf, and other MCP-compatible editors.

**Options:**

| Flag | Description |
|------|-------------|
| `--transport` | Protocol: `stdio` (default, for editors) or `sse` (for web clients) |
| `--port` | Port for SSE transport (default: 7338) |

**MCP tools exposed (7 tools):**

| Tool | What it does |
|------|-------------|
| `get_overview` | Repository architecture summary, key modules, entry points, git health, community summary |
| `get_answer` | One-call RAG: confidence-gated synthesis over the wiki, with cited 2–5 sentence answers and a per-repository question cache |
| `get_context` | Complete context for files/modules/symbols — docs, ownership, decisions, freshness, community membership. Defaults to `compact=True`; pass `compact=False` for the full structure block and importer list. In workspace mode, accepts `repo` parameter. |
| `search_codebase` | Semantic search over wiki with git freshness boosting. In workspace mode, searches across all repos. |
| `get_risk` | Modification risk assessment — hotspot score, dependents, co-change partners, bus factor, blast radius, test gaps, 0–10 risk score |
| `get_why` | Why code is structured the way it is — architectural decisions, git archaeology. Three modes: NL search, path-based, health dashboard. |
| `get_dead_code` | Tiered dead code report grouped by confidence with cleanup impact estimates |

In workspace mode, tools are workspace-aware — pass `repo="backend"` to target a specific repo or `repo="all"` to query across the entire workspace. The default repo is used when `repo` is omitted.

See [MCP Integration](#mcp-integration-with-ai-editors) for setup instructions. Full tool reference: [MCP_TOOLS.md](MCP_TOOLS.md)

---

### `repowise serve`

Start the API server and web UI.

```bash
repowise serve [PATH]
```

If Node.js 20+ is installed, the web frontend is automatically downloaded (once, ~50 MB) and started alongside the API. No separate setup needed.

**Options:**

| Flag | Description |
|------|-------------|
| `--port` | API server port (default: 7337) |
| `--host` | Host to bind to (default: 127.0.0.1) |
| `--workers` | Uvicorn workers (default: 1) |
| `--ui-port` | Web UI port (default: 3000) |
| `--no-ui` | Start API server only, skip the web UI. |

**Examples:**

```bash
# Start everything (API + Web UI)
repowise serve

# API only
repowise serve --no-ui

# Custom ports
repowise serve --port 8080 --ui-port 8081
```

---

### `repowise dead-code`

Detect dead and unused code in your codebase.

```bash
repowise dead-code [PATH]
```

**Options:**

| Flag | Description |
|------|-------------|
| `--min-confidence` | Minimum confidence threshold (default: 0.4) |
| `--safe-only` | Only show findings marked safe to delete |
| `--kind` | Filter: `unreachable_file`, `unused_export`, `unused_internal`, `zombie_package` |
| `--format` | Output: `table` (default), `json`, `md` |

**Examples:**

```bash
# Show all findings
repowise dead-code

# Only safe-to-delete items
repowise dead-code --safe-only --min-confidence 0.8

# JSON output for scripting
repowise dead-code --format json

# Only unused exports
repowise dead-code --kind unused_export
```

---

### `repowise decision`

Manage architectural decision records. Repowise automatically extracts decisions from inline markers (`// DECISION: ...`), READMEs, and git history. You can also add them manually.

**Subcommands:**

```bash
repowise decision list [PATH]          # List decisions
repowise decision show ID [PATH]       # Show full details of a decision
repowise decision add [PATH]           # Interactively add a new decision
repowise decision confirm ID [PATH]    # Confirm a proposed decision (set to active)
repowise decision dismiss ID [PATH]    # Delete a proposed decision
repowise decision deprecate ID [PATH]  # Mark as deprecated
repowise decision health [PATH]        # Decision health dashboard
```

**List options:**

| Flag | Description |
|------|-------------|
| `--status` | Filter: `active`, `proposed`, `deprecated`, `superseded`, `all` |
| `--source` | Filter: `git_archaeology`, `inline_marker`, `readme_mining`, `cli`, `all` |
| `--proposed` | Shortcut for `--status proposed` |
| `--stale-only` | Only show stale decisions |

**Examples:**

```bash
# See all active decisions
repowise decision list --status active

# Review auto-extracted proposals
repowise decision list --proposed

# Decision health overview
repowise decision health

# Confirm a proposed decision
repowise decision confirm abc123

# Deprecate, replaced by another
repowise decision deprecate abc123 --superseded-by def456
```

---

### `repowise generate-claude-md`

Generate or update `CLAUDE.md` with codebase intelligence.

```bash
repowise generate-claude-md [PATH]
```

`CLAUDE.md` gives AI editors (Claude Code, Cursor, etc.) instant context about your codebase — architecture, key modules, hotspots, entry points, and conventions.

If you have custom instructions at the top of your `CLAUDE.md`, they are preserved. Only the auto-generated section (between markers) is updated.

**Options:**

| Flag | Description |
|------|-------------|
| `--output / -o` | Custom output path (default: `CLAUDE.md` in repo root) |
| `--stdout` | Print to stdout instead of file |

---

### `repowise export`

Export wiki pages to files.

```bash
repowise export [PATH]
```

**Options:**

| Flag | Description |
|------|-------------|
| `--format` | `markdown` (default), `html`, `json` |
| `--output / -o` | Output directory (default: `.repowise/export`) |
| `--full` | Include decisions, dead code findings, hotspots, and provenance metadata (JSON format only) |

**Examples:**

```bash
# Export all pages as markdown files
repowise export

# Export as a single JSON file
repowise export --format json --output ./wiki-export/

# Export as HTML
repowise export --format html
```

---

### `repowise reindex`

Rebuild vector search index from existing wiki pages. Useful after changing embedders or if the vector store gets corrupted.

```bash
repowise reindex [PATH]
```

**Options:**

| Flag | Description |
|------|-------------|
| `--embedder` | Embedder: `gemini`, `openai`, `auto` (default: auto-detect from config) |
| `--batch-size` | Pages per embedding batch (default: 20) |

---

### `repowise status`

Show wiki sync state and page statistics.

```bash
repowise status [PATH]
```

Displays: last sync commit, total pages, pages by type, provider used, total tokens consumed.

---

### `repowise doctor`

Run health checks on the wiki setup.

```bash
repowise doctor [PATH]
```

Checks:
- Git repository valid
- `.repowise/` directory exists
- Database connectable with page count
- `state.json` valid
- Providers installed and importable
- Stale page count

---

### `repowise workspace`

Manage multi-repo workspaces. See [Workspaces](WORKSPACES.md) for the full guide.

**Subcommands:**

```bash
repowise workspace list                         # Show all repos with index status
repowise workspace add <path> [--alias NAME]    # Add a repo to the workspace
repowise workspace remove <alias>               # Remove a repo (doesn't delete files)
repowise workspace scan                         # Re-scan for new repos
repowise workspace set-default <alias>          # Change the default repo for MCP queries
```

**Examples:**

```bash
# Initialize a workspace
cd my-workspace/
repowise init .

# Add a repo that lives outside the workspace directory
repowise workspace add /path/to/external-repo --alias api-gateway

# Update all workspace repos
repowise update --workspace

# Update just one repo
repowise update --repo backend
```

---

### `repowise hook`

Manage post-commit git hooks that auto-sync the wiki after every commit.

```bash
repowise hook install              # Install hook for current repo
repowise hook install --workspace  # Install for all workspace repos
repowise hook status               # Check if hooks are installed
repowise hook status --workspace   # Check all workspace repos
repowise hook uninstall            # Remove the hook
repowise hook uninstall --workspace
```

The hook is marker-delimited, so it coexists safely with other tools' hooks (linters, formatters, etc.) in the same `post-commit` file. The hook runs `repowise update` in the background — your terminal is never blocked.

---

## Web UI

Repowise includes a full web dashboard built with Next.js, React, and D3.js.

### Automatic (with Node.js)

If you have Node.js 20+ installed, `repowise serve` handles everything:

```bash
repowise serve
# API: http://localhost:7337
# Web UI: http://localhost:3000
```

On first run, the pre-built frontend is downloaded (~50 MB) and cached in `~/.repowise/web/`. Subsequent runs start instantly.

### Docker (no Node.js needed)

```bash
git clone https://github.com/repowise-dev/repowise.git
cd repowise

# Build the image (one-time)
docker build -t repowise -f docker/Dockerfile .

# Run with your indexed repo's .repowise directory
docker run -p 7337:7337 -p 3000:3000 \
  -v /path/to/your-repo/.repowise:/data \
  -e GEMINI_API_KEY=your-key \
  -e REPOWISE_EMBEDDER=gemini \
  repowise
```

Or with docker compose:

```bash
git clone https://github.com/repowise-dev/repowise.git
cd repowise
export REPOWISE_DATA=/path/to/your-repo/.repowise
export GEMINI_API_KEY=your-key
docker compose -f docker/docker-compose.yml up
```

Open **http://localhost:3000** for the Web UI, **http://localhost:7337** for the API.

### From source (for development)

For working on the frontend code with hot reload:

```bash
# Terminal 1 — API only
repowise serve --no-ui

# Terminal 2 — Frontend with hot reload
cd /path/to/repowise
npm install
REPOWISE_API_URL=http://localhost:7337 npm run dev --workspace packages/web
```

On Windows PowerShell:

```powershell
$env:REPOWISE_API_URL = "http://localhost:7337"
npm run dev --workspace packages/web
```

Open **http://localhost:3000** in your browser.

### Pages & Features

**Dashboard** (`/`)
Home page with aggregate stats (total pages, fresh/stale counts, dead code findings), a list of indexed repositories, and recent job status.

**Repository Overview** (`/repos/[id]/overview`)
A single-page dashboard for each repository that aggregates key health signals. Includes:
- **Health score ring** — composite score (0–100) computed from documentation coverage, freshness, dead code ratio, hotspot density, and ownership silo risk
- **Attention panel** — prioritized list of items needing action (stale docs, high-churn hotspots, dead code findings)
- **Language donut** — breakdown of codebase by programming language
- **Ownership treemap** — visualizes code ownership distribution across modules
- **Hotspots mini** — top high-churn files at a glance
- **Decisions timeline** — recent architectural decisions
- **Module minimap** — compact interactive graph of module relationships
- **Quick actions** — one-click buttons for sync, full re-index, CLAUDE.md generation, and export
- **Active job banner** — shows progress of running pipeline jobs with live polling

The overview page degrades gracefully — each data section loads independently, so partial data (e.g., missing git metadata) still renders a useful dashboard. A "Graph Intelligence" section at the bottom of the overview shows an expandable list of architectural communities (with labels, cohesion scores, and member counts) and an execution flows panel listing the top entry points with their call-path traces.

**Wiki Browser** (`/repos/[id]/wiki/...`)
The heart of repowise. Browse AI-generated documentation for every file and module. Each page includes:
- Rendered markdown with syntax-highlighted code blocks and Mermaid diagrams
- Sticky table of contents
- Freshness badge (fresh / stale / outdated)
- Git history sidebar — commits, churn percentile, top authors, co-change partners, hotspot indicator
- Regenerate button for stale pages
- "Graph Intelligence" section in the right sidebar showing PageRank and betweenness percentile bars, community label, and in/out degree counts

**Dependency Graph** (`/repos/[id]/graph`)
Interactive force-directed graph rendered on HTML Canvas with D3.js. Handles 2000+ nodes. Six view modes:
- **Module view** — hierarchical organization
- **Ego graph** — neighborhood of a selected node
- **Architecture view** — entry point reachability
- **Dead code view** — highlights unreachable files
- **Hot files view** — commit activity heatmap
- **Full graph** — everything

Supports pan, zoom, click-to-inspect, path finding between nodes, filtering by language, and PNG export. Community color mode uses real community labels (derived from Leiden detection) in the legend rather than generic placeholders. Clicking a node in community color mode opens a community detail panel showing members, cohesion score, and neighboring communities. The active color mode is preserved as a URL parameter so links can be shared.

**Search** (`/repos/[id]/search`)
Full-text and semantic search with result cards showing snippets, confidence scores, and links. A global command palette (`Ctrl+K` / `Cmd+K`) is accessible from any page for quick navigation.

**Symbol Index** (`/repos/[id]/symbols`)
Searchable, sortable table of every extracted symbol (functions, classes, methods, interfaces). Click any row to open the symbol drawer, which now includes a right panel showing graph metrics (PageRank and betweenness percentile bars), callers and callees with confidence scores, and heritage relationships (extends/implements) for classes.

**Documentation Coverage** (`/repos/[id]/coverage`)
Donut chart and table showing freshness breakdown. Regenerate stale pages directly from the UI.

**Code Ownership** (`/repos/[id]/ownership`)
Contributor attribution by module or file. Highlights knowledge silos (bus factor risk) where one person owns >80% of a module.

**Hotspots** (`/repos/[id]/hotspots`)
Ranked table of high-churn files with commit counts, churn percentile bars, and owner attribution.

**Dead Code** (`/repos/[id]/dead-code`)
Findings grouped by category (unreachable files, unused exports, zombie packages) with confidence scores, line counts, and safe-to-delete badges. Bulk operations for resolving or acknowledging findings.

**Architectural Decisions** (`/repos/[id]/decisions`)
Browse and manage extracted decisions. View full rationale, affected files, health score, and status.

**Codebase Chat** (`/repos/[id]/chat`)
Ask questions about your codebase in natural language. Streaming responses powered by your configured LLM with real-time tool call visualization. The chat uses the same MCP tools as editor integrations.

**Settings** (`/settings`)
Configure API connection, default provider/model, embedder, and view webhook/MCP setup instructions.

**Workspace Dashboard** (`/workspace`) *(workspace mode only)*
Aggregate stats across all repos, repo cards with file/symbol/coverage counts, and cross-repo intelligence summary.

**Workspace Contracts** (`/workspace/contracts`) *(workspace mode only)*
All detected API contracts (HTTP, gRPC, message topics) with provider/consumer matching, filterable by type and repo.

**Workspace Co-Changes** (`/workspace/co-changes`) *(workspace mode only)*
Cross-repo file pairs ranked by co-change strength.

---

## MCP Integration with AI Editors

### Claude Code

Add to your project's `.claude/settings.json` or run:

```bash
repowise mcp /path/to/your-repo --transport stdio
```

Claude Code auto-detects the `.repowise/.mcp.json` generated by `repowise init`.

### Cursor / Windsurf / Cline

Add an MCP server entry pointing to:

```json
{
  "command": "repowise",
  "args": ["mcp", "/path/to/your-repo", "--transport", "stdio"]
}
```

### Web-based MCP clients

```bash
repowise mcp /path/to/your-repo --transport sse --port 7338
```

Connect to `http://localhost:7338/sse`.

### What AI editors can do with MCP

Once connected, your AI editor can:
- Get an architecture overview before starting any task
- Fetch rich context for files before reading/modifying them (docs, ownership, decisions, freshness)
- Assess modification risk before changing hotspot files
- Understand *why* code is structured a certain way (architectural decisions)
- Search the wiki semantically ("how do we handle retries?")
- Trace dependency paths between modules
- Find dead code to clean up
- Generate architecture diagrams

---

## Proactive Context Enrichment (Hooks)

Repowise automatically enriches AI agent tool calls with codebase graph context via Claude Code hooks. This is installed automatically during `repowise init` — no manual configuration required.

Unlike MCP tools (which agents must explicitly call), hooks fire on every search automatically. Every `Grep` or `Glob` an agent runs gets graph context injected alongside the results, without the agent having to think about it.

### How it works

#### PreToolUse Hook — Grep/Glob enrichment

Whenever an AI agent runs `Grep` or `Glob`, repowise intercepts the call and queries the local `wiki.db` for each matching file. The enrichment is appended to the tool result before the agent sees it:

| Field | What it tells the agent |
|-------|------------------------|
| **Symbols** | Functions, classes, and methods defined in the file |
| **Imported by** | Which files depend on this file (reverse dependency) |
| **Depends on** | What this file imports (forward dependency) |
| **Git signals** | Hotspot status, bus factor, and owner |

Average latency is ~24ms — well under the 500ms target. No LLM calls, no network requests — pure local SQLite queries against `wiki.db`.

#### PostToolUse Hook — Git commit detection

After a successful `git commit`, `git merge`, `git rebase`, `git cherry-pick`, or `git pull`, repowise checks whether the wiki is stale by comparing `HEAD` against the last indexed commit in `.repowise/state.json`. If the wiki is out of date, the agent is notified:

```
Wiki is stale — run `repowise update` to refresh
```

This ensures agents are never silently working from outdated documentation.

### Configuration

Hooks are written to `~/.claude/settings.json` automatically during `repowise init`. The installed configuration:

| Hook type | Matcher | Action |
|-----------|---------|--------|
| `PreToolUse` | `Grep\|Glob` | Query `wiki.db` and prepend graph context to the result |
| `PostToolUse` | `Bash` | Check for git operations and notify if wiki is stale |

Both hooks call the `repowise augment` CLI command internally. Hooks are designed for graceful failure — any error results in a silent exit so a repowise issue never breaks the agent.

### CLI command

```bash
repowise augment    # Not meant to be called manually — invoked by Claude Code hooks
```

### Sample enrichment output

When an agent runs `Grep` or `Glob`, it sees its normal results followed by context like this:

```
[repowise] 2 related file(s) found:

  packages/core/.../page_generator.py
    Symbols: function:_now_iso, class:PageGenerator, method:__init__
    Imported by: init_cmd.py, update_cmd.py, generation/__init__.py
    Depends on: context_assembler.py, base.py, models.py
    Git: HOTSPOT, bus-factor=1, owner=RaghavChamadiya

  packages/cli/.../init_cmd.py
    Symbols: function:_resolve_embedder, function:_register_mcp_with_claude
    Imported by: reindex_cmd.py, search_cmd.py, main.py
    Depends on: update_cmd.py, cost_estimator.py
```

This means an agent that searches for `"PageGenerator"` immediately knows which files depend on it, what it depends on, and that it is a hotspot — without making a separate MCP tool call.

### Relationship to MCP tools

Hooks and MCP tools are complementary:

- **Hooks** — passive, automatic, zero agent effort. Fire on every search regardless of whether the agent is thinking about graph context.
- **MCP tools** — active, on-demand, richer output. Used when the agent needs full documentation, risk assessment, architectural decisions, or dependency tracing.

For most day-to-day coding tasks, hooks provide sufficient context automatically. MCP tools remain the right choice for deeper investigation.

---

## Auto-Sync

repowise supports five methods to keep your wiki in sync with code changes. See [Auto-Sync](AUTO_SYNC.md) for the full guide.

| Method | Command | Best for |
|--------|---------|----------|
| **Post-commit hook** | `repowise hook install` | Set-and-forget local dev |
| **File watcher** | `repowise watch` | Active development |
| **GitHub webhook** | Server endpoint | Teams, CI/CD |
| **GitLab webhook** | Server endpoint | Teams, CI/CD |
| **Polling fallback** | Automatic with `repowise serve` | Safety net |

### Quick setup

```bash
# Post-commit hook (recommended)
repowise hook install
repowise hook install --workspace    # all workspace repos

# File watcher
repowise watch
repowise watch --workspace           # all workspace repos
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | If using Anthropic | Anthropic API key |
| `OPENAI_API_KEY` | If using OpenAI | OpenAI API key |
| `GEMINI_API_KEY` | If using Gemini | Google Gemini API key |
| `REPOWISE_DB_URL` | No | Database URL override (default: `.repowise/wiki.db`) |
| `REPOWISE_EMBEDDER` | No | Embedder for semantic search: `gemini`, `openai`, `mock` |
| `REPOWISE_API_URL` | Frontend only | Backend URL for the web UI (default: `http://localhost:7337`) |
| `REPOWISE_API_KEY` | No | Optional API key to protect the server |

---

## Common Workflows

### First-time setup for a single repo

```bash
pip install "repowise[anthropic]"
export ANTHROPIC_API_KEY="sk-ant-..."
cd /path/to/your-project
repowise init
repowise hook install    # auto-sync after every commit
```

### First-time setup for a multi-repo workspace

```bash
pip install "repowise[anthropic]"
export ANTHROPIC_API_KEY="sk-ant-..."
cd /path/to/workspace/   # parent dir with backend/, frontend/, etc.
repowise init .
repowise hook install --workspace
```

### Daily development workflow

```bash
# Option A: Manual update after pulling
git pull
repowise update

# Option B: Continuous sync while coding
repowise watch

# Option C: Set-and-forget (if hook installed)
# Just code and commit — the hook handles it
```

### Before a code review

```bash
# Check what's at risk
repowise dead-code --safe-only

# Review decision health
repowise decision health

# See hotspots
repowise status
```

### Team onboarding

```bash
# Generate full wiki for the new team member
repowise init --provider openai

# They can browse it
repowise serve

# Or use it in their editor
repowise mcp --transport stdio
```

### CI/CD integration

```bash
# Index-only in CI (free, no LLM calls)
repowise init --index-only

# Export docs as markdown for static hosting
repowise export --format markdown --output ./docs/wiki/
```

### Switching LLM providers

```bash
# Re-generate with a different provider
repowise init --provider openai --model gpt-5.4-nano --force

# Or just change the model for future updates
# (edit .repowise/config.yaml, then:)
repowise update --provider gemini
```

---

## Troubleshooting

**"Provider X requires the Y package"**
Install the optional dependency: `pip install "repowise[anthropic]"` (or openai, gemini, litellm).

**Empty search results with semantic mode**
Check that an embedder is configured. Run `repowise reindex --embedder gemini` to rebuild the vector store.

**"embedder.mock_active" warning**
Set `REPOWISE_EMBEDDER=gemini` (or `openai`) for real vector search. Mock embedder produces random vectors — semantic search won't work meaningfully.

**Stale pages after code changes**
Run `repowise update` to sync. Or use `repowise watch` for automatic syncing.

**Cost seems high**
Use `--dry-run` to see the cost estimate first. Use `--test-run` to validate with just 10 files. Use `--skip-tests --skip-infra` to reduce scope. Lower `--concurrency` to slow down API usage.

**init was interrupted**
Run `repowise init --resume` to pick up where it left off.

**Vector store corrupted**
Run `repowise reindex` to rebuild from existing wiki pages.

**Doctor says database has 0 pages**
The init either failed or was run with `--index-only`. Run `repowise init` with a provider to generate pages.

**Frontend shows empty repo list**
Make sure `REPOWISE_DB_URL` (or `REPOWISE_API_URL` for the frontend) points to the correct database. The backend and frontend must point to the same wiki DB.

**CORS errors in the browser**
Ensure both the backend (`repowise serve`) and frontend (`npm run dev`) are running. The backend allows all origins by default.
