---
description: Search the Repowise wiki using natural language, full-text, or symbol search.
allowed-tools: Bash, Read
---

# Repowise Search

Search the codebase wiki.

## Usage

If $ARGUMENTS is empty, ask: "What are you looking for? You can search with natural language like 'how does authentication work' or 'where is rate limiting handled'."

If $ARGUMENTS is provided, run:
```
repowise search "$ARGUMENTS"
```

## Search modes

The default mode is `fulltext` (SQLite FTS5). You can specify a mode:

- `--mode fulltext` — fast keyword search (default)
- `--mode semantic` — vector similarity search using embeddings. Requires an embedding provider to be configured. If this fails with an error, suggest running `/repowise:reindex` first.
- `--mode symbol` — fuzzy match on symbol names (functions, classes, variables)

Other flags:
- `--limit N` — max results (default: 10)

## Present results

Show results as a clean list with the page title, type, and a brief snippet.

## When search comes up empty

Search needs wiki pages, and every indexed repo has them: a keyless run renders
the whole wiki from structure. So an empty result is usually not a missing wiki.

- **No pages at all** means the index never finished, or the repo was indexed
  with `--mode fast`, which is the one mode that writes no wiki. Suggest
  `repowise init --yes` (no API key needed), or `repowise init --resume` if a
  previous run was interrupted.
- **Pages exist but semantic search finds nothing** means the wiki was embedded
  with the mock embedder, which keeps full-text search working and leaves
  semantic search to be built later. Suggest `repowise reindex` with an embedder
  configured. Full-text and symbol search (`--mode symbol`) work regardless.
