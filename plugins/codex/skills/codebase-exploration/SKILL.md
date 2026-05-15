---
name: codebase-exploration
description: Use when exploring, understanding, or answering questions about a Repowise-indexed codebase, including architecture, where code is implemented, how a module works, or which files are relevant before reading source.
---

# Codebase Exploration With Repowise

This project has a Repowise intelligence layer. Use Repowise MCP tools before broad source browsing so the answer starts from indexed docs, ownership, graph structure, git signals, and decisions.

## Starting A New Exploration Task

Call `get_overview()` first. It returns the architecture summary, module map, entry points, and tech stack.

## Answering How Or Where Questions

1. Call `search_codebase(query="topic or symbol")` to find relevant documented modules and files.
2. Call `get_context(targets=[...])` with all relevant files from the search results in one batch.
3. Read raw source only after the indexed context is not specific enough for the user’s question.

## Understanding Connections Between Modules

Call `get_dependency_path(source="module_a", target="module_b")` when the user asks how two areas connect.

## Getting Diagrams

Call `get_architecture_diagram(scope="module", path="path/to/module")` for a subsystem diagram, or `get_architecture_diagram()` for the full repository.

## Error Handling

- If tools report that no repositories were found, suggest running `repowise init`.
- If `search_codebase` has no useful results, the repository may be index-only; fall back to `get_context` with specific paths.
- If MCP tools are unavailable, proceed with normal source inspection and mention that Repowise context was unavailable.
