---
frontmatter: |
    description: Rebuild the Repowise vector store by re-embedding all wiki pages. No LLM calls — only embedding API calls.
    allowed-tools: Bash, Read, AskFollowupQuestion
---

# Repowise Reindex

Rebuild the vector store (embeddings) without regenerating documentation.

This is useful when:
- You switched embedding providers
- The LanceDB vector data got corrupted
- You want to refresh search quality after adding new wiki pages

## Steps

1. Check if `.repowise/` exists. If not: "This repo isn't indexed yet. Run `{{cmd:init}}` first."

2. Run: `repowise reindex`

   This re-embeds all existing wiki pages into LanceDB. No LLM generation calls — only embedding API calls. Fast and cheap.

## Available flags

- `--embedder gemini|openai|openrouter|ollama|edenai|auto` — embedding provider (default: auto-detect from env vars)
- `--batch-size N` — pages per embedding batch (default: 20)

## Requirements

Requires a key for one of the real embedders — `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` or `EDENAI_API_KEY` — or a running Ollama. The mock embedder is not accepted for reindexing. If none is available, ask the user to set one before proceeding.

## Handling $ARGUMENTS

If $ARGUMENTS contains "full" or "from-scratch", confirm with the user: "This will regenerate ALL documentation from scratch, not just re-embed. It will take as long as the initial indexing and cost API tokens. Are you sure?" If yes: `repowise init --force`
