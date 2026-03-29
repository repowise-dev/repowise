---
name: codebase-exploration
description: >
  Use when exploring, understanding, or answering questions about a codebase that has Repowise
  indexed (indicated by a .repowise/ directory in the project root). Activates for questions like
  "how does X work", "explain the architecture", "where is Y implemented", "what does this module do",
  or any task requiring understanding of codebase structure before diving into source files.
user-invocable: false
---

# Codebase Exploration with Repowise

This project has a Repowise intelligence layer. Before reading raw source files to understand the codebase, use Repowise MCP tools — they provide richer context including documentation, ownership, history, and architectural decisions.

## When starting a new exploration task

Call `get_overview()` first. This returns the architecture summary, module map, entry points, and tech stack. This single call replaces reading dozens of files to understand the project structure.

## When answering "how does X work" questions

1. Call `search_codebase(query="X")` to find the most relevant documented modules and files.
2. Call `get_context(targets=[...relevant files from search results...])` to get full documentation, ownership, freshness, and decisions for those targets. Batch all targets in one call.
3. Only read raw source files if the Repowise docs don't cover enough detail for the specific question.

## When asked about connections between modules

Call `get_dependency_path(source="module_a", target="module_b")` to understand how two parts of the codebase are connected through the dependency graph.

## When you need a visual overview

Call `get_architecture_diagram(scope="module", path="path/to/module")` for a Mermaid diagram of a specific subsystem, or `get_architecture_diagram()` for the full repo.

## Error handling

- If tools return "No repositories found. Run 'repowise init' first." — suggest the user run `/repowise:init`.
- If `search_codebase` returns empty results — the repo may be in analysis-only mode (no wiki pages). Note this and fall back to `get_context` with specific file paths, or suggest upgrading to full mode.
- If tools fail to connect entirely — the `repowise` binary may not be installed. Suggest `/repowise:init`.
