# Workspaces — Multi-Repo Support

Repowise workspaces let you index and analyze multiple repositories together. You get per-repo documentation, graphs, and search, plus cross-repo intelligence: co-change detection, API contract extraction, and package dependency mapping.

---

## Table of Contents

1. [When to Use Workspaces](#when-to-use-workspaces)
2. [Quick Start](#quick-start)
3. [How It Works](#how-it-works)
4. [Workspace Commands](#workspace-commands)
5. [Cross-Repo Intelligence](#cross-repo-intelligence)
6. [Web UI](#web-ui)
7. [MCP Integration](#mcp-integration)
8. [File Layout](#file-layout)
9. [FAQ](#faq)

---

## When to Use Workspaces

Use a workspace when your project spans multiple git repositories that are related:

- A **backend + frontend** in separate repos
- A **monorepo root** with standalone service repos alongside it
- **Microservices** that communicate over HTTP, gRPC, or message topics
- Any set of repos where you want to understand **cross-repo dependencies and co-change patterns**

If you only have a single repo, `repowise init` works as before — no workspace needed.

---

## Quick Start

### 1. Organize your repos

Put related repos under a common parent directory:

```
my-workspace/
  backend/          # git repo
  frontend/         # git repo
  shared-libs/      # git repo
```

Or, if your workspace root is itself a git repo (e.g., a monorepo with sub-repos):

```
my-project/         # git repo (monorepo)
  .git/
  backend/          # git repo
  frontend/         # git repo
```

### 2. Initialize the workspace

```bash
cd my-workspace
repowise init .
```

Repowise will:

1. **Scan** for git repositories (up to 3 levels deep)
2. **Prompt you to select** which repos to index
3. **Ask you to pick a primary repo** (the default for MCP queries)
4. **Walk you through provider setup** (LLM provider, model, cost estimate)
5. **Index each repo** — parse files, build graphs, index git history
6. **Generate documentation** for each repo (unless `--index-only`)
7. **Run cross-repo analysis** — co-changes, API contracts, package deps
8. **Register MCP servers** with Claude Desktop and Claude Code

### 3. Explore

```bash
# Check workspace status
repowise status --workspace

# List workspace repos
repowise workspace list

# Start the web UI
repowise serve

# Search across all repos
repowise search "authentication flow"
```

---

## How It Works

A workspace is a directory containing multiple git repositories, tied together by a config file (`.repowise-workspace.yaml`) and a shared data directory (`.repowise-workspace/`).

Each repo is indexed independently into its own `.repowise/wiki.db` — the same format as single-repo mode. The workspace layer adds cross-repo analysis on top.

### Single-Repo vs Workspace

| Feature | Single-Repo | Workspace |
|---------|-------------|-----------|
| Per-repo docs, graph, search | Yes | Yes (for each repo) |
| Co-change detection | Within repo | Within + across repos |
| API contract extraction | No | Yes (HTTP, gRPC, topics) |
| Package dependency mapping | No | Yes |
| Web UI | Repo pages | Repo pages + workspace dashboard |
| MCP | One server per repo | One server, all repos |

---

## Workspace Commands

### `repowise init .`

Initialize a workspace in the current directory. Scans for git repos, prompts for selection, and indexes everything.

**Options:**

| Flag | Description |
|------|-------------|
| `--index-only` | Parse and analyze without LLM generation (free) |
| `-x, --exclude` | Glob patterns to exclude (e.g., `-x "node_modules/"`) |
| `--yes` | Skip confirmation prompts |
| `--concurrency N` | Max concurrent file parses (default: auto) |

**Example:**

```bash
repowise init . -x "node_modules/" -x "*.lock" -x "vendor/"
```

### `repowise workspace list`

Show all repos in the workspace with their index status.

```bash
repowise workspace list
```

### `repowise workspace add <path>`

Add a new repo to an existing workspace and index it.

```bash
repowise workspace add ../new-service --alias api-gateway
```

### `repowise workspace remove <alias>`

Remove a repo from the workspace (does not delete files).

```bash
repowise workspace remove api-gateway
```

### `repowise workspace scan`

Re-scan the workspace directory for new repos that haven't been added yet.

```bash
repowise workspace scan
```

### `repowise workspace set-default <alias>`

Change which repo is the default for MCP queries.

```bash
repowise workspace set-default backend
```

### `repowise workspace diagnostics`

Explain the cross-repo contract link count — per-repo provider/consumer counts, unmatched consumers grouped by reason, and orphan providers. See [Extraction Diagnostics](#extraction-diagnostics).

```bash
repowise workspace diagnostics            # human-readable report
repowise workspace diagnostics --json     # raw JSON
repowise workspace diagnostics --repo api # limit to one repo
```

---

## Cross-Repo Intelligence

When you initialize a workspace with 2+ repos, repowise runs three types of cross-repo analysis:

### Co-Change Detection

Analyzes git history across repos to find files that frequently change together. For example, if `backend/api/routes.py` and `frontend/src/api/client.ts` are always modified in the same time window, they get a high co-change score.

Useful for:
- Understanding implicit dependencies between repos
- Knowing what frontend files to check when a backend API changes
- Identifying tightly coupled components

### API Contract Extraction

Scans source files for HTTP route handlers, gRPC service definitions, and message topic publishers/subscribers. Then matches providers (servers) with consumers (clients) across repos.

**Supported patterns:**

| Type | Providers | Consumers |
|------|-----------|-----------|
| HTTP | Express, FastAPI, Spring, Laravel, Go (gin/echo/chi) | fetch, axios, requests, httpx |
| gRPC | `.proto` service definitions | gRPC client stubs |
| Topics | Kafka, RabbitMQ, Redis Pub/Sub, NATS producers | Corresponding consumers |

### Package Dependency Scanning

Reads package manifests (`package.json`, `pyproject.toml`, `go.mod`, `pom.xml`, etc.) to detect when one repo depends on another as a package.

---

## System Graph

The contracts, package dependencies, and co-changes above are each a flat list. repowise folds them into a single normalized **system graph** — the one structure every cross-repo view reads. It is rebuilt automatically on every `repowise update --workspace` and persisted to `.repowise-workspace/system_graph.json`.

**Nodes are services, not repos.** A monorepo with three detected service boundaries (a `package.json` / `go.mod` / `Cargo.toml` sub-directory) shows three nodes; the repo is a grouping attribute on each node. A repo with no sub-boundary collapses to a single repo-root node. Each node carries its provider/consumer counts, the contract types it participates in, and flags for orphan/isolated services.

**Edges are typed and honest.** Every edge carries:

- a `kind` — `http`, `grpc`, `event`, `package`, or `co_change`;
- a `match_type` — `exact`, `candidate`, `manual`, or `inferred`;
- a `confidence` and a `weight` (how many underlying contracts / deps / co-changes it aggregates);
- `contract_refs` back-pointers so any view can drill from an edge to its evidence.

Edge direction is uniform: **`source` depends on / calls `target`.** A consumer points to the provider it calls; a dependent repo points to the repo it imports. Structural edges (contracts, package deps) are flagged distinctly from behavioral co-change edges — repowise never conflates "these change together" with "these call each other".

Fetch it over REST with `GET /api/workspace/system-graph`.

## Extraction Diagnostics

When the cross-repo link count looks low, diagnostics explain why. Computed alongside contract matching, they report — per repo and contract type — how many providers and consumers were found, which consumers went unmatched (and why), and which providers have no consumer at all.

```bash
repowise workspace diagnostics            # human-readable report
repowise workspace diagnostics --json     # raw JSON
repowise workspace diagnostics --repo api # limit to one repo
```

The report covers:

- **Provider / consumer counts** per repo, broken down by contract type.
- **Unmatched consumers**, grouped by reason:
  - `no_provider` — no provider anywhere declares a matching route/service/topic.
  - `internal_only` — the only matching provider is in the same repo + service, so the call is intra-service and intentionally not surfaced as a cross-repo link.
  - `unlinked` — a cross-service provider with a matching id exists, but no link formed (a candidate worth inspecting).
- **Orphan providers** — endpoints declared but never consumed by any repo.
- **Weak links** — matched links below the confidence threshold.

The same data is available over REST at `GET /api/workspace/diagnostics` and is embedded in the system graph artifact's `diagnostics` block.

---

## Web UI

Start the web server:

```bash
repowise serve
```

In workspace mode, the web UI adds:

- **Workspace Dashboard** (`/workspace`) — aggregate stats across all repos, repo cards with file/symbol/coverage counts, and cross-repo intelligence summary
- **Contracts View** (`/workspace/contracts`) — all detected API contracts with provider/consumer matching, filterable by type and repo
- **Co-Changes View** (`/workspace/co-changes`) — cross-repo file pairs ranked by co-change strength

The sidebar shows all workspace repos under **Repositories**. Click any repo to access its full per-repo pages (overview, docs, graph, search, hotspots, etc.).

---

## MCP Integration

Workspace init automatically registers MCP servers with Claude Desktop and Claude Code. The MCP server is workspace-aware:

- **Default repo context** — queries go to the primary repo unless you specify otherwise
- **Cross-repo tools** — MCP tools can query across repos and return enriched context with co-change and contract data
- **Repo parameter** — most tools accept an optional `repo` parameter to target a specific repo, or `"all"` to query across the workspace

---

## File Layout

After workspace init, your directory looks like:

```
my-workspace/
  .repowise-workspace.yaml        # Workspace config (repo list, default, settings)
  .repowise-workspace/            # Shared cross-repo data
    cross_repo_edges.json          # Co-change pairs and package deps
    contracts.json                 # Extracted API contracts and links
    system_graph.json              # Service-granular system graph + diagnostics
  .claude/
    CLAUDE.md                      # Workspace-level CLAUDE.md for AI editors
  backend/
    .repowise/                     # Per-repo index data
      wiki.db                      # SQLite database (pages, graph, symbols, git)
      lancedb/                     # Vector embeddings
      config.yaml                  # Repo-level config
      mcp.json                     # MCP server config
  frontend/
    .repowise/
      wiki.db
      lancedb/
      ...
```

### What goes in `.gitignore`

Add these to your `.gitignore`:

```gitignore
.repowise/
.repowise-workspace/
.repowise-workspace.yaml
```

The workspace config and data are local — they reference absolute paths and contain generated analysis that should be rebuilt per-machine.

---

## FAQ

### Can I add repos that live outside the workspace directory?

Yes. Use `repowise workspace add /path/to/external-repo`. The path will be stored relative to the workspace root if possible, or as an absolute path otherwise.

### What happens if I run `repowise init` (without `.`) in a workspace?

It runs in single-repo mode for the current directory, ignoring the workspace. Use `repowise init .` from the workspace root to initialize or re-initialize the workspace.

### Can I have nested workspaces?

No. Repowise searches upward for `.repowise-workspace.yaml` and uses the first one it finds. Nested workspace configs are not supported.

### How do I update a workspace after code changes?

```bash
repowise update              # Update the primary repo
repowise update --workspace  # Update all workspace repos
```

Or use watch mode for automatic updates:

```bash
repowise watch --workspace
```

### How do I re-run just the cross-repo analysis?

Currently, cross-repo analysis runs automatically during `repowise init .` and `repowise update --workspace`. To force a re-run, use `repowise init .` again — it will detect existing indexes and only re-run what's needed.

### Does the MCP server handle multiple repos?

Yes. A single MCP server instance serves all workspace repos. It uses lazy-loading with LRU eviction (max 5 repos loaded simultaneously) to manage memory. The default repo is always kept in memory.
