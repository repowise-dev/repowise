# Workspaces — Multi-Repo Support

Repowise workspaces let you index and analyze multiple repositories together. You get per-repo documentation, graphs, and search, plus cross-repo intelligence: co-change detection, API contract extraction, and package dependency mapping.

---

## Table of Contents

1. [When to Use Workspaces](#when-to-use-workspaces)
2. [Quick Start](#quick-start)
3. [How It Works](#how-it-works)
4. [Workspace Commands](#workspace-commands)
5. [Cross-Repo Intelligence](#cross-repo-intelligence)
6. [Architecture Metrics](#architecture-metrics)
7. [Web UI](#web-ui)
8. [MCP Integration](#mcp-integration)
9. [File Layout](#file-layout)
10. [FAQ](#faq)

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

### `repowise workspace check`

Architecture lint: check the declared `conformance:` rules against the system graph and detect dependency cycles. Exits non-zero on any finding, so it gates CI. See [Architecture Conformance](#architecture-conformance).

```bash
repowise workspace check                  # human-readable report; exit 1 on findings
repowise workspace check --json           # raw report JSON
```

### `repowise workspace metrics`

Architecture-complexity metrics: propagation cost, the cyclic core, per-service roles, and a deterministic 1-10 architecture score. See [Architecture Metrics](#architecture-metrics).

```bash
repowise workspace metrics                # human-readable summary
repowise workspace metrics --json         # raw metrics JSON
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

HTTP routes are matched on their **full** path: a router mount prefix
(`APIRouter(prefix=...)`, `include_router(prefix=...)`, Express `app.use('/x', router)`,
Go route groups) is stitched onto each handler path before matching. A client call
whose base URL is an unresolved placeholder (`fetch(\`${API_BASE}/users\`)`) matches
on the host-relative path; the link is **exact** when exactly one workspace service
provides that path and a lower-confidence **candidate** when the target is ambiguous.

**Tuning extraction** via the `contracts:` block in `.repowise-workspace.yaml`:

```yaml
contracts:
  # Map a consumer base token or absolute host to the repo it targets, so a
  # call whose base is unresolved at parse time links as an exact match.
  service_bases:
    API_BASE: backend          # ${API_BASE}/... -> the "backend" repo
    api.example.com: backend    # https://api.example.com/... -> "backend"
  # Extra globs to skip (added to the built-in test/spec defaults).
  exclude_globs:
    - "generated/**"
```

Files under test trees (`tests/`, `test/`, `__tests__/`, `spec/`, `e2e/`) and test
files (`test_*.py`, `*.test.ts`, `*.spec.ts`, ...) are excluded by default: a route
or topic that exists only in a test is a fixture, not a service contract. Calls to a
literal third-party host (Stripe, Formspree, ...) that is not a workspace service are
excluded from matching and reported under the `external_host` diagnostics reason.

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

Fetch it over REST with `GET /api/workspace/system-graph`, or explore it visually in the [Live System Map](#live-system-map).

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
  - `external_host` — the call targets a literal third-party host (Stripe, Formspree, ...) that is not a workspace service, so it is intentionally excluded from matching.
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
- **System Map** (`/workspace/system-map`) — the [Live System Map](#live-system-map): a code-derived diagram of services and their typed relationships
- **Contracts View** (`/workspace/contracts`) — all detected API contracts with provider/consumer matching, filterable by type and repo
- **Co-Changes View** (`/workspace/co-changes`) — cross-repo file pairs ranked by co-change strength

The sidebar shows all workspace repos under **Repositories**. Click any repo to access its full per-repo pages (overview, docs, graph, search, hotspots, etc.).

### Live System Map

The System Map renders the [system graph](#system-graph) as an always-current diagram. It is the visual counterpart to the REST endpoint — the same nodes and edges, laid out and explorable, never a hand-drawn picture.

- **Service nodes**, coloured by category (service, frontend, worker, library, external), with a health ring rolled up from the owning repo and small flags for orphan or isolated services.
- **Typed edges** distinguished by `kind` (colour + glyph) and by `match_type` (solid for exact/manual, dashed for candidate, dotted for inferred co-change). Behavioral co-change edges read differently from structural contract/dependency edges.
- **Filters** to toggle each edge kind on or off, and a **service ↔ repo** switch that collapses a monorepo's services into one node per repository.
- **Drill-down**: click a service to inspect its providers/consumers and connected services; click an edge to see its match type, confidence, weight, and the underlying contract evidence, with a jump to the Contracts view.
- A **legend** explaining the edge colours, dash patterns, and the health scale.

The map appears once the workspace has at least two indexed repositories with detected relationships; it shows honest empty states otherwise.

---

## Cross-Repo Blast Radius

Blast radius answers a single question: **if I change this service, what downstream services and repos break?** It walks the [system graph](#system-graph) *against* its edge direction — a `consumer → provider` edge means changing the provider impacts the consumer — and returns every reachable service ranked by an impact score.

Two edge classes are weighted and labelled distinctly:

- **Structural** edges (http / grpc / event / package) assert a real dependency — a contract or an import. They propagate impact at full weight and surface as **will break**.
- **Behavioral** co-change edges only assert that two files historically *changed together*. They are correlation, not a call, so they propagate at half weight (one named constant, `BEHAVIORAL_EDGE_WEIGHT`) and surface as **may drift**.

Each impacted service carries its `distance` (hops from the change) and `score` (0-1, with distance decay and the behavioral weighting baked in). Nearer, structural impact ranks highest.

Use it three ways:

- **REST** — `GET /api/workspace/blast-radius?target=<node-id-or-repo>&max_depth=3&include_behavioral=true`. `target` is a node id (`repo` or `repo::service/path`) or a repo alias (expands to all its services).
- **MCP** — the `get_blast_radius` tool (workspace mode) gives an agent the impacted set before it touches a high-fan-out provider. The `get_risk` PR-mode directive also gains `will_break_consumers` and `missing_cross_repo_cochanges` so a diff in one repo flags its cross-repo fallout.
- **System Map** — pick a service in the **Blast radius** control above the map; the reachable set ripples (highlighted, the rest dimmed, badges grading intensity), and a side panel lists the impacted services. Click any impacted service to walk the impact outward from there.

---

## Breaking-Change Guard

Where blast radius answers *what could be affected*, the breaking-change guard answers a sharper question: **did a provider change in a way that actually breaks its consumers?** On every `repowise update --workspace`, the freshly-extracted contracts are diffed against the previously-indexed set and each incompatible provider change is reported with the exact consumer files that call it.

Detected change kinds (a registry — adding a kind is one new rule, never an `if/elif`):

| Kind | Severity | Fires when |
|------|----------|-----------|
| `removed_endpoint` | breaking | A provider route / gRPC method / topic that existed before is gone |
| `removed_field` | breaking (response) / warning (request) | A request or response field disappeared |
| `field_type_changed` | breaking | A field's type changed (e.g. `string → int64`) |
| `field_number_changed` | breaking | A proto field's wire number changed |
| `field_required` | breaking | A field became required, or a new required field was added |

**Non-breaking changes never flag** — an added *optional* field, a widened set, or a brand-new endpoint produces no record. Field-level diffs need a contract *schema*; today gRPC carries one (proto message fields, recovered by the existing proto parser), and HTTP gains field-level checks when an OpenAPI spec is present. Route-level removal is detected for every transport from the contract id alone.

Impacted consumers are resolved from the matched contract links — the same provider↔consumer evidence the [system graph](#system-graph)'s edges are built from — so impact is endpoint-precise (the consumer file that calls the changed contract) and direct (the first reachability hop, which is exactly what a contract break endangers; transitive ripple stays the job of blast radius).

Use it three ways:

- **REST** — `GET /api/workspace/breaking-changes` returns the report from the most recent update (filterable by `repo` or `severity`). Each change carries its provider, detail, and impacted consumers with both code sides.
- **MCP** — the `get_risk` PR-mode directive gains a `breaking_changes` block listing the provider contracts that changed incompatibly in the diff's repo and the consumers they endanger, across repos.
- **System Map** — toggle **Breaking changes** above the map: changed providers are badged with their breaking count, the consumers they endanger are badged *at risk*, and the seams between them are highlighted (additive overlay — the map stays whole). A side panel lists each change with both the provider and consumer files.

---

## Architecture Conformance

Workspaces let you declare, in `.repowise-workspace.yaml`, which services are *allowed* to depend on which others, and then continuously check the live system graph against those rules. This is your team's **architecture lint**: the intended architecture, expressed as code, verified on every update.

### Declaring rules

Conformance rules live in a `conformance:` block in the workspace config (no separate file). Each rule has a `source` and a `target` *matcher* and an `allow` flag:

```yaml
repos:
  - path: web
    alias: frontend
    tags: [ui, edge]
  - path: services/db
    alias: db
    tags: [data]

conformance:
  rules:
    # Deny rules (allow defaults to false): the dependency is a violation.
    - source: frontend
      target: db
      description: The UI must call the API, never the database directly.
    - source: "*"
      target: legacy-payments
    # Tag-based: nothing in the "ui" tier may depend on the "data" tier...
    - source: "tag:ui"
      target: "tag:data"
    # ...except migrations, which are explicitly allowed (an exception).
    - source: migrations
      target: db
      allow: true
```

A **matcher** resolves against service nodes in the [system graph](#system-graph):

| Matcher form | Matches |
|--------------|---------|
| `*` | every service |
| `tag:<name>` | every service whose repo declares that tag (see `tags:` on each repo) |
| anything else | a glob over the node id, repo alias, and display name (`frontend`, `api::*`, `*-worker`) |

A rule with `allow: false` (the default) is a **deny** rule: a structural dependency from a matching source to a matching target is a violation. A rule with `allow: true` is an **exception** that whitelists an otherwise-denied edge. Only structural edges (HTTP, gRPC, event, package, db) are evaluated; behavioral co-change is never a dependency.

### Dependency cycles

Independently of any rules, conformance detects **circular dependencies** among services over structural edges (`A → B → … → A`). A cycle means the services cannot be built, deployed, or reasoned about independently. Cycle detection runs even with zero rules declared, so every workspace gets it for free.

### Using it

- **CLI** — `repowise workspace check` prints violations and cycles and exits non-zero when any are found, so it gates CI (the architecture lint):

  ```bash
  repowise workspace check          # human-readable report; exit 1 on findings
  repowise workspace check --json    # raw report JSON (still exits 1 on findings)
  ```

  It recomputes from the persisted system graph, so editing rules and re-running picks them up without a full re-index.
- **REST** — `GET /api/workspace/conformance` returns the report from the most recent update (filterable by `repo`).
- **MCP** — `get_conformance` exposes violations and cycles to an agent; the `get_risk` PR-mode directive gains `conformance_violations` and `dependency_cycles` blocks for the findings the diff's repo participates in.
- **Conformance view** — the web UI's Conformance page renders a **dependency-structure matrix (DSM)**: services on both axes, each filled cell a dependency tinted by transport, with rule violations ringed red and cycle cells amber. Governance panels list the violations and cycles. Violations also badge the offending edges on the [Live System Map](#live-system-map) (toggle **Conformance**), reusing the same additive overlay as the breaking-change guard.

---

## Architecture Metrics

Conformance and the cycle finder answer *per-relationship* questions (is this edge allowed, is this loop a cycle). Architecture metrics give the one *evaluative* read of the whole system: how coupled it is, where its architectural core is, and a single score you can track over time and compare across workspaces. These are the standard MacCormack / Baldwin / Sturtevant architecture-complexity metrics, computed deterministically over the system graph — no LLM. They use **structural edges only** (http / grpc / event / package / db); co-change is excluded.

### What it computes

- **Propagation cost** — the share of *other* services the average service can reach transitively through dependencies (0% = fully decoupled, 100% = everything reaches everything). The headline coupling number; lower is better.
- **Cyclic core** — the largest cyclic group of services (the largest strongly-connected component of the structural graph). Its size and ratio (core / services) describe how much of the system is tangled together.
- **Architecture type** — `core-periphery` when the core spans a meaningful fraction of the system, else `hierarchical`.
- **Per-service role** — each service is classified from its visibility profile:
  - **Core** — in the largest cyclic group (the architectural center).
  - **Shared** — high visibility fan-in, low fan-out: many services depend on it, it depends on few (a widely-used utility/library).
  - **Control** — high fan-out, low fan-in: it depends on many, few depend on it (an orchestrator / entry point).
  - **Peripheral** — lightly coupled in both directions.
- **Architecture score** — a deterministic 1-10 roll-up (matching the Code Health 1-10 convention) from propagation cost, core ratio, dependency-cycle count, and declared-rule violation count. Lower coupling and a smaller core score higher.

### Using it

- **CLI** — `repowise workspace metrics` prints the score, propagation cost, cyclic core, dependency-cycle count, and the per-role service breakdown. CI-friendly plain output; `--json` emits the raw metrics.

  ```bash
  repowise workspace metrics          # human-readable summary
  repowise workspace metrics --json    # raw metrics JSON
  ```

- **REST** — `GET /api/workspace/architecture` returns the workspace metrics plus the per-service roles. Computed at request time from the system graph (no separate artifact); the conformance violation count, if a report exists, is folded into the score.
- **MCP** — `get_architecture` gives an agent the score, propagation cost, core members, and role breakdown in one call — the system-structure read to consult before a cross-service refactor.
- **Web** — the **architecture score** appears as a stat on both the Conformance and System Map pages. The DSM header shows score / propagation cost / core size and tints each service's diagonal cell by its role, so the on-diagonal core block stands out. On the Live System Map, toggle **Core** to highlight the cyclic core, and the inspector shows any selected service's role and visibility profile.

---

## MCP Integration

Workspace init automatically registers MCP servers with Claude Desktop and Claude Code. The MCP server is workspace-aware:

- **Default repo context** — queries go to the primary repo unless you specify otherwise
- **Cross-repo tools** — MCP tools can query across repos and return enriched context with co-change and contract data; `get_blast_radius` answers cross-repo downstream impact (see [Cross-Repo Blast Radius](#cross-repo-blast-radius)); `get_conformance` answers architecture rule violations and dependency cycles (see [Architecture Conformance](#architecture-conformance)); `get_architecture` answers whole-system coupling, the cyclic core, and the architecture score (see [Architecture Metrics](#architecture-metrics))
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
    breaking_changes.json          # Breaking provider changes vs the last index
    conformance.json               # Architecture rule violations + dependency cycles
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
