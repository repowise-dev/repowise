---
name: codebase-exploration
description: Use when exploring, understanding, or answering questions about a Repowise-indexed codebase, including architecture, where code is implemented, how a module works, or which files are relevant before reading source.
---

# Codebase Exploration With Repowise

This project has a Repowise intelligence layer. Use Repowise MCP tools before broad source browsing so the answer starts from indexed docs, ownership, graph structure, git signals, and decisions.

## Which Tool For Which Question

| You want… | Call |
|---|---|
| First orientation in an unfamiliar repo | `get_overview()` — architecture summary, key modules, entry points, git health, knowledge map. Skip it once you have the map. |
| A direct answer to "how/where/why does X work" | `get_answer(question="…")` — synthesised answer with citations + a `retrieval_quality` signal. Collapses the search → read → reason loop. |
| Find a symbol, file, or fuzzy concept | `search_codebase(query="…")` — hybrid search. `mode="auto"` routes an identifier to indexed symbol hits (`symbol_id`/line bounds → pipe into `get_symbol`), a path to file pages (→ `get_context`), and prose to semantic wiki search (each hit reports `search_method`: `embedding` vs `bm25`). Force a branch with `mode=symbol\|path\|concept\|hybrid`; narrow symbols with `symbol_kind`. |
| A triage card for specific files/symbols | `get_context(targets=[…])` — title, summary, signatures, hotspot bit, top callers, decision titles, symbol_ids. Batch many targets in one call. |
| The actual source of one symbol | `get_symbol("path/to/file.py::Name")` — exact bytes with line bounds. Cheaper than a raw read + offset math. Use a `symbol_id` from `get_context`. |

## Recommended Flow

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

Fall back to raw source reads only when the indexed context doesn't cover the
specific detail the user asked about.

## Trust Signals — Verify When

- `_meta.stale_warning` is present (the index has diverged from HEAD), or
- `retrieval_quality` is `partial`/`weak`, or
- a result's `search_method` is `bm25`.

Otherwise the response is current — act on it.

## Error Handling

- If tools report that no repositories were found, suggest running `repowise init`.
- If `get_answer`/`search_codebase` come back empty, the repository may have a
  template-rendered wiki; fall back to `get_context` with specific paths, and note
  that model-written pages (`repowise generate`, or `repowise init` with an LLM
  provider) unlock richer docs + semantic search.
- If MCP tools are unavailable, proceed with normal source inspection and mention
  that Repowise context was unavailable.
