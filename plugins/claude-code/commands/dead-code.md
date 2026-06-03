---
description: Report unreachable files, unused exports, and zombie packages, tiered by confidence.
allowed-tools: Bash, Read
---

# Repowise Dead Code

Find dead code through graph reachability + git context. No LLM — works in
index-only mode. Findings are tiered by confidence; only `safe_to_delete` ones
should be proposed for removal.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/repowise:init` first." Stop.
2. Run `repowise dead-code` (with any filters from `$ARGUMENTS`).
3. Present findings grouped by confidence. Mark which are `safe_to_delete`. For
   lower-confidence ones, frame them as "candidates to investigate," not deletions.

## Filters from `$ARGUMENTS`

- A path → `repowise dead-code <path>`
- "safe" → `--safe-only` (only safe_to_delete findings)
- "files" → `--kind unreachable_file`
- "exports" → `--kind unused_export`
- "packages" / "zombie" → `--kind zombie_package`
- "strict" → `--min-confidence 0.7` (default is 0.4)

Other flags: `--format json`, `--include-internals` (aggressive, private-symbol
scan; higher false-positive rate), `--include-zombie-packages` /
`--no-include-zombie-packages`, `--no-unreachable`, `--no-unused-exports`,
`--repo <alias>` / `--no-workspace`.

For per-directory or per-owner rollups, use the `get_dead_code` MCP tool with
`group_by="directory"` or `group_by="owner"` (these are tool params, not CLI flags).

## Before suggesting any deletion

- Confirm with the user; show the name, confidence, and why it looks dead.
- Recently-touched "dead" code is more likely a false positive — flag it.
- Dynamically-loaded patterns (plugins, handlers, adapters, framework routes)
  can look unused but aren't. Repowise filters the common cases; edge cases remain.
- Double-check blast radius with `get_risk(targets=[...])` before removing files
  or exported symbols.
