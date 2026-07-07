# MCP Tools Reference

repowise exposes a curated set of tools via the [Model Context Protocol](https://modelcontextprotocol.io) (MCP). These tools give AI coding assistants (Claude Code, Codex, Cursor, Cline, Windsurf) structured access to your codebase intelligence: dependency graph, git history, documentation, and architectural decisions. A single-repo server advertises 10 tools by default; workspace mode adds 3 more automatically. The surface is configurable; see [Configuring the tool surface](#configuring-the-tool-surface).

**Start the MCP server:**

```bash
repowise mcp --transport stdio           # for Claude Code, Codex, Cursor, etc.
repowise mcp --transport streamable-http # for HTTP clients on port 7338
repowise mcp --transport sse --port 7338 # legacy SSE transport
```

**Auto-setup:** `repowise init` automatically registers the MCP server and installs proactive hooks for Claude Code. `repowise init --codex` writes project-local Codex MCP config and hooks.

---

## Tool Overview

| Tool | Purpose | Typical use |
|------|---------|-------------|
| `get_overview` | Architecture summary | First call on any unfamiliar codebase |
| `get_answer` | One-call RAG Q&A | First call on any code question |
| `get_context` | Rich context for targets | Before reading or modifying code |
| `get_symbol` | Raw source bytes for one symbol | When you need one function/class body |
| `search_codebase` | Hybrid symbol / path / concept search | Finding a symbol or file, or discovering code by topic |
| `get_risk` | Modification risk | Before changing hotspot files |
| `get_why` | Architectural decisions | Before structural changes |
| `get_dead_code` | Unreachable code | Cleanup tasks |
| `get_health` | Code-health marker scores | Before refactoring, find the worst files |
| `list_repos` | List the repos a server is serving | Discovering workspace repo aliases |
| `get_blast_radius` | Cross-repo downstream impact (workspace only) | Before changing a service other repos consume |
| `get_conformance` | Architecture rule violations + cycles (workspace only) | Auditing or before changing service boundaries |
| `get_architecture` | System coupling, cyclic core, 1-10 architecture score (workspace only) | Gauging overall structure before a cross-service refactor |

Two further tools are **off by default** and opt-in (see below): `get_dependency_path` (shortest dependency path between two symbols/files) and `get_execution_flows` (top entry points and their call traces).

---

## Configuring the tool surface

The default surface is deliberately small: fewer, richer tools mean fewer round-trips and less schema overhead per task. What a server advertises is resolved from three things: each tool's defaults, whether the server is in workspace mode, and an optional override.

- **Default (single-repo):** the 10 tools above (every tool except the workspace-only three).
- **Default (workspace):** those 10 plus `get_blast_radius`, `get_conformance`, and `get_architecture`, which are added automatically when the server is started inside a workspace. They are never advertised outside one.
- **Opt-in tools:** `get_dependency_path` and `get_execution_flows` are registered but off by default. Turn them on per repo.

**Configure it in `.repowise/config.yaml`** under an `mcp.tools` key. Two shapes are supported:

```yaml
# Adjust the default set with + / - deltas (the common case):
mcp:
  tools: ["+get_execution_flows", "-get_dead_code"]

# Or give an explicit allowlist (only these tools):
mcp:
  tools: ["get_answer", "get_context", "get_symbol", "search_codebase"]

# Or enable everything available in the current mode:
mcp:
  tools: all
```

**Or per launch on the CLI**, which overrides the config block:

```bash
repowise mcp --tools "+get_execution_flows"          # default set plus one
repowise mcp --tools "get_answer,get_context"         # explicit allowlist
repowise mcp --all                                    # every available tool
```

Workspace-only tools named explicitly in single-repo mode are ignored (they cannot do useful work there). Unknown tool names are ignored with a warning.

**Or from the dashboard:** the Settings page lists every tool with its description and a per-repo toggle, and writes the same `mcp.tools` config for you.

---

## Reversible truncation: `_meta.omitted`

Tool responses are token-budgeted. When a response is truncated, the dropped
content is no longer silently lost: it is stored in the repo's
[omission store](DISTILL.md#the-omission-store) and the response's `_meta`
envelope lists how to get it back:

```jsonc
"_meta": {
  "omitted": {
    "refs": ["a1b2c3d4e5f6"],
    "tokens": 5840,
    "restore": "repowise expand <ref> (CLI) or get_symbol(\"repowise#<ref>\", query?) (MCP)"
  }
}
```

Truncated skeleton blocks are replaced in place by a `[repowise#<ref>: ...]`
marker; everything else is captured into one combined document per response.
Resolve refs with `repowise expand <ref>` from a shell, or
`get_symbol("repowise#<ref>")` from any MCP client; the tool count stays at
nine. See [DISTILL.md](DISTILL.md) for the full reversibility model.

---

## `get_overview`

Architecture summary, module map, entry points, git health, and community summary.

**Parameters:** None required.

| Parameter | Type | Description |
|-----------|------|-------------|
| `repo` | string | *(workspace only)* Target repo alias, or `"all"` |

**Returns:** Architecture description, key modules with purpose and owner, entry points, tech stack, hotspot files, knowledge silos, community summary (top communities by size with labels and cohesion scores).

**When to use:** First call on any unfamiliar codebase. Gives the agent a mental map before diving into specifics.

**Example call:**

```
get_overview()
```

---

## `get_answer`

One-call RAG: retrieves over the wiki, gates synthesis on confidence, and returns a cited 2–5 sentence answer.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `question` | string | Yes | Natural language question about the codebase |
| `repo` | string | No | *(workspace only)* Target repo alias |

**Returns:** A synthesized answer with file/symbol citations and a confidence label (`high`, `medium`, `low`). High-confidence answers can be cited directly. Low-confidence answers return ranked wiki excerpts instead.

**When to use:** First call on any code question. Collapses search, read, and reason into one round-trip. If confidence is low, follow up with `search_codebase` to discover candidate pages.

**Example call:**

```
get_answer(question="How does the authentication flow work?")
```

---

## `get_context`

The workhorse tool. Returns docs, symbols, ownership, freshness, and community membership for any combination of files, modules, or symbols.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `targets` | list[string] | Yes | File paths, module names, or symbol IDs. Batch multiple targets in one call. |
| `include` | list[string] | No | Additional data to include: `"full_doc"` (full wiki markdown), `"callers"` (who calls this, symbol targets), `"callees"` (what this calls, symbol targets), `"ownership"` (primary owner, bus factor, contributor count), `"last_change"` (last commit date + author), `"metrics"` (PageRank, betweenness, percentiles), `"community"` (cluster membership + neighbors), `"decisions"` (full decision records; default returns titles only), `"skeleton"` (file targets only; the file with bodies elided: every signature, imports, and the bodies of the most central symbols, token-budgeted; typically ~15% of the full file's tokens) |
| `compact` | boolean | No | Default `true`. Set `false` for full structure block and importer list. |
| `repo` | string | No | *(workspace only)* Target repo alias, or `"all"` |

**Returns per target:** Documentation summary, symbols defined, ownership percentages, freshness score, co-change partners, architectural decisions governing the file. With `include` options: source code, call graph, graph metrics, community membership.

**When to use:** Before reading or modifying code. Pass all relevant targets in one call to minimize round-trips. In workspace mode, enriched with cross-repo co-change and contract data.

**Example calls:**

```
get_context(targets=["src/auth/middleware.ts"])
get_context(targets=["middleware", "api/routes", "payments"], include=["callers", "metrics"])
get_context(targets=["src/auth"], compact=false, include=["community"])
get_context(targets=["src/big_module.py"], include=["skeleton"])
```

**Skeletons:** with `include=["skeleton"]`, file targets gain a structure-level
rendering sliced from the index's persisted symbol bounds (no parsing at query
time): every signature, the import preamble, and the bodies of the top symbols
ranked by graph centrality / hotspot / query match. Elision markers carry
1-indexed line ranges so you can range-`Read` anything back. For
structure-level questions ("what's in this file", "which function handles X")
this replaces a full file read at a fraction of the cost.

---

## `get_symbol`

Raw source bytes for one indexed symbol with exact line bounds, cheaper and
safer than `Read` + offset math. The only tool that returns actual source code.
Also resolves **omission refs** (`repowise#<12-hex>`) from truncated responses.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol_id` | string | Yes | Canonical `"path/to/file.py::SymbolName"` from `get_context`'s symbol list (normalises `::` / `.` / `/` separators across languages), **or** an omission ref `"repowise#<12-hex>"` / a pasted whole `[repowise#…]` marker. |
| `query` | string | No | Omission refs only: return just the stored lines matching this regex (or substring). Ignored for symbol ids. |
| `context_lines` | int | No | Extra source lines before/after the symbol (0–50, default 0) |
| `repo` | string | No | *(workspace only)* Usually omitted; `"all"` is not supported |

**Returns:** For a symbol id: the symbol's source bytes (bounded at ~400 lines),
its exact start/end line numbers, kind, and a `truncated` flag; on a miss, an
`error` with the closest matches. For an omission ref: the stored content plus
provenance (`source`, `created_at`, `original_tokens`).

**When to use:** When you need the body of one function or class: pipe the
`symbol_id` straight from `get_context`'s symbol list. Or when a response's
`_meta.omitted` lists refs you want back and you have no shell for
`repowise expand` (e.g. Claude Desktop).

**Example calls:**

```
get_symbol(symbol_id="src/auth/service.py::AuthService")
get_symbol(symbol_id="src/auth/service.py::login", context_lines=10)
get_symbol(symbol_id="repowise#a1b2c3d4e5f6")
get_symbol(symbol_id="repowise#a1b2c3d4e5f6", query="FAILED")
```

---

## `search_codebase`

Hybrid code search over RepoWise's indexes. A single tool that, depending on
the shape of the query, searches the indexed **symbols**, **file paths**, or
the **wiki**, instead of forcing a fallback to Grep for identifiers.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | Identifier, path, or natural-language query |
| `limit` | int | No | Max results (default 5) |
| `mode` | string | No | `auto` (default) \| `concept` \| `symbol` \| `path` \| `hybrid` |
| `kind` | string | No | `implementation` \| `test` \| `config` \| `doc` |
| `symbol_kind` | string | No | Restrict symbol hits by kind (`function`, `class`, `method`, ...) |
| `page_type` | string | No | `file_page` \| `module_page` \| `symbol_spotlight` (concept mode) |
| `repo` | string | No | *(workspace only)* Target repo alias, or `"all"` to search across workspace |

**Modes:**

- **`auto`** (default) routes by query shape:
  - an **identifier** (`GitIndexer`, `index_repo`) -> searches indexed symbols;
  - a **path** (`core/ingestion/indexer.py`) -> searches file pages;
  - **prose** ("how do we handle retries?") -> wiki-semantic search;
  - mixed prose + identifier -> **hybrid** (symbol hits first, then concept pages).
- **`concept`** forces the original wiki-semantic behavior.
- **`symbol`** / **`path`** force the structural search.

**Returns:**

- *Symbol hits*: `{type: "symbol", symbol_id, name, kind, file, start_line, end_line, signature, next: "get_symbol"}`. Ranked by exact-name/qualified-name match, query-token coverage, then graph centrality (PageRank / betweenness / entry-point); non-test before test unless `kind="test"`.
- *File hits*: `{type: "file", page_id, file, title, next: "get_context"}`.
- *Concept hits*: ranked wiki pages with `relevance_score`, `snippet`, `target_path`, and a `search_method` (`embedding` vs `bm25` fallback).

Tombstoned and `exclude_patterns`-excluded results are filtered. In workspace
mode, structural and concept searches both federate across repos and merge.

**When to use:** Locating a function/class/method by name, resolving a
path-shaped query, or discovering pages by topic: the symbol/file shapes pipe
directly into `get_symbol` / `get_context`.

**Example calls:**

```
search_codebase(query="GitIndexer index_repo")          # → symbol hits
search_codebase(query="core/ingestion/indexer.py")      # → file hits
search_codebase(query="rate limit OR throttle OR retry") # → wiki pages
search_codebase(query="login", mode="symbol", symbol_kind="method")
```

---

## `get_risk`

Modification risk assessment for files or a set of changed files.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `targets` | list[string] | No | File paths to assess |
| `changed_files` | list[string] | No | Files in a PR/changeset for blast radius analysis |
| `repo` | string | No | *(workspace only)* Target repo alias |

**Returns:** Per-file risk score (0–10), hotspot status, dependent count, co-change partners, blast radius, recommended reviewers, test gap analysis, security signals. In workspace mode, enriched with cross-repo co-change partners and contract dependencies.

When `changed_files` is passed, the response leads with a `directive` block. In workspace mode that directive also carries the cross-repo fallout of the changed repo:

- `will_break_consumers`: services in *other* repos that depend on this one (structural impact), each with `repo`, `service`, `distance`, `score`, and the edge kinds carrying the impact.
- `missing_cross_repo_cochanges`: services in other repos that historically co-change with this one but aren't in the diff.
- `breaking_changes`: provider contracts in this repo that changed *incompatibly* since the last index (a removed route or field, a type or field-number change, a newly-required field), each with the changed `contract_id`, the change `kind`/`severity`, and the `impacted_consumers` (repo, service, file) it endangers across repos. Schema-level truth, distinct from the topology-level `will_break_consumers`; non-breaking changes (added optional field, new endpoint) never appear. See [Breaking-Change Guard](WORKSPACES.md#breaking-change-guard).
- `conformance_violations`: declared dependency-rule breaches the diff's repo participates in, each with the offending `source`/`target` services, the `rule` (e.g. `frontend !-> db`), and `edge_kind`. See [Architecture Conformance](WORKSPACES.md#architecture-conformance).
- `dependency_cycles`: circular service dependencies involving this repo, each with the participating `nodes` and `length`.

**When to use:** Before modifying files, especially hotspots. Understand what could break, who to involve in review, and whether tests cover the affected area.

**Example calls:**

```
get_risk(targets=["src/auth/middleware.ts"])
get_risk(changed_files=["src/api/routes.ts", "src/middleware/cors.ts"])
```

---

## `get_blast_radius`

*(Workspace mode only.)* Cross-repo downstream impact: if you change this service, what breaks across the other repos?

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `targets` | list[string] | Yes | Node ids (`repo` or `repo::service/path`) or repo aliases |
| `max_depth` | int | No | Reachability depth (1–8, default 3) |
| `include_behavioral` | bool | No | Include co-change (behavioral) edges (default true) |

**Returns:** The impacted services ranked by impact `score`, each with `distance` (hops), `structural` (a real dependency vs co-change only), and the edge kinds that carried the impact; plus `impacted_repos`, `structural_count` / `behavioral_count`, `total_impacted`, and any `unresolved_targets`.

**When to use:** Before changing a high-fan-out provider, see who consumes it across repo boundaries. Structural impact (`will break`) outweighs behavioral co-change (`may drift`). Reads the same system graph the [Live System Map](WORKSPACES.md#live-system-map) renders.

**Example calls:**

```
get_blast_radius(targets=["backend"])
get_blast_radius(targets=["mono::services/auth"], max_depth=2, include_behavioral=false)
```

---

## `get_conformance`

*(Workspace mode only.)* Architecture governance: does the live system graph obey the declared dependency rules, and are there circular service dependencies?

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `repo` | string | No | Limit findings to those involving this repo alias |

**Returns:** `violations` (each with the offending `source`/`target` services, the `rule_source`/`rule_target` matchers that fired, and the `edge_kind`), `cycles` (each with the participating `nodes` and `length`), and the `violation_count` / `cycle_count` / `rules_evaluated` rollups.

**When to use:** Before a refactor that changes service boundaries, or to audit whether the live architecture still matches the intended one. Rules are declared under `conformance:` in `.repowise-workspace.yaml`. See [Architecture Conformance](WORKSPACES.md#architecture-conformance).

**Example calls:**

```
get_conformance()
get_conformance(repo="frontend")
```

---

## `get_architecture`

*(Workspace mode only.)* The one evaluative read of the whole system: how coupled is it, where is the architectural core, and a single 1-10 architecture score. Deterministic, structural edges only (co-change excluded).

**Parameters:** none.

**Returns:** `score` (1-10), `architecture_type` (`core-periphery` or `hierarchical`), `propagation_cost_pct` (share of other services the average service reaches), `core_size` / `core_ratio` / `core_members` (the largest cyclic group), `cycle_count`, `conformance_violations`, a `role_breakdown` (count of Core / Shared / Control / Peripheral services), and a one-line `summary`.

**When to use:** Before a cross-service refactor, or to gauge and compare overall system structure over time. See [Architecture Metrics](WORKSPACES.md#architecture-metrics).

**Example call:**

```
get_architecture()
```

---

## `get_why`

Architectural decision intelligence. Three modes depending on parameters.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | No | Natural language query about decisions, OR a file path |
| `repo` | string | No | *(workspace only)* Target repo alias |

**Modes:**

1. **NL search**: pass a question: `get_why(query="why JWT over sessions?")` -> searches decision records
2. **Path-based**: pass a file path: `get_why(query="src/auth/service.ts")` -> returns decisions governing that file
3. **Health dashboard**: no args: `get_why()` -> stale decisions, conflicts, ungoverned hotspots

**Returns:** Matching decision records with title, rationale, alternatives considered, affected files, staleness score. Health mode returns stale decisions, conflicts, and ungoverned hotspots.

**When to use:** Before architectural changes, understand existing intent and constraints. After changes, record new decisions.

**Example calls:**

```
get_why(query="rate limiting")
get_why(query="src/payments/processor.ts")
get_why()
```

---

## `get_dead_code`

Unreachable code sorted by confidence tier with cleanup impact estimates.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `min_confidence` | float | No | Minimum confidence threshold (default: 0.4) |
| `include_internals` | boolean | No | Include private/underscore symbols (default: false) |
| `repo` | string | No | *(workspace only)* Target repo alias |

**Returns:** Dead code findings grouped by confidence tier (`safe_to_delete` ≥ 0.70, `review_first` < 0.70). Each finding includes: file path, kind (unreachable_file, unused_export, unused_internal, zombie_package), confidence score, line count, and cleanup impact estimate.

**When to use:** Cleanup tasks. Conservative by design: `safe_to_delete` excludes dynamically-loaded patterns and framework-decorated functions.

**Example calls:**

```
get_dead_code()
get_dead_code(min_confidence=0.8, include_internals=true)
```

---

## `get_health`

Code-health marker scores: the same deterministic markers the
`repowise health` CLI computes, across three signals (defect risk,
maintainability, performance), exposed for agentic workflows. Zero LLM calls.
Use it to **self-check a change before opening a PR**: the same signals a
code-health merge-gate judges it on.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `targets` | list[string] | No | File paths, or `module:foo` to expand a module's file set. Empty means dashboard mode. |
| `include` | list[string] | No | Opt-in blocks (default response stays lean): `"biomarkers"` (findings in dashboard mode), `"refactoring"` (structured, graph-aware refactoring plans; see below), `"trend"` (snapshot diff + declining / predicted-decline alerts), `"coverage"`, `"accuracy"` (the "does the score find the bugs?" stat, dashboard mode), `"signals"` (per-file process / people / topology signals, targeted mode), `"churn_complexity"` (churn × complexity quadrant points, dashboard mode), and a dimension name (`"performance"` / `"defect"` / `"maintainability"`) to filter findings to that pillar. |
| `repo` | string | No | *(workspace only)* Target repo alias |
| `limit` | int | No | Max rows in the lowest-scoring file list (default 20, capped at 50) |

**Returns:** Dashboard mode (no `targets`) returns repo-level KPIs (hotspot
health, average health, worst performer, maintainability / performance pillar
averages), the lowest-scoring files, and a per-module NLOC-weighted rollup.
Targeted mode returns per-file marker findings with severity, per-dimension
scores, and the score breakdown. Each finding carries a `dimension`
(`defect` / `maintainability` / `performance`).

The opt-in enrichments:

- **`accuracy`** returns a `defect_accuracy` block: of the K least-healthy files, how
  many were recently bug-fixed vs the repo-wide base rate (precision@K + `lift`),
  with a per-K table and the flagged files. Silent (`null`) on repos with too
  little history to be honest (< 25 scored files or < 5 recently-fixed files).
- **`signals`** adds a `signals` object on each targeted metric: prior-defect count,
  change scatter, 90-day churn, primary / recent owner, and graph in / out
  degree. Honest `null` per field when the underlying row is absent (never an
  imputed zero).
- **`churn_complexity`** returns `churn_complexity` points (one per recently-changed
  file: 90-day commit count, max CCN, NLOC, score, churn percentile): the
  refactor zone where volatility and tangle collide.
- **`refactoring`** returns ranked, structured refactoring plans (not template
  strings): `extract_class` (the cohesion `groups` to split into), `extract_helper`
  (clone `occurrences` + `suggested_site`), `move_method` (`{method, from_class,
  to_class}`), and `break_cycle` (the import `cut_edges`). Each plan carries its
  `evidence`, `impact_delta`, `effort_bucket`, and `blast_radius`, ranked
  `impact × centrality × blast radius`. Full shapes in
  [`docs/REFACTORING.md`](REFACTORING.md).
- **dimension filter** narrows the returned findings to one pillar. Pair with
  `"biomarkers"` for the full (uncapped) finding set, e.g.
  `include=["biomarkers", "performance"]`.

**When to use:** Before opening a PR, to self-check the files you changed
(`targets=[...], include=["signals"]`) and confirm you are not regressing the
worst files. Before refactoring, find the worst-scoring files and what to fix
first (`include=["accuracy", "churn_complexity"]`). Pair with `get_risk` on
hotspots.

**Example calls:**

```
get_health()
get_health(include=["accuracy", "churn_complexity"])
get_health(include=["biomarkers", "performance"])     # only performance findings
get_health(targets=["src/api/server.py"], include=["signals"])
get_health(targets=["module:src.api"], include=["trend", "refactoring"])
```

---

## Workspace Mode

In workspace mode (initialized with `repowise init .`), all tools accept an optional `repo` parameter:

- **Omit `repo`**: queries the default (primary) repo
- **`repo="backend"`**: targets a specific repo by alias
- **`repo="all"`**: queries across all workspace repos (supported by `search_codebase`, `get_context`, `get_overview`; not supported by `get_symbol`)

The MCP server automatically enriches responses with cross-repo intelligence:
- **Co-change partners** from other repos surfaced in `get_context` and `get_risk`
- **API contract links** (HTTP, gRPC, topics) between repos
- **Package dependencies** between repos
- **Cross-repo blast radius** via the workspace-only `get_blast_radius` tool, and a cross-repo `directive` in `get_risk` PR-mode
- **Breaking-change guard**: incompatible provider-contract changes and the consumers they endanger, in the `get_risk` PR-mode `breaking_changes` directive
- **Architecture conformance**: declared dependency-rule violations and dependency cycles via the workspace-only `get_conformance` tool, and `conformance_violations` / `dependency_cycles` in the `get_risk` PR-mode directive
- **Architecture metrics**: whole-system coupling (propagation cost), the cyclic core, per-service roles, and a deterministic 1-10 architecture score via the workspace-only `get_architecture` tool

---

## Proactive Hooks (Complementary)

In addition to the MCP tools above, `repowise init` installs AI-agent hooks (Claude Code and Codex) that provide **passive, automatic** context enrichment:

- **Claude Code PostToolUse**: broad or zero-result `Grep`/`Glob` calls can be enriched with graph context, and git operations can trigger stale-wiki notices.
- **Codex SessionStart/UserPromptSubmit**: Codex receives concise Repowise MCP workflow guidance when a session or prompt starts.
- **Codex PostToolUse**: after edits or git operations, Codex receives a freshness reminder when indexed context may be stale.

Hooks are lightweight reminders. MCP tools are for deeper, on-demand investigation. See [Auto-Sync](AUTO_SYNC.md) and [Codex Integration](CODEX.md) for details.
