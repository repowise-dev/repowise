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

## Analysis-only mode

If the search returns no results and the repo appears to be in analysis-only mode (no wiki pages), tell the user: "Semantic and full-text search require documentation to be generated. Your repo is in analysis-only mode. Run `/repowise:init` again with an LLM provider to enable full documentation and search. Symbol search (`--mode symbol`) may still work."
