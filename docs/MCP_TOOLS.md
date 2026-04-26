# MCP Tools Reference

repowise exposes 7 tools via the [Model Context Protocol](https://modelcontextprotocol.io) (MCP). These tools give AI coding assistants (Claude Code, Cursor, Cline, Windsurf) structured access to your codebase intelligence — dependency graph, git history, documentation, and architectural decisions.

**Start the MCP server:**

```bash
repowise mcp --transport stdio           # for Claude Code, Cursor, etc.
repowise mcp --transport sse --port 7338 # for web clients
```

**Auto-setup for Claude Code:** `repowise init` automatically registers the MCP server and installs proactive hooks. No manual configuration needed.

---

## Tool Overview

| Tool | Purpose | Typical use |
|------|---------|-------------|
| `get_overview` | Architecture summary | First call on any unfamiliar codebase |
| `get_answer` | One-call RAG Q&A | First call on any code question |
| `get_context` | Rich context for targets | Before reading or modifying code |
| `search_codebase` | Semantic search | Discovering code by topic |
| `get_risk` | Modification risk | Before changing hotspot files |
| `get_why` | Architectural decisions | Before structural changes |
| `get_dead_code` | Unreachable code | Cleanup tasks |

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

**When to use:** First call on any code question. Collapses search → read → reason into one round-trip. If confidence is low, follow up with `search_codebase` to discover candidate pages.

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
| `include` | list[string] | No | Additional data to include: `"source"` (symbol body), `"callers"` (who calls this), `"callees"` (what this calls), `"metrics"` (PageRank, centrality), `"community"` (cluster membership) |
| `compact` | boolean | No | Default `true`. Set `false` for full structure block and importer list. |
| `repo` | string | No | *(workspace only)* Target repo alias, or `"all"` |

**Returns per target:** Documentation summary, symbols defined, ownership percentages, freshness score, co-change partners, architectural decisions governing the file. With `include` options: source code, call graph, graph metrics, community membership.

**When to use:** Before reading or modifying code. Pass all relevant targets in one call to minimize round-trips. In workspace mode, enriched with cross-repo co-change and contract data.

**Example calls:**

```
get_context(targets=["src/auth/middleware.ts"])
get_context(targets=["middleware", "api/routes", "payments"], include=["callers", "metrics"])
get_context(targets=["src/auth"], compact=false, include=["community"])
```

---

## `search_codebase`

Semantic search over the full wiki. Natural language queries.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | Natural language search query |
| `repo` | string | No | *(workspace only)* Target repo alias, or `"all"` to search across workspace |

**Returns:** Ranked wiki pages with relevance scores, snippets, and file paths.

**When to use:** When `get_answer` returned low confidence and you need to discover candidate pages by topic. Also useful for broad exploration ("how do we handle retries?", "payment processing flow").

In workspace mode, searches across all repos and merges results.

**Example call:**

```
search_codebase(query="rate limit OR throttle OR retry")
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

**When to use:** Before modifying files — especially hotspots. Understand what could break, who to involve in review, and whether tests cover the affected area.

**Example calls:**

```
get_risk(targets=["src/auth/middleware.ts"])
get_risk(changed_files=["src/api/routes.ts", "src/middleware/cors.ts"])
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

1. **NL search** — pass a question: `get_why(query="why JWT over sessions?")` → searches decision records
2. **Path-based** — pass a file path: `get_why(query="src/auth/service.ts")` → returns decisions governing that file
3. **Health dashboard** — no args: `get_why()` → stale decisions, conflicts, ungoverned hotspots

**Returns:** Matching decision records with title, rationale, alternatives considered, affected files, staleness score. Health mode returns stale decisions, conflicts, and ungoverned hotspots.

**When to use:** Before architectural changes — understand existing intent and constraints. After changes — record new decisions.

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

**When to use:** Cleanup tasks. Conservative by design — `safe_to_delete` excludes dynamically-loaded patterns and framework-decorated functions.

**Example calls:**

```
get_dead_code()
get_dead_code(min_confidence=0.8, include_internals=true)
```

---

## Workspace Mode

In workspace mode (initialized with `repowise init .`), all tools accept an optional `repo` parameter:

- **Omit `repo`** — queries the default (primary) repo
- **`repo="backend"`** — targets a specific repo by alias
- **`repo="all"`** — queries across all workspace repos (supported by `search_codebase`, `get_context`, `get_overview`)

The MCP server automatically enriches responses with cross-repo intelligence:
- **Co-change partners** from other repos surfaced in `get_context` and `get_risk`
- **API contract links** (HTTP, gRPC, topics) between repos
- **Package dependencies** between repos

---

## Proactive Hooks (Complementary)

In addition to the 7 MCP tools, `repowise init` installs Claude Code hooks that provide **passive, automatic** context enrichment:

- **PreToolUse** — every `Grep`/`Glob` call is enriched with graph context (symbols, importers, dependencies, git signals) at ~24ms latency
- **PostToolUse** — after git commits, the agent is notified when the wiki is stale

Hooks fire automatically on every search. MCP tools are for deeper, on-demand investigation. See [Auto-Sync](AUTO_SYNC.md) for details.
