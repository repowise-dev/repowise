# MCP Tools Reference

repowise exposes a curated set of tools via the [Model Context Protocol](https://modelcontextprotocol.io) (MCP). These tools give AI coding assistants (Claude Code, Codex, Cursor, Cline, Windsurf) structured access to your codebase intelligence: dependency graph, git history, documentation, and architectural decisions.

17 tools are registered in total. A single-repo server advertises 10 by default: exactly the canonical tools. Workspace mode adds the `list_repos` discovery utility, for 11. 6 specialist tools are opt-in where eligible. The surface is configurable; see [Configuring the tool surface](#configuring-the-tool-surface).

**Start the MCP server:**

```bash
repowise mcp --transport stdio           # for Claude Code, Codex, Cursor, etc.
repowise mcp --transport streamable-http # for HTTP clients on port 7338
repowise mcp --transport sse --port 7338 # legacy SSE transport
```

**Auto-setup:** `repowise init` automatically registers the MCP server and installs proactive hooks for Claude Code. `repowise init --codex` writes project-local Codex MCP config and hooks.

**Opting out:** each Claude config holds a single `repowise` MCP key, so indexing a second repo repoints it rather than adding a second entry. Pass `repowise init --no-editor-setup` (or set `REPOWISE_SKIP_EDITOR_SETUP=1`) for a repo you do not want registered: a scratch clone, a worktree, a CI or benchmark run. Nothing about the index changes, and re-running `repowise init` without the flag registers it later. `init` also prints a notice when it is about to repoint an existing entry.

---

## Contents

**Canonical tools (default in both modes, 10)**
[get_overview](#get_overview) &middot;
[get_answer](#get_answer) &middot;
[get_context](#get_context) &middot;
[get_symbol](#get_symbol) &middot;
[search_codebase](#search_codebase) &middot;
[get_risk](#get_risk) &middot;
[get_change_risk](#get_change_risk) &middot;
[get_why](#get_why) &middot;
[get_dead_code](#get_dead_code) &middot;
[get_health](#get_health)

**Workspace discovery utility (default in workspace mode, 1)**
[list_repos](#list_repos)

**Opt-in specialists (6; workspace eligibility still applies)**
[get_architecture](#get_architecture) &middot;
[get_blast_radius](#get_blast_radius) &middot;
[get_dependency_path](#get_dependency_path) &middot;
[get_execution_flows](#get_execution_flows) &middot;
[generate_refactoring_code](#generate_refactoring_code) &middot;
[get_conformance](#get_conformance)

Also see [Configuring the tool surface](#configuring-the-tool-surface), [Reversible truncation](#reversible-truncation-_metaomitted) and [Unrecognised arguments](#unrecognised-arguments-ignored_arguments).

---

## The ten flagship tools

| Tool | Purpose | Typical use |
|------|---------|-------------|
| `get_overview` | Architecture summary | First call on any unfamiliar codebase |
| `get_answer` | One-call RAG Q&A | First call on any code question |
| `get_context` | Rich context for targets | Before reading or modifying code |
| `get_symbol` | Raw source bytes for one symbol | When you need one function/class body |
| `search_codebase` | Hybrid symbol / path / concept search | Finding a symbol or file, or discovering code by topic |
| `get_risk` | Modification risk | Before changing hotspot files |
| `get_change_risk` | What a commit or range newly made worse | Before merging a commit or PR range |
| `get_why` | Architectural decisions | Before structural changes |
| `get_dead_code` | Unreachable code | Cleanup tasks |
| `get_health` | Code-health marker scores | Before refactoring, find the worst files |

In workspace mode, `list_repos` is also on by default so repository aliases are discoverable. It is unavailable in single-repo mode because the server is already bound to the only repository. See [Supplementary tools](#supplementary-tools).

---

## Configuring the tool surface

The default surface is deliberately small: fewer, richer tools mean fewer round-trips and less schema overhead per task. What a server advertises is resolved from three things: each tool's `default`/`requires_workspace` metadata, whether the server is in workspace mode, and an optional override.

- **Default (single-repo):** 10 tools, exactly the canonical intelligence set.
- **Default (workspace):** those 10 plus `list_repos`, the workspace discovery utility.
- **Opt-in tools:** `get_dependency_path`, `get_execution_flows`, and `generate_refactoring_code` are eligible in either mode. `get_architecture`, `get_blast_radius`, and `get_conformance` are workspace-only. All six are off by default.

**Configure it in `.repowise/config.yaml`** under an `mcp.tools` key. Four shapes are supported:

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

# Or select the agent-lean profile (see below):
mcp:
  tools: lean
```

**Or per launch on the CLI**, which overrides the config block:

```bash
repowise mcp --tools "+get_execution_flows"          # default set plus one
repowise mcp --tools "get_answer,get_context"         # explicit allowlist
repowise mcp --tools lean                             # agent-lean profile
repowise mcp --all                                    # every available tool
```

Workspace-only tools named explicitly in single-repo mode are ignored (they cannot do useful work there). Unknown tool names are ignored with a warning.

**The `lean` profile** is the agent-lean surface: `get_answer`, `get_context`, `get_symbol`, `search_codebase`, `get_risk`, and `get_why`, plus `list_repos` in workspace mode (where repo aliases must be discoverable). `get_why` is part of the lean set because why/history questions are the category no code-search surface can answer from the tree alone; a lean profile without it measurably underperforms on exactly those questions. The profile advertises ~2.1k tokens of schema versus ~4.1k for the default surface. That is small enough to keep always loaded, so when a repo has `mcp.tools: lean` configured, `repowise init` skips the tool-search recommendation (the `ENABLE_TOOL_SEARCH` setting that defers MCP schemas behind a lookup round trip) for Claude Code; the six schemas the agent actually reaches for stay in context on every turn. init never turns an existing `ENABLE_TOOL_SEARCH` setting off, since it applies to every MCP server, not just repowise.

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
A response that would still oversize sheds whole blocks, in an order each tool
declares cheapest-loss-first, and reports `truncated: true` alongside the refs.

Every tool is budgeted. Most declare their own shed order; the rest meet a
final size guard that trims the largest blocks and records what it took. Either
way no response is returned unbounded and unflagged, and
`_meta.response_budget` reports the ceiling that applied and the size delivered
under it.
Resolve refs with `repowise expand <ref>` from a shell, or
`get_symbol("repowise#<ref>")` from any MCP client. See
[DISTILL.md](DISTILL.md) for the full reversibility model.

**The `_meta` envelope** (all fields optional, present only when meaningful):

| Field | When present |
|-------|--------------|
| `timing_ms` | Tool wall-time |
| `hint` | A short, conservative follow-up suggestion. On a `get_answer` reply that graded `low` while the index is behind live HEAD, it says to run `repowise update` and ask again before trusting the answer |
| `cached` | Only when `true` |
| `index_age_days` | Days since the last `repowise update` |
| `indexed_commit` | Short (12-char) SHA the index was built against |
| `live_head` | Short (12-char) SHA of the current checkout, whenever `.git/HEAD` is readable. Equal to `indexed_commit` when the index is current |
| `stale_warning` | Only on a real signal: HEAD mismatch **that actually changed files**, or age over ~90 days when git is unreachable. Two commits with identical trees (an empty commit, a no-op merge) report `index_behind` with no warning |
| `index_behind` | Whenever the live-vs-indexed comparison ran: `true` if HEAD has moved (alongside `stale_warning` when served content actually changed), `false` if the commits match. Absent means the comparison could not run (no git, or a repo-level tool that serves no file content) |
| `embedder_degraded` | Whenever an embedder is resolved, `true` or `false`. Absent means none was initialised |
| `embedder`, `embedder_warning` | Only when the embedder fell back to a mock/degraded mode |
| `response_budget` | Always: `limit_chars` (the ceiling that applied), `tier` (`default` or `expanded`, chosen by whether the call passed an expansion argument), `serialized_chars` (the size delivered) |
| `scope_hint` | `get_context` and `get_answer`, when knowledge-graph layers exist that contain none of the served paths: one sentence naming up to three of them with file counts, so an agent knows which areas the answer did not touch |
| `complete` | When the response served whole units: how many symbol bodies (bounds verified against the live file) or whole files, and that they need not be re-opened. Sliced bodies and partial ranges are never counted |
| `state` | Only when something fired: `degraded` plus `degraded_reasons` mapping each contributing key to its reason (a synthesis reason string, the retrieval legs that broke), `partial`, `truncated`. A coarse roll-up of the response's own flags |

Silence on `stale_warning` means the index is current; don't infer staleness from its absence. `list_repos`, `get_architecture`, `get_blast_radius`, and `get_conformance` don't carry a freshness envelope at all. Neither does `search_codebase` when a workspace call merges results from several repos, since there is no single indexed commit to compare.

---

## Unrecognised arguments: `ignored_arguments`

A tool never answers a bad argument with a filter that matches nothing. A value
outside a closed vocabulary is **dropped, not applied** — so the response is the
one you would have got without it — and the tool names what it dropped, at the
top level:

```jsonc
"ignored_arguments": [
  { "argument": "kind",
    "values": ["unused_exports"],
    "valid": ["unreachable_file", "unused_export", "unused_internal", "zombie_package"] }
]
```

The key is absent when every argument was understood, so its presence is the
whole signal. One entry per argument, however many of its values missed.

This exists because the alternative is a lie: `get_dead_code(kind="unused_exports")`
used to filter on the plural, match nothing, and recommend *"No dead code found
matching your filters."* beside a summary counting hundreds of unused exports
([#1496](https://github.com/repowise-dev/repowise/issues/1496)). It covers
`get_dead_code` (`kind`, `tier`, `min_confidence`), `get_context` (`include`)
and `search_codebase` (`kind`).

`get_dead_code`'s `min_confidence` additionally accepts the tier names the
response is organised by — `"high"` (0.8), `"medium"` (0.5), `"low"` (0.0) — as
well as a float. `get_health` reports the same thing under its own older name,
`unknown_only_keys`, for the `only` projection.

---

## `get_overview`

Architecture summary, module map, and entry points.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `repo` | string | No | *(workspace only)* Target repo alias, or `"all"` |
| `include` | list[string] | No | Opt-in blocks, any combination of `"content"`, `"outline"`, `"tour"`, `"decisions"`, `"graph"`, `"ownership"` (see below) |

**Returns (default):** `title`, `content_md` (the overview essay's summary section), `key_modules` (name, path, outline section), `entry_points`, `architecture` (layer names, file counts, layer order), `code_health`, `git_health`, `_meta`, and in workspace mode a `workspace` footer. The response's `more` field names the opt-in blocks.

**Opt-in blocks** — omitted unless named in `include`, and not computed at all when they are not:

| `include` key | Adds |
|---|---|
| `"content"` | the full overview essay in `content_md` |
| `"outline"` | `outline` — the stored wiki page tree, two rungs deep. `key_modules[].section` indexes into it |
| `"tour"` | `guided_tour` and `reading_order` — onboarding walks |
| `"decisions"` | `key_decisions`. `get_why` is the richer route |
| `"graph"` | `community_summary` — code-community clusters |
| `"ownership"` | `knowledge_map` — top owners and knowledge silos |

**When to use:** First call on any unfamiliar codebase. Gives the agent a mental map before diving into specifics. Skip on later calls in the same session; it doesn't change mid-session.

**Example calls:**

```
get_overview()
get_overview(include=["outline", "content"])
```

> **Output-schema change.** `guided_tour`, `reading_order`, `key_decisions`,
> `community_summary` and `knowledge_map` moved behind `include`; `outline` did
> too (`include=["outline"]` previously only deepened an always-present tree).
> `key_modules` no longer carries `description`, `page_id` or `parent_page_id`,
> and `architecture.layers[].description` is gone.

---

## `get_answer`

One-call RAG: retrieves over the wiki, gates synthesis on confidence, and returns a cited 2-5 sentence answer.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `question` | string | Yes | Natural language question about the codebase |
| `scope` | string | No | Repository-relative path prefix to restrict retrieval to |
| `include` | list[string] | No | `["evidence"]` returns the expanded projection: no confidence-keyed trimming, and a larger response budget |
| `repo` | string | No | *(workspace only)* Target repo alias |

**Returns:** A synthesized answer with file/symbol citations, a `confidence`
label (`high`, `medium`, `low`) rating the prose, and a `retrieval_quality`
label (`high`, `partial`, `weak`) rating the evidence under it. Every grade
returns an answer; what changes is how much evidence rides along with it.
A `high` answer can be cited directly and sheds `retrieval`, `best_guesses`,
`candidates` and `fallback_targets`, keeping one quote and, when the answer is
not already grounded, one symbol body. A `medium` answer keeps one body, two
quotes and the top two evidence rows. A `low` answer keeps two bodies and the
top three evidence rows, and `fallback_targets` appears only when nothing else
was served. Read the rows the reply names rather than calling `search_codebase`
again. `include=["evidence"]` skips this trimming entirely.

When synthesis cannot run at all, no provider resolvable or the call failed, the
response carries a top-level `degraded` naming the reason, is built from
retrieval and mined rationale with no LLM involved, and keeps the fullest
evidence shape whatever it graded. It also raises `_meta.state.degraded`.
`confidence` there is graded from the retrieval actually served, not from the
missing prose: on `no-llm-provider` it is `medium` unless `retrieval_quality` is
`weak`, in which case `low`. Any other reason, a configured provider whose call
failed, stays `low`, because a retry can still produce a real answer. `high` is
unreachable on this path, since the `answer` string is assembled boilerplate.

`_meta.complete` names the symbol bodies served whole from live source, with
bounds checked against the file; do not re-open those. `_meta.scope_hint` names
up to three knowledge-graph layers holding none of the served paths, so an agent
knows which areas the answer did not touch. When an answer grades `low` and the
index is behind live HEAD, `_meta.hint` says to run `repowise update` and ask
again before trusting it.

Two path-bearing blocks, with different jobs:

| Field | Job | Confidence-gated? |
|-------|-----|-------------------|
| `retrieval` | **Evidence.** Enriched hits (summary, snippet, key symbols) to re-read when the prose needs checking. Shrinks as confidence rises, because a trustworthy answer needs less of it. | Yes |
| `candidates` | **Navigation.** The ranked shortlist of files retrieval resolved, one `{path, lines?}` entry each, up to 20. | Shape-gated: the default projection drops it at every confidence |

`candidates` is built whenever retrieval resolved anything, including on high-confidence answers where `retrieval` is deliberately empty, but the default projection drops it; ask for it with `include=["evidence"]`. It is where to look next; it is not evidence that the answer is right.

**Retrieval legs:** three, fused by Reciprocal Rank Fusion: full-text and vector search over wiki pages, plus the structural symbol index. The symbol leg is keyed on the content words of the question rather than on whether it happens to carry an identifier-shaped token, so "how does an incremental update persist symbols" reaches the same rows as `_persist_symbols`. It exists because a generated file page renders only the *public* symbol table: a private helper or a local name is not in the text the other two legs index.

**When to use:** First call on any code question. Collapses search, read, and reason into one round-trip. On a low grade, start from the evidence rows the reply already carries; reach for `search_codebase` only when `retrieval_quality` is `weak`.

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
| `targets` | list[string] | Yes | File paths, module names, or symbol IDs. Batch multiple targets in one call. Symbol ids take the same `"path/to/file.py::Name"` form `get_symbol` accepts, with the same `::` / `.` / `/` separator normalisation, so an id from either tool works in the other. |
| `include` | list[string] | No | Additional data to include: `"full_doc"` (full wiki markdown), `"callers"` (who calls this, symbol targets), `"callees"` (what this calls, symbol targets), `"ownership"` (primary owner, bus factor, contributor count), `"last_change"` (last commit date + author), `"metrics"` (PageRank, betweenness, percentiles), `"community"` (cluster membership + neighbors), `"decisions"` (full decision records; default returns titles only), `"skeleton"` (file targets only; the file with bodies elided: every signature, imports, and the bodies of the most central symbols, token-budgeted; typically ~15% of the full file's tokens). An empty `callers`, `callees` or `used_by` list sits beside a `*_basis` object: the language, how many call edges the index resolved for it, the share of those that are guesses, and a note that unbound call sites are not counted, so an empty list means no resolved edge, not proof of none |
| `compact` | boolean | No | Default `true`. Set `false` for full structure block and importer list. |
| `repo` | string | No | *(workspace only)* Target repo alias, or `"all"` |

**Returns per target:** Documentation summary, symbols defined, ownership percentages, freshness score, co-change partners, architectural decisions governing the file. With `include` options: source code, call graph, graph metrics, community membership.

A file with no indexed symbols (README, config, plain data) gets a
`docs.file_preview` instead of an empty symbol list: line and character counts,
plus the heading spine for markdown or the first non-blank lines otherwise.
Counts and verbatim excerpts only, nothing inferred.

When the symbol half of a `path::Name` target does not resolve but the file
half does, the reply is that file's card with `resolved_to` naming the file and
a `note` saying which symbol was not found. The file's symbol list is where the
correct id is, so this is a partial answer rather than a dead end.

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
| `symbol_id` | string | Yes | One of three forms: `"path/to/file.py::SymbolName"` (canonical, from `get_context`'s symbol list; normalises `::` / `.` / `/` separators across languages), `"path/to/file.py:140-180"` (a live range read, 200 lines max), or an omission ref `"repowise#<12-hex>"` / a pasted whole `[repowise#...]` marker. |
| `query` | string | No | Omission refs only: return just the stored lines matching this regex (or substring). Ignored for symbol ids and range reads. |
| `context_lines` | int | No | Extra source lines before/after the symbol (0-50, default 0) |
| `depth` | int | No | Follow the call graph outward from this symbol and include what it calls, with bodies (1-3, default 1 = this symbol only). Out-of-range values clamp. |
| `repo` | string | No | *(workspace only)* Usually omitted; `"all"` is not supported |
| `reference` | object | No | A structured `continuation_reference` or `fetch_reference` emitted by this tool; pass it unchanged to retain both id and repository scope. |

**Returns:** For a symbol id or range: the source (bounded at ~600 lines,
each line prefixed with its file line number in the same format as a `Read`
result), its exact start/end line numbers, kind, and a `truncated` flag; on a
miss, an `error` with the closest matches (`fallback_lines` from a live grep).
When several indexed symbols match the id (overloads, re-exports, conditional
definitions) the response has `ambiguous: true` and a `candidates` list with
every matching body — none is silently chosen; candidates past the response
budget appear in `not_rendered` with a `fetch_with` range read. For an
omission ref: the stored content plus provenance (`source`, `created_at`,
`original_tokens`).

With `depth` above 1 the response also carries `callee_bodies`: the symbols
this one calls, transitively, each with its `depth` (hops from the root), its
source, and a `verified` flag. Every symbol appears once, at the shallowest
depth it was reached from. Callees past the response budget are listed in
`not_rendered` with the `fetch_with` range that retrieves them, so a bounded
walk never looks like a complete one.

**When to use:** When you need the body of one function or class: pipe the
`symbol_id` straight from `get_context`'s symbol list. Use the line-range form
for anything that falls between symbols. Or when a response's `_meta.omitted`
lists refs you want back and you have no shell for `repowise expand` (e.g.
Claude Desktop).

Reach for `depth=2` when you are following a call chain: reading a body,
finding the next name in it, then fetching that one. The graph already holds
those edges before the first call, so one `depth=2` call replaces the whole
sequence of round trips.

**Example calls:**

```
get_symbol(symbol_id="src/auth/service.py::AuthService")
get_symbol(symbol_id="src/auth/service.py::login", context_lines=10)
get_symbol(symbol_id="src/auth/service.py::login", depth=2)
get_symbol(symbol_id="src/auth/service.py:140-180")
get_symbol(symbol_id="repowise#a1b2c3d4e5f6")
get_symbol(symbol_id="repowise#a1b2c3d4e5f6", query="FAILED")
```

---

## `search_codebase`

Hybrid code search over repowise's indexes. A single tool that, depending on
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
| `page_type` | string | No | Restrict to one page type. The two you will reach for are `file_page` (the always-on per-file docs) and `module_page` (the subsystem/concept pages). Other stored types (`repo_overview`, `layer_page`, `scc_page`, `api_contract`, `infra_page`, `symbol_spotlight`) also filter. |
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
- *Concept hits*: ranked wiki pages with `relevance_score`, `snippet`, `target_path`, and a `search_method` (`embedding` vs `bm25` fallback). A `symbol_spotlight` page's `target_path` is a page identifier of the form `file.py::Symbol`; those hits also carry `file` with the openable path. **Read `file` when present.** `target_path` is for piping into `get_symbol`, not for opening.

Alongside `results`, the response carries **`candidates`**: up to `limit`
distinct files worth opening next, one `{path}` entry each, best first.

Every entry is a real file path, and that is the difference between the two
blocks. `results` ranks *pages*, and a page is not always a file: a
`module_page` is named by a structural group key that reads exactly like a
directory, an `scc_page` by `scc-<hash>`, an `onboarding` page by a slot name.
Ranking those is correct; opening them is not. `candidates` resolves symbol
pages to their file, collapses several symbols of one file to a single entry,
skips every page that names no file, and backfills from below the result
window so a slot spent on a module page does not also cost you a file.

**If your next move is a Read, read `candidates`.** If you are enumerating
matches or resolving a `symbol_id`, read `results`.

Tombstoned and `exclude_patterns`-excluded results are filtered. In workspace
mode, structural and concept searches both federate across repos and merge
(this is the one tool where `repo="all"` is fully supported).

**When to use:** Locating a function/class/method by name, resolving a
path-shaped query, or discovering pages by topic: the symbol/file shapes pipe
directly into `get_symbol` / `get_context`.

**Example calls:**

```
search_codebase(query="GitIndexer index_repo")          # -> symbol hits
search_codebase(query="core/ingestion/indexer.py")      # -> file hits
search_codebase(query="rate limit OR throttle OR retry") # -> wiki pages
search_codebase(query="login", mode="symbol", symbol_kind="method")
```

---

## `get_risk`

Modification risk assessment for files or a set of changed files.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `targets` | list[string] | No | File paths to assess |
| `changed_files` | list[string] | No | Files in a PR/changeset for blast radius analysis; passing this switches the response into PR-directive mode |
| `include` | list[string] | No | Opt-in blocks: `graph` (typed `dependents`, `consumers`, `cross_repo_links`, structural `impact_surface`, `direct_risks`), `churn` (`change_magnitude`, `risk_type`, `change_pattern`) |
| `repo` | string | No | *(workspace only)* Target repo alias |

**Returns:** Per-file `hotspot_score` (0-1 churn percentile), `health_score` (0-10), hotspot status, direct directed `dependents_count`, historical `co_change_partners` (each with a recency-decayed `weight`, not an integer count), blast radius, recommended reviewers, test gap analysis, and security signals. With `include=["graph"]`, `dependents` preserves direct versus transitive structural reach, `consumers` contains typed contract consumers only, and `cross_repo_links` retains both repository identities, direction, relationship type, evidence kind, and file- or repository-level granularity. Package-manifest links are repository-level and never invent a target file. Every typed relationship collection carries matching total/emitted/truncated fields. `relationship_analysis` distinguishes available-empty analysis from unavailable, degraded, partial, and source-truncated artifacts and retains artifact generation/provenance fields. Structural reach is not proof of runtime breakage.

> **Opt-in blocks.** `impact_surface` and `direct_risks` are pagerank floats an agent cannot rank; `change_magnitude`, `risk_type` and `change_pattern` restate numbers printed beside them. All five are computed regardless and feed `risk_summary`; `include` only decides whether they ship. `global_hotspots` accompanies a multi-target call only, being ambient orientation that a single named file does not need; it ranks by fix history the same way `defect_profile` does.

> **Scales.** Every response carries the facts that stop a misreading: unit,
> range, calibration status, and whether the value is authoritative. The
> per-field dictionary behind them never varies between calls, so it ships only
> with `include=["scales"]` - ask for it once per session, not per call. That
> dictionary describes indexed file values: `hotspot_score`, `owner_pct`, and
> `recent_owner_pct` are 0-1 ratios, while `risk_type` is an
> uncalibrated category. In PR mode, `structural_impact_score` is a deterministic,
> uncalibrated 0-10 structural-exposure heuristic; `localized` is below 4,
> `moderate` is 4 to below 7, and `broad` is 7 or above. It is not a runtime-
> breakage probability and is not authoritative for live change review.
> Deprecated `overall_risk_score` remains an exact alias, with migration metadata.
> Direct rows expose raw, unbounded `structural_score` values in
> pagerank-weighted-hotspot units. These are not comparable to
> `get_change_risk.score`. Coverage and gap fields are percentages from 0-100.
> Every emitted float is rounded to 4 significant digits.

When `changed_files` is passed, the exact serialized response starts with a `directive` block. Its core lists are the local blast radius: `may_break` (production files in structural reverse-import reach of the diff, candidates for review rather than proven breakage), `may_break_tests` (test files reached the same way, kept separate so a burst of tests doesn't crowd production impact out of the capped list), `missing_cochanges` (historical co-changers absent from the diff), compatibility `missing_tests`, and additive typed `test_recommendations`. Every recommendation retains `basis`, all retained `bases`, repository identity, source files, and evidence: `measured` means the per-test coverage map found the test; `inferred` means structural reachability found a candidate and is not coverage proof. `tests_to_run` preserves the older measured-first/fallback id projection and `tests_to_run_basis` remains `measured`, `inferred`, or `none`; `files_without_measured_tests` carries the narrower typed coverage claim. Matching total/emitted/truncated/omitted fields describe each exact pre-cap population. `coverage_analysis`, `test_inference_analysis`, and `test_analysis` distinguish available-empty evidence from unavailable, stale, partial, or degraded analysis; unavailable coverage with `tests_to_run: []` never means that no tests are needed. The full typed population is shared with the REST blast-radius response, while any budget-omitted directive rows are recoverable through the response omission marker. In workspace mode the directive also carries the cross-repo fallout of the changed repo:

- `will_break_consumers`: deprecated compatibility name for services in *other* repos that structurally depend on this one. Rows carry `claim: structural_reach`, `runtime_breakage_claim: false`, both repository roles, direction, distance, and aggregated edge kinds; the sibling `will_break_consumers_semantics` is `structural_reach_only`. Matching total/emitted/truncated fields describe the exact pre-cap structural population, while `cross_repo_relationship_analysis` labels unavailable or partial edge provenance.
- `missing_cross_repo_cochanges`: services in other repos that historically co-change with this one but aren't in the diff.
- `breaking_changes`: provider contracts in this repo that changed *incompatibly* since the last index (a removed route or field, a type or field-number change, a newly-required field), each with the changed `contract_id`, the change `kind`/`severity`, and the `impacted_consumers` (repo, service, file) it endangers across repos. Schema-level truth, distinct from the topology-level `will_break_consumers`; non-breaking changes (added optional field, new endpoint) never appear. See [Breaking-Change Guard](../scale/WORKSPACES.md#breaking-change-guard).
- `conformance_violations`: declared dependency-rule breaches the diff's repo participates in, each with the offending `source`/`target` services, the `rule` (e.g. `frontend !-> db`), and `edge_kind`. See [Architecture Conformance](../scale/WORKSPACES.md#architecture-conformance).
- `dependency_cycles`: circular service dependencies involving this repo, each with the participating `nodes` and `length`.

> **Output-schema change.** `directive.will_break` is now `directive.may_break`,
> and `directive.will_break_tests` is now `directive.may_break_tests`. Both are
> a reverse-import reachability walk: `get_risk` is given a file list, never a
> diff, so it cannot know whether the symbol an importer actually uses changed.
> The old name promised a precision the analyzer does not have.
> `will_break_consumers` temporarily keeps its old key for compatibility, but
> it is structural reach only and is explicitly marked deprecated. Only
> `breaking_changes` comes from incompatible contract diffing.

**When to use:** Before modifying files, especially hotspots. Understand what could break, who to involve in review, and whether tests cover the affected area.

**Example calls:**

```
get_risk(targets=["src/auth/middleware.ts"])
get_risk(changed_files=["src/api/routes.ts", "src/middleware/cors.ts"])
get_risk(targets=["src/auth/middleware.ts"], include=["graph", "churn"])
```

---

## `get_change_risk`

Review one commit, a `base..head` range, or uncommitted work. Unlike
`get_risk`, which evaluates indexed files and can report blast radius, this
compares the two revisions directly and needs no index refresh.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `revspec` | string | No | Commit or `base..head` range to score. Omit it to score uncommitted work, or pass `HEAD` when the tree is clean |
| `repo` | string | No | *(workspace only)* Target repo alias |
| `extensions` | list[string] | No | File suffixes to count, such as `[".py", ".ts"]` |
| `exclude_patterns` | list[string] | No | Gitignore-style paths to omit; combined with root `.riskignore` rules |
| `baseline` | int | No | Recent commits to sample for percentile ranking (default `200`; `0` disables every percentile, `risk_percentile` and `fix_history.percentile` alike) |
| `include` | list[string] | No | `"findings"` for every change finding, `"diagnostics"` for the raw score mechanics, `"scales"` for units and calibration |
| `finding_id` | string | No | Expand one `health_delta` finding by its id |

**Returns:** `directive` leads with a `status`
(`review_required`, `review_recommended`, `clear_in_analyzed_scope`, `unknown`),
a headline, bounded reasons, and concrete next actions.

`health_delta` is what the change newly made worse, across defect,
maintainability and performance. Both revisions are analysed from their own
content, so a finding present at head is reported only when the diff explains
it. `scope` counts changed, eligible, analysed, skipped and failed files, and
`status` distinguishes `available` from `partial` and `unavailable` — a
`partial` comparison is never a clean bill, and `skipped` says why each file
was left out. `introduced`, `worsened` and `resolved` are totals;
`top_findings` carries the three most actionable, with `findings_total` and a
recovery call for the rest.

Each finding names its `dimension`, `biomarker`, `severity`, `path`, `symbol`
and head-side `lines`, a `reason`, and an `attribution` — `basis`
(`added_lines`, `changed_symbol`, `changed_call_edge`, `new_file`,
`file_change`, `context_change`, `unknown`) with a `confidence`. Identity
ignores line numbers, so moving code introduces nothing and a rename carries
its findings across. Performance findings carry `opportunity_id` and
`opportunity_rank` and are ordered by opportunity rank and actionability, never
by defect impact, which is zero for them by construction. `inspect` gives the
exact `finding_id` call that expands one; ids are bound to the two revisions
that produced them. A finding that exactly matches a stored one also carries a
`health_reference` for `get_health(finding_id=...)`.

`fix_history` reports the recency-weighted bug-fix record of the files the
change touches, with `files` naming where the pressure sits and `percentile`
ranking it against the same measure over the repo's own recent commits. It is
the part that separates a small edit to a fragile file from a large edit to a
safe one. `available` is false when the history walk could not run.

`change_shape` carries the supporting diff-shape reading: `score`,
`risk_percentile`, `review_priority`, `classification`, `fallback_band` and
`is_fix`, which also stay at the top level. `score` is an offline-calibrated
0-10 output measuring diff size and spread — not a probability, and not where
the change lands. `fallback_band` appears only when no baseline was available.
`working_tree` says whether uncommitted work was the subject.

`include=["diagnostics"]` adds the raw mechanics: `risk_authority`,
`score_measures`, `score_unit`, `baseline_sample_size`, `features` and
`drivers`. `include=["scales"]` adds each field's kind, unit, range,
calibration and thresholds. Both are identical on every call, so ask once.

It also returns `impacted_tests`, whose `tests_to_run` names the tests the
per-test coverage map proves execute the change's changed *lines* (line-precise,
so a narrower set than `get_risk`'s file-level `tests_to_run` — same field name,
because it is the same concern). It is capped at ten, with `total` and
`truncated` reporting the overflow and the tail written to the omission store:
on `truncated: true` the response carries an `omission_marker`, and
`repowise expand <ref>` returns the rest. Its `line_coverage` buckets flag
`untested_changes` (covered file, uncovered change), `stale_test_candidates`
(covered lines whose guarding test file is absent from the diff), `covered`, and
`no_coverage_data` (files absent from the map). When no map is ingested the
change is never reported as untested: `status` becomes `inferred` when the
import graph can name test files reaching the change (candidates, file-level, no
line attribution, and `line_coverage` stays empty because reaching cannot speak
to lines), and `no_map` ("run the full suite") when it cannot. `basis` carries
the same distinction in one word: `measured`, `inferred`, or absent. Build the
measured map with `coverage run --contexts=test` followed by
`repowise coverage add`.

In workspace mode the response also carries `cross_repo`, and every
`cross_repo.consumers[]` row gains a `tests` block: a `state` (`measured`,
`inferred`, `none` or `unresolved`), up to five `tests_to_run` rows carrying
`test_file`, `test_id`, `basis`, `via` and `confidence`, `total` and
`truncated` for the overflow (the tail goes to the omission store), and
`unresolved_reason` / `unresolved_detail` when the join could not be followed.
Only `tests_to_run` is capped at five; `total` is the true number of tests
found for that consumer, so `truncated: true` means `total` minus five went to
the omission store. `unresolved_reason: "lookup_failed"` is the state every row
lands in when the join itself failed, as opposed to one link that could not be
followed, and `unresolved_detail` names what failed.

> **Output-schema change.** `impacted_tests.tests` is now
> `impacted_tests.tests_to_run`, matching `get_risk`'s directive.

`is_fix` is the defect benchmark's keyword rule read over the commit subject,
not the conventional-commit type, so a `feat:` commit whose subject says it
fixes something reads true; the rule is frozen for comparability rather than
tuned. `prior_fixes` below is the tuned view: it applies a diff-shape filter on
top of that rule, counting only commits that actually edited production code.
`fix_history` above runs the same unfiltered rule, and `prior_fixes` is the one
block of the three that needs an index.

When the changed files carry counted bug fixes, the response also holds
`prior_fixes`: per file, how many past bug-fix commits touched it
(`fix_count`), how many of the change's lines fall inside the ranges one of
those fixes replaced (`overlapping_lines`), and how long ago the most recent
was (`last_fix_days_ago`). `total_fixes` counts distinct commits, not rows,
and `files` is capped at ten with `truncated` reporting overflow.

Each file also carries how much of *this* change sits in it — `changed_lines`
and `share_of_change` — with `changed_lines_in_fixed_files` as the total across
them. That join is what lets the response say where the risk sits rather than
only that some touched file has a past: the score is whole-change, so when one
returned file holds at least half the changed lines, `concentration` names it.

`overlapping_lines` is labelled `approximate` in the payload, and that label is
load-bearing: a past fix's ranges are numbered against its own parent commit,
so anything that moved lines in between shifts them. Read it as "this
neighbourhood has been patched before", not "this exact line". The per-file
`fix_count` beside it carries no such caveat. The whole block is aggregate and
never names the commit that introduced a bug: file-level SZZ measured 74.5%
precision on this repo's frozen judgments, which is enough to count fixes and
not enough to accuse one commit of causing them. The block is absent entirely
on an index with no fix history.

In workspace mode the response also holds `cross_repo`, built from the artifacts
of the last `repowise update --workspace` rather than from a graph traversal. It
appears when the commit touches a file that provides a contract some other repo
consumes, or when the breaking-change report attributes a break to one of the
changed files. `consumers[]` names each link with its `provider_file`, the
consumer's `repo`, `file` and `contract_id`, the `contract_type` and the
`match_type` that joined them, plus `provider_symbol_id` and `symbol_id` when the
link is symbol-level; `consumer_repos` lists the other repos in one place.
`breaking_changes[]` carries the `contract_id`, `type`, `kind`, `severity`,
`detail`, `provider_file` and `impacted_repos` of each contract that changed
incompatibly. `breaking_changes_available` says whether a detection pass ran at
all, so an empty list reads as silence rather than as an all-clear, and
`breaking_changes_as_of` stamps that half only. `consumers` is capped at ten and
`breaking_changes` at five, with `consumers_truncated` and
`breaking_changes_truncated` counting what the caps left out; the block answers
whether this commit crosses a repo boundary, and `get_blast_radius` is the tool
for the full traversal. It is absent outside workspace mode, without contract
artifacts, and when the commit touches no published file.

`branch_overlap` names the other open branches editing the files this change
edits. It is git-only, so it appears whether or not the repo is indexed; an index
only orders the shared files and adds the history rows. `base` and `current` name
the two ends of the comparison, and each `branches[]` entry carries the branch
name, `ahead` and `behind` commit counts, `last_commit` (the date), and `files[]`.
Every file row states its `basis` in words, either `same file` or
`co-change pair, N of M commits`, the second carrying the `partner` file of this
change it pairs with and appearing only under a branch that already shares a file
directly, at most three per branch. `scanned`, `total` and `truncated` report the
branch scan itself, which is bounded to the newest 50 branches by committer date.
There is no score and no percentage in the block. `branches` is capped at five and
each entry's `files` at ten, both through the shared response budget: a capped
list gains `<key>_total`, `<key>_emitted`, `<key>_truncated`, `<key>_omitted` and
`<key>_reduced_reason` beside it, and the omitted rows go to the omission store,
so on `branches_truncated` or `files_truncated` the response carries an
`omission_marker` and `repowise expand <ref>` returns the rest. `truncated` is a
different fact: it reports the branch scan bound, not a cap. The block is absent when the change has
no counted files, when no other branch edits a shared file, and when the scan
exceeds its 20-second ceiling or git cannot answer.

`change_shape.independent_changes` says when the diff is several changes rather
than one. It groups the changed files by connectivity, over index edges (imports,
calls, type references, framework and dynamic edges), stored co-change pairs, and,
when `revspec` is a `base..head` range, the files each commit of that range
touched, which links them to each other. A single commit and uncommitted work
carry no commit evidence, so only a range reads it. Only a changed file that is in
the index, is not a test, and is written in a language whose resolver can emit an
import edge is eligible to be grouped: docs, config and data files are never in a
group, not even through a co-change pair, and tests never join or connect one.
`count` is the number of groups; each `groups[]` entry lists its `files` and its
`bridging_files`, the files that alone hold the group together, where moving one
out would split it (named only for groups of three or more files, and most often
the file two commits of the range share). `ungrouped_files` carries every changed
file left out of the grouping, and `summary` names the reasons in those terms:
docs, config, tests, files not in the index, or files it has never linked. `basis` states in words
what was actually checked, and its sentence changes with the subject: it names a
shared commit alongside the import, call, type reference and co-change pair when
the commits of a range were read, and omits it when there were none to read. Under
either wording it is a claim about this index and not about the code. There is no
separate key saying which sentence you got; the sentence itself says it.
`ungrouped_files` is capped at ten through the shared response budget, so a capped
list gains `ungrouped_files_total`, `_emitted`, `_truncated`, `_omitted` and
`_reduced_reason` beside it and the omitted names go to the omission store,
recoverable with `repowise expand <ref>`; nothing else in the block is capped. The
block needs an index and is absent without one, and it is absent whenever the diff
is one change: fewer than two changed files, fewer than two of them eligible to be
grouped, or fewer than two groups surviving. Under a response over budget it is
the first thing shed, ahead of the rest of `change_shape`; `branch_overlap` sheds
after `prior_fixes` and before `cross_repo`.

The freshness envelope is scoped to the files this change edits, whether or not
the repo is indexed: `branch_overlap` reads files on other branches, and that
never widens what the response is about.

**When to use:** Before merging a commit or PR range, especially when you need
to assess the change itself rather than the risk of an already-indexed file.

**Example calls:**

```
get_change_risk()
get_change_risk(revspec="main..HEAD", extensions=[".py"], exclude_patterns=["tests/"])
get_change_risk(revspec="main..HEAD", include=["findings"])
get_change_risk(revspec="main..HEAD", finding_id="chf_27a13be11e7ee33f")
```

---

## `get_why`

Architectural decision intelligence. Falls back to git archaeology when no decision records exist for a path, and further to a rationale comment mined live from the source when neither decisions nor git history explain the "why".

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | No | Natural language question about decisions, OR a file/module path |
| `targets` | list[string] | No | File paths to anchor an NL `query` search to |
| `repo` | string | No | *(workspace only)* Target repo alias, or `"all"` (only when `query` is given) |
| `id` | string | No | A decision id or `ev_...` evidence id previously emitted by `get_why`; resolves it directly without relevance search |
| `reference` | object | No | An emitted evidence reference object; pass it unchanged to retain both id and repository scope. |

**Modes:**

1. **NL search**: pass a question, optionally anchored to `targets`: `get_why(query="why JWT over sessions?")` -> searches decision records.
2. **Path-based**: pass a file path as `query`: `get_why(query="src/auth/service.ts")` -> returns three lanes, `decisions` (accepted, governing), `candidates` (nobody accepted them) and `history` (accepted and since replaced), plus the file's origin story.
3. **Health dashboard**: no `query`: `get_why()` -> stale decisions, conflicts, ungoverned hotspots.
4. **Reference lookup**: pass `id`: `get_why(id="ev_...")` -> the exact evidence and supporting decision in one call.

**Returns:** Matching decision records with title, rationale, alternatives considered, affected files, staleness score. Health mode returns stale decisions, conflicts, and ungoverned hotspots.

`answer_basis` names the strongest lane the response rests on: `decision`, `episode`, `rationale`, `archaeology`, or `documentation`. Only `decision` is a ruling; the rest are evidence to weigh. Absent when no lane was served, and on the health dashboard.

**The lane a record is in decides whether it binds you, and path mode puts it in one.** `decisions` holds accepted records: somebody accepted each in a recorded event naming the reason, the scope, the evidence and the accepter, so treat them as constraints. `candidates` holds records something inferred and nobody has agreed to; read them as hints and never as rules, and note the `candidates_note` beside them says so too. Nothing produces an acceptance except an explicit `repowise decision confirm` or a committed ADR that says it is accepted, so a candidate that has recurred across fifty sessions is still a candidate.

Do not read the lane off `status`. That column is a projection kept in step for readers that predate the split, and a record can carry `status: "active"` with no acceptance behind it at all. An accepted record instead carries a `currency`: `active` (still describes its code), `needs_review` (its files have moved, and it still binds), `uncheckable` (it names nothing, so nothing can check it), `superseded` or `dismissed`. A candidate carries `review_state: "open"` and no `currency`.

Path mode's `alignment` counts the lanes separately and they sum to `governing_count`, which is every record naming the file: `active_count` is what governs it, `deprecated_count` what was accepted and withdrawn, `uncheckable_count` what was accepted but names nothing, `candidate_count` what is merely awaiting review. `score` is derived from `active_count` alone, so a file with `active_count: 0` is ungoverned however many candidates name it.

The `candidates` and `history` lanes are capped at three rows each and shed first under response-budget pressure, so an absent lane means the budget was tight, not that it was empty. `get_overview`, `get_risk` directives and `get_answer` serve accepted records only; a candidate reaches none of them as an instruction.

**When to use:** Before architectural changes, understand existing intent and constraints. After changes, record new decisions.

**Example calls:**

```
get_why(query="rate limiting")
get_why(query="src/payments/processor.ts")
get_why(query="why is caching split from the eviction path?", targets=["src/cache"])
get_why()
```

---

## `get_dead_code`

Unreachable code, unused exports, unused internals, and zombie packages, sorted by confidence tier with cleanup impact estimates. Flag-based, not include-list-based.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `repo` | string | No | *(workspace only)* Target repo alias |
| `kind` | string | No | Restrict to one finding kind: `unreachable_file` \| `unused_export` \| `unused_internal` \| `zombie_package` |
| `min_confidence` | float | No | Minimum confidence floor (default `0.4`; `0.7`+ is cleanup-ready only) |
| `safe_only` | boolean | No | Deletion-ready findings only, excluding anything with runtime-load risk (default `false`) |
| `limit` | int | No | Max findings per tier, clamped to 25 (default 20) |
| `tier` | string | No | Restrict to one tier: `high` (>= 0.8) \| `medium` \| `low` |
| `directory` | string | No | Path-prefix filter |
| `owner` | string | No | Primary-owner filter |
| `group_by` | string | No | Roll findings up by `directory` or `owner` instead of listing them flat |
| `include_internals` | boolean | No | Include private/underscore symbols (default `false`) |
| `include_zombie_packages` | boolean | No | Include zombie-package findings (default `true`) |
| `no_unreachable` | boolean | No | Exclude `unreachable_file` findings (default `false`) |
| `no_unused_exports` | boolean | No | Exclude `unused_export` findings (default `false`) |
| `finding_id` | string | No | Resolve an emitted stable finding `id` directly in one call |

**Returns:** Dead code findings grouped by confidence tier (high >= 0.8, medium, low). Each finding includes: file path, kind, confidence score, line count, and cleanup impact estimate. In workspace mode, confidence is lowered on findings other repos still import. `summary.call_resolution_basis` lists, per language, how many call edges the index resolved and what share are guesses, which is the graph the findings rest on.

**When to use:** Cleanup tasks, not a targeted fix. Conservative by design: `safe_only` excludes dynamically-loaded patterns and framework-decorated functions.

**Example calls:**

```
get_dead_code()
get_dead_code(min_confidence=0.8, tier="high", safe_only=true)
get_dead_code(kind="unused_export", group_by="owner")
```

---

## `get_health`

Code-health marker scores: the same deterministic markers the
`repowise health` CLI computes, across three signals (defect risk,
maintainability, performance), exposed for agentic workflows. Zero LLM calls.
Use it to inspect stored health analysis before a change and after committing
health-relevant changes and running `repowise update`: neither re-calling
`get_health` nor updating an uncommitted working tree recomputes those metrics.

**Safe recipes:**

```text
get_health(only=["directive"])
get_health(targets=["path"], include=["refactoring"])
get_health(targets=["module:path"], only=["modules","metrics"])
get_health(include=["trend"], only=["trend"])
get_health(include=["accuracy"], only=["accuracy"])
get_health(include=["coverage"], only=["coverage"])
get_health(include=["performance","refactoring"], only=["performance_opportunities","refactoring_plans"])
get_health(include=["refactoring"], only=["refactoring_opportunities"], limit=6)
get_health(opportunity_id="refop2_...")
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `targets` | list[string] | No | File paths, or `module:foo` to expand a module's file set. Empty means dashboard mode. |
| `include` | list[string] | No | Opt-in blocks (default response stays lean): `"biomarkers"` (findings in dashboard mode), `"refactoring"` (structured, graph-aware refactoring plans; see below), `"trend"` (snapshot diff + declining / predicted-decline alerts), `"coverage"`, `"accuracy"` (the "does the score find the bugs?" stat, dashboard mode), `"signals"` (per-file process / people / topology signals, targeted mode), `"churn_complexity"` (churn x complexity quadrant points, dashboard mode), and a dimension name (`"performance"` / `"defect"` / `"maintainability"`) to filter findings to that pillar. |
| `only` | list[string] | No | Keep just these top-level keys. `include` adds blocks, `only` subtracts them. `mode`, `_meta`, `unresolved`, `known_modules` and each kept list's `*_total` sibling always survive. The three `include` **block** names work as aliases: `biomarkers`→`findings`, `accuracy`→`defect_accuracy`, `refactoring`→`refactoring_plans`. Note that `refactoring_plans` is the raw
per-detector list and is now **opt-in**: `include=["refactoring"]` leads with
`refactoring_opportunities`, the composed unit, and emitting both would ship two
representations of the same work in one response. The `include` **dimension** names (`performance`, `defect`, `maintainability`) do not — they filter rows inside several blocks and have no single key to resolve to, so they land in `unknown_only_keys`. Nor does `signals`, which merges into `metrics[].signals` — in targeted mode, where `signals` applies, name `metrics` instead. |
| `repo` | string | No | *(workspace only)* Target repo alias |
| `limit` | int | No | Max rows in **every** ranked list (default 20, capped at 50). `0` means no rows; the `*_total` siblings still report the true counts. |
| `finding_id` | string | No | Resolve an emitted stable health-finding `id` directly in one call. |
| `plan_id` | string | No | Resolve an emitted stable refactoring-plan `id` directly in one call. |
| `opportunity_id` | string | No | Resolve one opportunity `id` directly. The prefix picks the pillar: `perf...` is a performance cause, `refop...` a composed refactoring. Mutually exclusive with the two above; passing more than one returns `mode: "conflict"` naming them rather than answering about whichever was checked first. |
| `refactoring_type` / `refactoring_confidence` / `refactoring_effort` | string | No | Queue filters over the same read model and vocabulary the REST route uses. An unrecognized value is reported back in `ignored_arguments` rather than silently narrowing to nothing. |
| `refactoring_view` | string | No | Named ordering for `refactoring_opportunities`. `diversified` (default) round-robins the rank order over cause, refactoring type and area, because the ranked head is a genuine run of ties; `canonical` is the published rank order verbatim, ties and all; `file_spread` asked for one row per file, which a composed opportunity satisfies by construction, so it resolves onto the diversified order. Both older values keep working. It also selects the legacy `refactoring_plans` list's view, where `diversified` resolves to that list's historical `canonical` default. |
| `cursor` | int | No | Zero-based offset into a ranked collection; the `recovery` block names the exact next call. |
| `performance_view` | string | No | `detail` (default) or `summary`. `summary` keeps identity, counts and plan state and drops the explanatory fields. |
| `performance_context` | string | No | `production` (default) / `tooling` / `test` / `unknown` / `all`. The summary block is scoped to the same context as the queue; `repository_total` stays the count over every context. |
| `performance_boundary` | string | No | `db` / `network` / `filesystem` / `subprocess` / `lock` / `none`. |
| `performance_confidence` | string | No | Evidence confidence: `high` / `medium` / `low`. Fix safety and actionability are separate facets. |
| `performance_sort` | string | No | `rank` (default) / `leverage` / `observations`. |

**Returns:** Dashboard mode (no `targets`) returns a `directive`, repo-level KPIs
(hotspot health, average health, worst performer, maintainability / performance
pillar averages), the lowest-scoring files, and a per-module NLOC-weighted
rollup. Targeted mode returns per-file marker findings with severity,
per-dimension scores, and the score breakdown. Each finding carries a `dimension`
(`defect` / `maintainability` / `performance`).

**Lead with `directive`.** Dashboard mode opens with the single file to fix
first, its dominant finding, `recovers_weighted_deficit_points` /
`share_of_repo_gap_pct` (what
fixing it buys the headline; the share is bounded by 100% and sums to 100%
across `high_leverage_files` — the gross deficit of below-target files is the
denominator, not the net gap, so a single file cannot read as closing more than
the whole remaining gap), and `then`, the next two by leverage. Every other
block ranks and describes; this one recommends. Same role as `get_risk`'s
`directive`. Rank by `weighted_deficit`, not `score` — the score floors at 1.0.

`recovers_points` remains an exact deprecated alias during the compatibility
window; `recovers_points_compatibility` names its replacement.

**Nothing is dropped silently.** Any `targets` entry that matched nothing is
named in `unresolved` with a reason (`not_indexed` → run `repowise update`,
`no_such_path`, `excluded`, `no_such_module`; a missed module name also returns
`known_modules`). Missing stored analysis is explicitly unavailable rather than
fabricated as a healthy score. A
target set that resolves to nothing still answers in targeted mode rather than
falling back to the repo dashboard. Every capped list carries a `*_total`
sibling — including under `only`, which retains it automatically. `unresolved`
and `known_modules` survive any `only` projection too, for the same reason
`mode` does: a caller who has to ask for the error report in order to see it
does not have an error report.

`_meta.health_analysis` explicitly labels the result as stored analysis,
states that the call did not recompute it, distinguishes index/live-Git facts
from source-byte verification, and gives the exact commit-then-update refresh
precondition. Its `status` is `available`, `provenance_unknown` (metrics exist
but no row recorded the commit they were computed against, with `reason:
"analysis_commit_not_recorded"`), or `unavailable` (no stored analysis at all).
`provenance_unknown` is a gap in attribution, not a failed analysis. `_meta.health_analyzed_at` dates the health pass, which is separate from
indexing and can lag it, and `_meta.health_analyzed_commit` says which commit
those scores were computed against. The incremental update path rescores only
the files that changed, so the metrics table can hold rows from several passes
at once; when it does, `_meta.health_analyzed_commits_distinct` says how many
and the reported commit is the newest pass's. Both fields are omitted rather
than guessed when no row records a commit.

**The response is bounded.** `include` only *adds* blocks, and the dashboard's
five ranked lists compose: `include=['refactoring']` on a mid-size repo lands
near the host's tool-result cap, past which the host rejects the whole result
and you get nothing. Pair `include` with `only` —
`get_health(include=['refactoring'], only=['refactoring_plans'])` is the call
`directive.plan_via` names. Anything that would still overflow is shed in the order this tool declares,
never silently: the response carries `truncated: true`, the `*_total` /
`*_emitted` / `*_reduced_reason` siblings describe what was there, and
`_meta.omitted` names refs that restore the dropped rows. Re-requesting one
block with `only` also recovers it.

**Test material is bucketed, not hidden.** Every metric row carries `is_test`
(distinct from `has_test_file`: "is this file a test" vs "is this file tested").
In dashboard mode the ranked finding lists are split — `top_findings` /
`findings` carry production findings, `test_findings` carries the test half, and
`top_findings_total + test_findings_total` is the whole open set. Defect risk in
a test asks a different question from defect risk in the code it covers, and at
the default limit a quarter of the headline list was describing the test suite.
Targeted mode is never split: you named the files, so you get their findings.
KPIs, `worst_files` and `high_leverage_files` deliberately still include test
files — excluding them would move the repo's headline score, which is a scoring
change, not a display one.

**Leverage, not just lowness.** `average_health` is NLOC-weighted (the number the
badge and dashboard surface), so a few large low-scoring files hold it down. To
make that actionable rather than a mystery:

- `kpis.average_health_unweighted` is the plain file mean and
  `kpis.average_health_weighting` is `"nloc"`. When the weighted and unweighted
  numbers diverge, the gap is telling you to chase *big* files, not the long tail.
- `gap_analysis` (dashboard mode) reports the net weighted points the average must
  recover to reach the Healthy floor (8.0), how many files sit below it, and how
  few of them carry the whole gap (`files_to_reach_target`) or half of it
  (`files_for_half_gap`). This reframes a repo-wide number as a short worklist.
- Every metric row carries `weighted_deficit = (8 - score) x nloc`: how much the
  repo headline recovers if that file reaches 8.0. `high_leverage_files`
  (dashboard mode) is the top-N ranked by it, distinct from `worst_files`, which
  sorts by raw score and ranks a 30-line file at 1.0 equal to a 1,200-line file at
  1.0 that moves the average ~40x more.
- `weighted_deficit`, `directive.recovers_weighted_deficit_points` and
  `gap_analysis.weighted_gap_points` share one unit — *score-points x NLOC* —
  which compares against itself and nothing else. Every `high_leverage_files`
  row and the `directive` also carry `share_of_repo_gap_pct`, the same quantity
  over the gross deficit of all below-target files
  (`gap_analysis.weighted_gross_gap_points`), so the shares are bounded by 100%
  and sum to 100% by construction; that plus
  `gap_analysis.files_to_reach_target` is what answers "is this worth doing".
- `_meta.health_semantics` gives the numerator, gross-deficit denominator,
  nonnegative unbounded scale and direction. These are deterministic heuristic
  triage points, not probabilities, normalized score points, percentages or
  guaranteed improvement.
- `kpis.non_code_files` and `kpis.average_health_code_only` say how much of the
  headline is markdown/JSON/YAML. No biomarker walks those files, so they score
  a mechanical 10.0 meaning "nothing looked at this" — on this repo, 233 of
  3,314 rows, lifting `average_health` from 7.31 to 7.47. `average_health`
  itself deliberately still counts them, so the tool, the badge, the snapshots
  and the web UI all report the same number.

**One score, not two.** A metric row carries `score` (the defect dimension and
the headline), `maintainability_score` and `performance_score`. There is no
`defect_score` in the response: it was set from the same value as `score` on
every row, and two names for one number cost a reader a source dive to pick
between them. The field to rank on is neither — it is `weighted_deficit`.

**`primary_biomarker` names a discrete cause.** It prefers the strongest
*discrete* finding over a continuous one. `coverage_gradient` fires on every
file that has coverage data at all, so on a well-covered repo it used to win the
max-impact tiebreak nearly everywhere and headline the list with "N% of lines
uncovered" — true, and equally true of every other file. The gradient still
counts in full toward `total_deduction` and the score, and still leads a file
that has no discrete finding.

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
- **`refactoring`** returns `refactoring_opportunities`: one composed unit per
  file, carrying the diagnosis it leads with (`lead_biomarker`), whether it
  actually addresses that diagnosis (`addresses_primary_problem`, tri-state -
  `null` means no dominant finding was recorded, which is not `false`), its
  ordered `steps` with a `mechanical` / `judgment` `applicability` each, and
  counts for the evidence behind it. Ordered by `refactoring_view`. A step
  carrying `relocated_by` names an earlier step that moves its symbol to another
  file: locate the symbol again before applying it, because the step's own
  `file_path` and span describe where the symbol was.
  `get_health(opportunity_id="refop...")` returns the full ordered steps, the
  member plan payloads, the validation profile and structured `next_actions`;
  `only=["refactoring_evidence"]` plus `cursor` pages the evidence.
- **`refactoring_directive`** rides on a bare `get_health()`: one opportunity,
  what it addresses, and the exact `opportunity_id` call that opens it. One
  primary-key read; it never touches the queue. **`refactoring_summary`**
  (`only=["refactoring_summary"]`) is the rollup by type, effort, confidence,
  lifecycle and mechanical-vs-judgment, with facets.
- **`refactoring_plans`** is the raw per-detector list, unchanged and still
  addressable by `plan_id`, but **opt-in**: name it in `only` to get it. It
  returns ranked, structured refactoring plans (not template
  strings): `extract_class` (the cohesion `groups` to split into), `extract_helper`
  (clone `occurrences` + `suggested_site`), `move_method` (`{method, from_class,
  to_class}`), and `break_cycle` (the import `cut_edges`). Each plan carries its
  `evidence`, `impact_delta`, `effort_bucket`, `blast_radius`, and an `id` you can
  hand to `generate_refactoring_code`. The list is capped to `limit` and ranked
  file-leverage-first (by the file's `weighted_deficit`, then per-plan impact), so
  plans on the files that move the headline surface first; `refactoring_plans_total`
  reports the full count behind the cap. Each plan echoes its
  `file_weighted_deficit`. Full shapes in [`docs/layers/REFACTORING.md`](../layers/REFACTORING.md).
- A requested empty plan list includes `refactoring_plans_status.reason`:
  `no_applicable_findings`, `plan_analysis_indeterminate`,
  `no_eligible_targets`, or `analysis_unavailable`. The structured-analysis
  fallback includes a concrete `get_symbol` or `get_context` source call and
  explicitly names the two facts the stored data cannot distinguish: no
  supported transformation vs a disabled or failed detector.
  Projection exclusion emits no plan warning; a zero-row cursor window is
  reported separately as `request_window_empty`.
- **dimension filter** narrows the returned findings to one pillar, e.g.
  `include=["biomarkers", "performance"]`.

**Performance findings rank on `perf_rank`, not on `health_impact`.** Every
performance finding carries `health_impact: 0` — the pillar is deliberately
never blended into the score — so ranking them by impact ordered them by nothing
and the cap kept whichever the tie broke to. Each performance row now carries an
integer `perf_rank` (absent on defect and maintainability rows, which rank on
`weighted_deficit`), and the returned list is ordered by it *within* each impact
tier, so the defect ordering is untouched. It is an ordering key, not a score:
nothing is blended into `score` / `performance_score`. It adds three signals the
row already carries, so you can re-rank on your own weights from the same
payload:

| signal | reads | why |
|---|---|---|
| the marker | `biomarker_type` | superlinear (`nested_loop_quadratic`, `nested_loop_with_io`, `sql_cartesian_join` 6) > lock-serialized (5) > one crossing per iteration, or one proven on a hot path (4) > in-loop CPU/allocation (3) > repeated acquisition with no boundary (2); an unweighted marker takes the floor (1) |
| the boundary | `details.boundary_kind` | `subprocess` 5 > `network`/`db` 4 > `lock` 3 > `filesystem` 2. A process spawn in a loop is not a stat in a loop |
| the call shape | `details.cross_function` | +1. An intra-function loop is usually visibly bounded at the call site; a cross-function N+1 is the one nobody sees by reading the loop |

These are the same weights the causal opportunity ranking reads. Two tables
used to answer "which marker costs more" and had drifted apart on markers both
named, so a finding and the opportunity built from it could disagree about the
same evidence.

Request-reachability is read off the marker rather than a column:
`hot_path_sync_io` and `nested_loop_quadratic` are only ever emitted for a
function the perf ranker called hot (top-quintile call-graph in-degree, or a
churny/hotspot file), so their presence is already the proof. Deliberately not
`severity` — that column grades `hot_path_sync_io` below `io_in_loop` and takes
only two values across a whole repo's perf findings.
- **`refactoring`** also emits `suggestion_legend`: `biomarker_type` → the prose
  suggestion for that type, once per response rather than per finding. Join on
  `biomarker_type`. It is keyed off the ranked finding head and does not vary
  with `only`, so it can carry an entry for a block a projection dropped —
  extra rows in a lookup table, never a missing one. Note it explains the
  **findings**, not the
  plans it ships beside — the two sets differ (no plan kind is sourced from
  `coverage_gradient`), and `directive.plan_addresses_reason` is what reports
  that gap.

**When to use:** Before opening a PR, to self-check the files you changed
(`targets=[...], include=["signals"]`) and confirm you are not regressing the
worst files. Before refactoring, find the worst-scoring files and what to fix
first (`include=["accuracy", "churn_complexity"]`). Pair with `get_risk` on
hotspots.

**Example calls:**

```
get_health(only=["directive"])                        # cheapest useful call: what to fix first
get_health()                                          # directive, kpis, gap_analysis, worst + high_leverage files
get_health(include=["accuracy", "churn_complexity"])
get_health(include=["biomarkers", "performance"])     # only performance findings
get_health(targets=["src/api/server.py"], include=["signals"])
get_health(targets=["module:src.api"], include=["trend", "refactoring"])
get_health(include=["accuracy"], only=["accuracy"])   # the block, without the dashboard again
get_health(only=["top_findings"])                     # + top_findings_total, automatically
get_health(only=["kpis"], limit=0)                    # headline numbers, no rows at all
```

### Performance: one lead, then drill down

A bare `get_health()` carries `performance_directive`: one bounded lead with
its status (`plan_ready` / `advisory` / `investigate` / `clear` / `unavailable`),
up to three `why_ranked` facets, the exact plan state, and a structured
`next_action`. Performance findings carry `health_impact: 0` by construction, so
they never competed for the main `directive` and the dashboard used to report
counts and nothing to act on. `clear` means no supported pattern surfaced, which
is not a claim about how the code runs; `unavailable` means this index has not
materialized the analysis yet, or did so under an older model.

```
get_health()                                                  # the lead
get_health(include=["performance"], only=["performance_summary"])
get_health(include=["performance"], only=["performance_opportunities"], performance_context="all")
get_health(opportunity_id="perf2_...")                        # the cause, its plan, its evidence
get_health(opportunity_id="perf2_...", only=["performance_evidence"], cursor=3)
```

Ids are stable within a performance model version and are never translated
across one, because grouping decides membership and two models disagree about
it. An id from an older model resolves to `model_state.state: "stale_model"`
with `refresh_required`, rather than failing to match and reading as "no plan".
Evidence rows carry the finding's public `finding_id`, which round-trips through
the `finding_id` selector; storage row ids are republished on every analysis and
are never emitted.

---

## Supplementary tools

These are registered and on by default (in the modes noted) but are not part
of the ten-tool headline set.

### `list_repos`

Lists the repos this server is serving. No parameters.

**Returns:** In workspace mode, `workspace: true`, the workspace root, the default repo alias, and every configured repo's `alias`, config-relative `path`, and `absolute_path`. Any of those emitted identities can be passed unchanged as `repo` to workspace-aware tools. In single-repo mode, `workspace: false` and a single `"default"` alias.

**When to use:** Discovering the `repo` aliases to pass to other tools, especially in workspace mode.

```
list_repos()
```

### Workspace-only tools

*(Available only when the server is started inside a workspace; see [Workspace Mode](#workspace-mode).)*

#### `get_blast_radius`

Cross-repo structural and historical reach from a changed service.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `targets` | list[string] | Yes | Node ids (`repo` or `repo::service/path`) or repo aliases |
| `max_depth` | int | No | Reachability depth (1-8, default 3) |
| `include_behavioral` | bool | No | Include co-change (behavioral) edges (default `true`) |

**Returns:** The impacted services ranked by uncalibrated `score` (0-1 relative
path weight, not a breakage probability), each with `distance` (hops),
`structural` (a real dependency vs co-change only), and the edge kinds that
carried the impact; plus `impact_score_semantics`, `impacted_repos`,
`structural_count` / `behavioral_count`, `total_impacted`, and unresolved targets.

Each `symbol_targets[].consumers[]` row also carries a `tests` block of the same
shape as `get_change_risk`'s: which tests in that consumer repo guard the
symbol's contracts, capped at five with the tail in the omission store, and a
named reason when a link could not be followed.

**When to use:** Before changing a high-fan-out provider, see who structurally
consumes it across repo boundaries. Structural reach outweighs historical
co-change in ranking, but neither is a runtime-breakage claim. Reads the same
system graph the [Live System Map](../scale/WORKSPACES.md#live-system-map) renders.

```
get_blast_radius(targets=["backend"])
get_blast_radius(targets=["mono::services/auth"], max_depth=2, include_behavioral=false)
```

#### `get_conformance`

Architecture governance: does the live system graph obey the declared dependency rules, and are there circular service dependencies?

**Opt-in.** Off by default even in workspace mode; enable with `mcp.tools: ["+get_conformance"]`. Named in single-repo mode it is ignored, since it needs the workspace graph. The same findings still surface in the `get_risk` PR-mode directive (`conformance_violations` / `dependency_cycles`) without opting the tool in.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `repo` | string | No | Limit findings to those involving this repo alias |

**Returns:** `violations` (each with the offending `source`/`target` services, the `rule_source`/`rule_target` matchers that fired, and the `edge_kind`), `cycles` (each with the participating `nodes` and `length`), and the `violation_count` / `cycle_count` / `rules_evaluated` rollups.

**When to use:** Before a refactor that changes service boundaries, or to audit whether the live architecture still matches the intended one. Rules are declared under `conformance:` in `.repowise-workspace.yaml`. See [Architecture Conformance](../scale/WORKSPACES.md#architecture-conformance).

```
get_conformance()
get_conformance(repo="frontend")
```

#### `get_architecture`

The one evaluative read of the whole system: how coupled is it, where is the architectural core, and a single 1-10 architecture score. Deterministic, structural edges only (co-change excluded). No parameters.

**Returns:** `score` (1-10), `architecture_type` (`core-periphery` or `hierarchical`), `propagation_cost_pct` (share of other services the average service reaches), `core_size` / `core_ratio` / `core_members` (the largest cyclic group), `cycle_count`, `conformance_violations`, a `role_breakdown` (count of Core / Shared / Control / Peripheral services), and a one-line `summary`.

**When to use:** Before a cross-service refactor, or to gauge and compare overall system structure over time. See [Architecture Metrics](../scale/WORKSPACES.md#architecture-metrics).

```
get_architecture()
```

### Opt-in tools

*(Registered but off by default in every mode; enable with `mcp.tools: ["+name"]` or `repowise mcp --tools "+name"`. See [Configuring the tool surface](#configuring-the-tool-surface).)*

#### `get_dependency_path`

Shortest dependency path between two files or modules.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source` | string | Yes | Source file or module path |
| `target` | string | Yes | Target file or module path |
| `repo` | string | No | *(workspace only)* Target repo alias; `"all"` is not supported |

**Returns:** The dependency path when one exists. When no direct path exists, visual context instead: nearest common ancestors, shared neighbors, community analysis, and bridge suggestions, to help debug architectural silos.

**When to use:** Understanding how two parts of the codebase are (or aren't) connected, or why an expected dependency doesn't show up.

```
get_dependency_path(source="src/api/routes.py", target="src/db/models.py")
```

#### `get_execution_flows`

Top entry points and their call traces: how the codebase actually executes.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `top_n` | int | No | Number of top entry points to trace (default 10) |
| `max_depth` | int | No | Max trace depth per flow (default 8) |
| `entry_point` | string | No | Trace from a specific symbol, overriding `top_n` scoring |
| `repo` | string | No | *(workspace only)* Target repo alias; `"all"` is not supported |

**Returns:** Scored entry points with BFS call-path traces showing which functions are called in sequence, and whether the flow crosses community boundaries.

**When to use:** Understanding runtime call flow through an unfamiliar system, or tracing what a specific entry point actually does end to end.

```
get_execution_flows()
get_execution_flows(entry_point="src/cli/main.py::main", max_depth=4)
```

#### `generate_refactoring_code`

Turns one structured refactoring plan from `get_health(include=["refactoring"])` into actual generated code and a unified diff, grounded on the plan plus the real source spans it references. For Extract Class, the result includes an LCOM4 before/after self-check.

**Off by default twice over:** it must be opted into the tool surface (`mcp.tools: ["+generate_refactoring_code"]`), and generation remains unavailable unless `refactoring.llm.enabled: true` is set in the repo's `.repowise/config.yaml`. A valid plan id still resolves while generation is disabled, returning the canonical plan plus `generation.available: false`. When enabled, it uses the repo's configured LLM provider/model (bring your own key) and caches results by a content hash, so an unchanged plan never regenerates.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `suggestion_id` | string | Yes | The `id` of a plan returned by `get_health(include=["refactoring"])` |
| `repo` | string | No | *(workspace only)* Target repo alias |

**When to use:** After `get_health(include=["refactoring"])` surfaces a plan you want turned into an applyable diff, and your repo has opted into both the tool and LLM-backed generation.

```
generate_refactoring_code(suggestion_id="a1b2c3d4")
```

---

## Workspace Mode

In workspace mode (initialized with `repowise init .`), all tools accept an optional `repo` parameter:

- **Omit `repo`**: queries the default (primary) repo
- **`repo="backend"`**: targets a specific repo by any `alias`, `path`, or `absolute_path` emitted by `list_repos`
- **`repo="all"`**: queries across all workspace repos (fully supported by `search_codebase`; `get_context` and `get_overview` also accept it; not supported by `get_symbol`, `get_dependency_path`, or `get_execution_flows`)

The MCP server automatically enriches responses with cross-repo intelligence:
- **Co-change partners** from other repos surfaced in `get_context` and `get_risk`
- **API contract links** (HTTP, gRPC, topics) between repos
- **Package dependencies** between repos
- **Cross-repo blast radius** via the workspace-only `get_blast_radius` tool, and a cross-repo `directive` in `get_risk` PR-mode
- **Breaking-change guard**: incompatible provider-contract changes and the consumers they endanger, in the `get_risk` PR-mode `breaking_changes` directive
- **Architecture conformance**: declared dependency-rule violations and dependency cycles via the workspace-only, opt-in `get_conformance` tool, and `conformance_violations` / `dependency_cycles` in the `get_risk` PR-mode directive
- **Architecture metrics**: whole-system coupling (propagation cost), the cyclic core, per-service roles, and a deterministic 1-10 architecture score via the workspace-only `get_architecture` tool

---

## Proactive Hooks (Complementary)

In addition to the MCP tools above, `repowise init` installs AI-agent hooks (Claude Code and Codex) that provide **passive, automatic** context enrichment:

- **Claude Code PostToolUse**: broad or zero-result `Grep`/`Glob` calls can be enriched with graph context, and git operations can trigger stale-wiki notices.
- **Codex SessionStart**: Codex receives concise repowise MCP workflow guidance when a session starts.
- **Codex PostToolUse**: after edits or git operations, Codex receives a freshness reminder when indexed context may be stale.

Hooks are lightweight reminders. MCP tools are for deeper, on-demand investigation. See [Auto-Sync](../scale/AUTO_SYNC.md) and [Codex Integration](CODEX.md) for details.
