---
description: Rebuild the Repowise vector store by re-embedding all documentation pages. No LLM calls, only embedding API calls.
---

# Repowise Reindex

Rebuild the vector store (embeddings) without regenerating documentation.

This is useful when:
- You want semantic search on an index built without an embedder key
- You switched embedding providers
- The LanceDB vector data got corrupted

## Steps

1. Check if `.repowise/` exists. If not: "This repo isn't indexed yet. Run `/prompts:repowise-init` first."

2. Run: `repowise reindex`, or `repowise reindex --embedder <name>` when the user named one.

   This re-embeds all existing documentation pages into LanceDB. No LLM generation calls, only embedding API calls.

## Available flags

- `--embedder gemini|openai|openrouter|ollama|edenai|mock|auto`: embedding provider. `auto` (the default) keeps the repo's embedder, or picks one from the keys present when the repo has none yet
- `--batch-size N`: pages per embedding batch, must be 1 or greater (default: 32)

## Requirements

Semantic search needs a real embedder: a key for `gemini` (`GEMINI_API_KEY`/`GOOGLE_API_KEY`), `openai`, `openrouter` or `edenai`, or a local Ollama with `OLLAMA_EMBEDDING_MODEL` set. When none is available, `repowise reindex` exits 1 and prints the command that turns semantic search on; relay it and ask the user to set the key themselves, never in the chat. On an Index only repo there is no documentation to embed yet: `repowise update --full` builds it first.

## Handling $ARGUMENTS

- An embedder name (`gemini`, `openai`, `ollama`, ...): run `repowise reindex --embedder <name>`.
- "full" or "from-scratch": reindex already re-embeds every page. To rebuild the documentation itself, suggest `repowise generate` (a model writes it, with a cost estimate first), or `repowise update --full` on an Index only repo (no key needed).
