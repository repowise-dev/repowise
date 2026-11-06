---
description: Search the Repowise wiki using natural language, full-text, or symbol search.
allowed-tools: Bash(repowise search:*), Read
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

- `--mode fulltext`: fast keyword search (default)
- `--mode semantic`: vector similarity search using embeddings. Requires an embedding provider to be configured. If this fails with an error, suggest running `/repowise:reindex` first.
- `--mode symbol`: fuzzy match on symbol names (functions, classes, variables)

Other flags:
- `--limit N`: max results (default: 10)

## Present results

Show results as a clean list with the page title, type, and a brief snippet.

## When search comes up empty

Search reads the documentation pages, and an index built from code has them
too, so an empty result is usually not missing documentation.

- **No pages at all** means the index never finished, or the repo is Index
  only (`--mode fast`), the one mode with no documentation. Suggest
  `repowise update --full` (no API key needed), or `repowise init --resume` if
  a previous run was interrupted.
- **Pages exist but semantic search finds nothing** means the pages were
  embedded with the keyless `mock` embedder, which keeps full-text search
  working and leaves semantic search off. Suggest
  `repowise reindex --embedder <name>` once an embedder key is set (the user
  sets it, never in the chat). Full-text and symbol search (`--mode symbol`)
  work regardless.
