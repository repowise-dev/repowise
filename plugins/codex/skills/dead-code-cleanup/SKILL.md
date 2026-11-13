---
name: dead-code-cleanup
description: Use when the user asks about unused code, cleanup, deleting files or exports, refactoring old areas, reducing bundle size, code hygiene, technical debt, or maintenance in a Repowise-indexed repository.
---

# Dead Code Cleanup with Repowise

Repowise detects dead code through graph analysis — no LLM needed, works even with a template-rendered wiki.

## When the user asks about dead/unused code

Run `repowise dead-code` to get findings sorted by confidence tier. Useful flags:
- `--safe-only` — only deletion-ready findings (high confidence, no runtime-load risk factors)
- `--kind unreachable_file` — files with no importers
- `--kind unused_export` — public symbols nobody uses
- `--kind zombie_package` — monorepo packages with no consumers
- `--min-confidence 0.7` — raise the floor for a release-ready cleanup
- `--format json` — machine output

To roll findings up by directory or owner, the `get_dead_code` MCP tool takes
`group_by="directory"` or `group_by="owner"`. To list it, add `+get_dead_code`
to `mcp.tools` in `.repowise/config.yaml` and restart the MCP server.

## How to present findings

- Only suggest deletion for findings with `safe_to_delete: true`
- For lower-confidence findings, present them as "candidates to investigate" not "things to delete"
- Dynamically-loaded code (plugins, handlers, adapters) may appear as dead code but isn't — Repowise filters common patterns but edge cases exist

## Before deleting anything

1. Confirm with the user. Present the file/symbol name, confidence score, and why Repowise thinks it's dead.
2. Run `repowise risk --target path/to/file` to double-check dependents.
3. Recently-modified "dead" code is more likely a false positive — flag this if the finding has recent git activity.

## Safe deletion order

1. Unreachable files first (whole file removal, cleanest)
2. Unused internal symbols next
3. Unused exports last (highest false-positive risk due to potential dynamic imports)
