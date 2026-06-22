---
name: codebase-exploration
description: >
  Use when exploring, understanding, or answering questions about a codebase that has Repowise
  indexed (a .repowise/ directory in the project root). Activates for "how does X work",
  "explain the architecture", "where is Y implemented", "what does this module do", or any task
  that needs an understanding of structure before diving into source files.
user-invocable: false
---

# Codebase Exploration with Repowise

This project has a Repowise intelligence layer. Before grepping and reading raw
source to understand the codebase, reach for the Repowise MCP tools — they
return documentation, ownership, history, decisions, and graph structure that
plain file reads don't, usually in one round-trip instead of many.

## Which tool for which question

| You want… | Call |
|---|---|
| First orientation in an unfamiliar repo | `get_overview()` — architecture summary, key modules, entry points, git health, knowledge map. Skip it once you have the map. |
| A direct answer to "how/where/why does X work" | `get_answer(question="…")` — synthesised answer with citations + a `retrieval_quality` signal. Collapses the search → read → reason loop. |
| Find a symbol, file, or fuzzy concept | `search_codebase(query="…")` — hybrid search. `mode="auto"` routes an identifier to indexed symbol hits (`symbol_id`/line bounds → pipe into `get_symbol`), a path to file pages (→ `get_context`), and prose to semantic wiki search (each hit reports `search_method`: `embedding` vs `bm25`). Force a branch with `mode=symbol\|path\|concept\|hybrid`; narrow symbols with `symbol_kind`. |
| A triage card for specific files/symbols | `get_context(targets=[…])` — title, summary, signatures, hotspot bit, top callers, decision titles, symbol_ids. Batch many targets in one call. |
| The actual source of one symbol | `get_symbol("path/to/file.py::Name")` — exact bytes with line bounds. Cheaper than Read + offset math. Use a `symbol_id` from `get_context`. |

## Recommended flow

1. New area you don't know → `get_overview()` once.
2. A specific question → `get_answer(question=…)` first.
   - High confidence → answer it, cite the paths.
   - `medium`/`low` confidence → follow `best_guesses[0].file` or
     `fallback_targets[0]` into `get_context`, then `get_symbol` for bytes.
3. A named symbol or a path → `search_codebase(query="Name")` /
   `search_codebase(query="path/to/file.py")`; symbol hits pipe straight into
   `get_symbol`, file hits into `get_context`.
4. More files around a concept → `search_codebase`, then `get_context` on the
   hits (batched), then `get_symbol` only for the bodies you actually need.

Fall back to raw Read/Grep only when the indexed context doesn't cover the
specific detail the user asked about.

## Trust signals — verify when

- `_meta.stale_warning` is present (the index has diverged from HEAD), or
- `retrieval_quality` is `partial`/`weak`, or
- a result's `search_method` is `bm25`.

Otherwise the response is current — act on it.

## Error handling

- "No repositories found. Run 'repowise init' first." → suggest `/repowise:init`.
- `get_answer`/`search_codebase` come back empty → the repo may be in index-only
  mode (no wiki). Fall back to `get_context` with explicit paths, and note that
  full mode (`/repowise:init` with an LLM provider) unlocks docs + semantic search.
- Tools fail to connect at all → the `repowise` binary may not be installed;
  suggest `/repowise:init`.
