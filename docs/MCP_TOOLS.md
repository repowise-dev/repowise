# MCP Tools Reference

repowise exposes 9 tools via the [Model Context Protocol](https://modelcontextprotocol.io) (MCP). These tools give AI coding assistants (Claude Code, Codex, Cursor, Cline, Windsurf) structured access to your codebase intelligence — dependency graph, git history, documentation, and architectural decisions.

**Start the MCP server:**

```bash
repowise mcp --transport stdio           # for Claude Code, Codex, Cursor, etc.
repowise mcp --transport sse --port 7338 # for web clients
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
| `search_codebase` | Semantic search | Discovering code by topic |
| `get_risk` | Modification risk | Before changing hotspot files |
| `get_why` | Architectural decisions | Before structural changes |
| `get_dead_code` | Unreachable code | Cleanup tasks |
| `get_health` | Code-health biomarker scores | Before refactoring — find the worst files |

---

## Reversible truncation — `_meta.omitted`

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
`get_symbol("repowise#<ref>")` from any MCP client — the tool count stays at
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
| `include` | list[string] | No | Additional data to include: `"full_doc"` (full wiki markdown), `"callers"` (who calls this — symbol targets), `"callees"` (what this calls — symbol targets), `"ownership"` (primary owner, bus factor, contributor count), `"last_change"` (last commit date + author), `"metrics"` (PageRank, betweenness, percentiles), `"community"` (cluster membership + neighbors), `"decisions"` (full decision records; default returns titles only), `"skeleton"` (file targets only — the file with bodies elided: every signature, imports, and the bodies of the most central symbols, token-budgeted; typically ~15% of the full file's tokens) |
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

Raw source bytes for one indexed symbol with exact line bounds — cheaper and
safer than `Read` + offset math. The only tool that returns actual source code.
Also resolves **omission refs** (`repowise#<12-hex>`) from truncated responses.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol_id` | string | Yes | Canonical `"path/to/file.py::SymbolName"` from `get_context`'s symbol list (normalises `::` / `.` / `/` separators across languages), **or** an omission ref `"repowise#<12-hex>"` / a pasted whole `[repowise#…]` marker. |
| `query` | string | No | Omission refs only — return just the stored lines matching this regex (or substring). Ignored for symbol ids. |
| `context_lines` | int | No | Extra source lines before/after the symbol (0–50, default 0) |
| `repo` | string | No | *(workspace only)* Usually omitted; `"all"` is not supported |

**Returns:** For a symbol id: the symbol's source bytes (bounded at ~400 lines),
its exact start/end line numbers, kind, and a `truncated` flag; on a miss, an
`error` with the closest matches. For an omission ref: the stored content plus
provenance (`source`, `created_at`, `original_tokens`).

**When to use:** When you need the body of one function or class — pipe the
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

## `get_health`

Code-health biomarker scores — the same 25 deterministic biomarkers the
`repowise health` CLI computes, exposed for agentic workflows. Zero LLM calls.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `targets` | list[string] | No | File paths, or `module:foo` to expand a module's file set. Empty → dashboard mode. |
| `include` | list[string] | No | `"refactoring"` (rule-based suggestions), `"trend"` (snapshot diff + declining / predicted-decline alerts), `"coverage"` |
| `repo` | string | No | *(workspace only)* Target repo alias |
| `limit` | int | No | Max rows in the lowest-scoring file list (default 20, capped at 50) |

**Returns:** Dashboard mode (no `targets`) returns repo-level KPIs (hotspot
health, average health, worst performer), the lowest-scoring files, and a
per-module NLOC-weighted rollup. Targeted mode returns per-file biomarker
findings with severity and the score breakdown.

**When to use:** Before refactoring — find the worst-scoring files and what to
fix first. Pair with `get_risk` on hotspots.

**Example calls:**

```
get_health()
get_health(include=["refactoring"])
get_health(targets=["src/api/server.py"])
get_health(targets=["module:src.api"], include=["trend"])
```

---

## Workspace Mode

In workspace mode (initialized with `repowise init .`), all tools accept an optional `repo` parameter:

- **Omit `repo`** — queries the default (primary) repo
- **`repo="backend"`** — targets a specific repo by alias
- **`repo="all"`** — queries across all workspace repos (supported by `search_codebase`, `get_context`, `get_overview`; not supported by `get_symbol`)

The MCP server automatically enriches responses with cross-repo intelligence:
- **Co-change partners** from other repos surfaced in `get_context` and `get_risk`
- **API contract links** (HTTP, gRPC, topics) between repos
- **Package dependencies** between repos

---

## Proactive Hooks (Complementary)

In addition to the 9 MCP tools, `repowise init` installs AI-agent hooks (Claude Code and Codex) that provide **passive, automatic** context enrichment:

- **Claude Code PostToolUse** — broad or zero-result `Grep`/`Glob` calls can be enriched with graph context, and git operations can trigger stale-wiki notices.
- **Codex SessionStart/UserPromptSubmit** — Codex receives concise Repowise MCP workflow guidance when a session or prompt starts.
- **Codex PostToolUse** — after edits or git operations, Codex receives a freshness reminder when indexed context may be stale.

Hooks are lightweight reminders. MCP tools are for deeper, on-demand investigation. See [Auto-Sync](AUTO_SYNC.md) and [Codex Integration](CODEX.md) for details.
