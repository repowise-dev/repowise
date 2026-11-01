---
claude-code:
  dir: codebase-exploration
  frontmatter: |
    name: codebase-exploration
    description: >
      Use when exploring, understanding, or answering questions about a codebase that has Repowise
      indexed (a .repowise/ directory in the project root). Activates for "how does X work",
      "explain the architecture", "where is Y implemented", "what does this module do", or any task
      that needs an understanding of structure before diving into source files.
    user-invocable: false
codex:
  dir: codebase-exploration
  frontmatter: |
    name: codebase-exploration
    description: Use when exploring, understanding, or answering questions about a Repowise-indexed codebase, including architecture, where code is implemented, how a module works, or which files are relevant before reading source.
---

# Codebase Exploration with Repowise

This project has a Repowise intelligence layer. For an exact identifier, string
or file name, Grep, Glob and Read are faster. For how code works, who calls it,
what a change breaks and why it is shaped this way, reach for the Repowise MCP
tools: they return documentation, ownership, history, decisions, and graph
structure that plain file reads don't, usually in one round-trip instead of many.

## Which tool for which question

| You want… | Call |
|---|---|
| An exact identifier, string or file name | Grep, Glob and Read. |
| First orientation in an unfamiliar repo | `get_overview()`: architecture summary, key modules, entry points, git health, knowledge map. Skip it once you have the map. |
| How X works or flows end to end | `get_answer(question="how does …")`, one question per call: synthesised answer with citations + one `confidence` trust signal. Collapses the search → read → reason loop. |
| Callers before a rename or signature change | `get_context(targets=["<symbol>"], include=["references"])`: definition, imports and calls; a `complete` list is the whole edit set. |
| What breaks if X changes | `get_risk(targets=["<file>"])`. |
| Why X is shaped this way, before changing a pattern | `get_why(query="why …", targets=["<file>"])`. |
| Before finishing a multi-file change | `get_change_risk(revspec="<base>..HEAD")`, then `get_health(targets=["<files you touched>"])`. |
| A fuzzy concept, or symbol ids and line bounds to feed `get_symbol` | `search_codebase(query="…")`: hybrid search. `mode="auto"` routes an identifier to indexed symbol hits (`symbol_id`/line bounds → pipe into `get_symbol`), a path to file pages (→ `get_context`), and prose to semantic wiki search (each hit reports `search_method`: `embedding` vs `bm25`). Force a branch with `mode=symbol\|path\|concept\|hybrid`; narrow symbols with `symbol_kind`. |
| A triage card for specific files/symbols | `get_context(targets=[…])`: title, summary, signatures, hotspot bit, top callers, decision titles, symbol_ids (a row without one is `path::name`). Batch many targets in one call. |
| The actual source of one symbol | `get_symbol("path/to/file.py::Name")`: exact bytes with line bounds. Cheaper than Read + offset math. Use a `symbol_id` from `get_context`. |

## Recommended flow

1. New area you don't know → `get_overview()` once.
2. A specific question → `get_answer(question=…)` first.
   - High confidence → answer it, cite the paths.
   - `medium`/`low` confidence → follow `best_guesses[0].file` or
     `fallback_targets[0]` into `get_context`, then `get_symbol` for bytes.
3. A named symbol or a path you only need to find → Grep, Glob or Read. When
   you need its callers, risk or history, pass it to `get_context`, `get_risk`
   or `get_why` instead.
4. More files around a concept → `search_codebase`, then `get_context` on the
   hits (batched), then `get_symbol` only for the bodies you actually need.

Read source directly for details the indexed context doesn't cover.

## Trust signals — verify when

- `_meta.stale_warning` is present (the index has diverged from HEAD), or
- `confidence` is `medium`/`low`, or
- a result's `search_method` is `bm25`.

Otherwise the response is current — act on it.

## Error handling

- "No repositories found. Run 'repowise init' first." → suggest `{{cmd:init}}`.
  Add `--no-editor-setup` if this repo is a scratch clone, a fixture, or a
  worktree, so `init` writes nothing outside `.repowise/` (no editor config,
  agent hooks or git hook).
- MCP tools unavailable → prefer the matching CLI slash commands when the
  plugin is installed (`{{cmd:ask}}`, `{{cmd:context}}`, `{{cmd:symbol}}`,
  `{{cmd:search}}`) rather than grepping blind.
- `get_answer`/`search_codebase` come back empty → the repo may have a
  template-rendered wiki. Fall back to `get_context` with explicit paths, and note
  that model-written pages (`repowise generate`, or `{{cmd:init}}` with an LLM
  provider) unlock richer docs + semantic search.
- Tools fail to connect at all → the `repowise` binary may not be installed;
  suggest `{{cmd:init}}`.
