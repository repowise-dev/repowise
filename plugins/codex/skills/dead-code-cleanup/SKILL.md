---
name: dead-code-cleanup
description: Use when the user asks about unused code, cleanup, deleting files or exports, refactoring old areas, reducing bundle size, code hygiene, technical debt, or maintenance in a Repowise-indexed repository.
---

# Dead Code Cleanup With Repowise

Repowise detects dead code through graph analysis and git context. Treat findings as a cleanup plan, not automatic permission to delete.

## Finding Dead Or Unused Code

Call `get_dead_code()` to get findings organized by confidence tier. Useful parameters:

- `safe_only=true` for findings already marked safe to delete.
- `min_confidence=0.7` for high-confidence cleanup work.
- `kind="unreachable_file"` for files with no importers.
- `kind="unused_export"` for public symbols with no known consumers.
- `kind="zombie_package"` for monorepo packages with no consumers.
- `directory="src/old/"` to limit scope.
- `tier="high"` for the highest-confidence band.

## Presenting Findings

- Only recommend deletion for findings with `safe_to_delete: true`.
- Present lower-confidence findings as candidates to investigate.
- Flag dynamic loading, plugin systems, route handlers, adapters, and public APIs as common false-positive zones.

## Before Deleting Anything

1. Confirm with the user.
2. Present the file or symbol, confidence score, and why Repowise thinks it is dead.
3. Call `get_risk(targets=["path/to/file"])` before deleting files or exports.
4. If the finding has recent git activity, note the higher false-positive risk.

## Safe Deletion Order

1. Unreachable files first.
2. Unused internal symbols next.
3. Unused exports last.
