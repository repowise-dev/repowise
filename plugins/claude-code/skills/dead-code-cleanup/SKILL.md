---
name: dead-code-cleanup
description: >
  Use when the user asks about cleanup, removing unused code, refactoring, reducing bundle size,
  or identifying dead code in a Repowise-indexed codebase (.repowise/ directory exists). Also
  activates when discussing technical debt, code hygiene, or repository maintenance.
user-invocable: false
---

# Dead Code Cleanup with Repowise

Repowise detects dead code through graph analysis — no LLM needed, works even in index-only mode.

## When the user asks about dead/unused code

Call `get_dead_code()` to get findings sorted by confidence tier. Useful parameters:
- `safe_only=true` — only findings confirmed safe to delete (confidence >= 0.7)
- `kind="unreachable_file"` — files with no importers
- `kind="unused_export"` — public symbols nobody uses
- `kind="zombie_package"` — monorepo packages with no consumers
- `directory="src/old/"` — limit to a specific directory
- `tier="high"` — only high-confidence findings (>= 0.8)

## How to present findings

- Only suggest deletion for findings with `safe_to_delete: true`
- For lower-confidence findings, present them as "candidates to investigate" not "things to delete"
- Dynamically-loaded code (plugins, handlers, adapters) may appear as dead code but isn't — Repowise filters common patterns but edge cases exist

## Before deleting anything

1. Confirm with the user. Present the file/symbol name, confidence score, and why Repowise thinks it's dead.
2. Call `get_risk(targets=["path/to/file"])` to double-check dependents.
3. Recently-modified "dead" code is more likely a false positive — flag this if the finding has recent git activity.

## Safe deletion order

1. Unreachable files first (whole file removal, cleanest)
2. Unused internal symbols next
3. Unused exports last (highest false-positive risk due to potential dynamic imports)
