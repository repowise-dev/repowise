---
name: pre-modification-check
description: >
  Use before modifying, refactoring, or deleting files in a codebase that has Repowise indexed
  (indicated by a .repowise/ directory). Activates when Claude is about to edit code, especially
  shared utilities, core modules, or files the user didn't explicitly mention. Helps assess
  impact and avoid breaking things.
user-invocable: false
---

# Pre-Modification Check with Repowise

Before modifying files in a Repowise-indexed codebase, assess the impact.

## Before editing a file

Call `get_risk(targets=["path/to/file.py"])`. Per file it returns
`hotspot_score`, `trend`, `risk_type`, `impact_surface` (top 3),
`dependents_count`, `co_change_partners`, `primary_owner`, `bus_factor`,
`test_gap`, and `security_signals`. Read it for:
- **Hotspot status** (`hotspot_score`, `trend`) — high-churn × complex? Extra care needed.
- **Dependents** (`dependents_count`, `impact_surface`) — how wide is the blast radius?
- **Co-change partners** — files that change together with this one (often without an import link); you may need to update them too.
- **Ownership / bus factor** — who owns it, and whether a single author maintains it.
- **Test gap & security signals** — flag untested or security-sensitive files before touching them.

## When modifying multiple files

Batch all targets into one call: `get_risk(targets=["file1.py", "file2.py", "module/"])`.

## When to warn the user

If `get_risk` shows:
- Hotspot score above 90th percentile — mention this is a frequently-changed, high-risk file
- More than 10 dependents — list the top dependents; API changes here will break consumers
- Bus factor of 1 — note that a single person maintains this code
- Risk type is "bug-prone" or "high-coupling" — flag explicitly before making changes

## Before refactoring or moving code

Call `get_context(targets=["file.py"])` first to understand the full context: what uses this file, what decisions govern it, and why it's structured this way. This prevents accidentally violating architectural decisions.

For a heavy refactor, also call `get_health(targets=["file.py"])` — the
marker findings (complexity, deep nesting, low cohesion, duplication) tell
you *what* to improve while you're in there, and give you a before/after score.

## Error handling

If `get_risk` returns a tool error, the MCP server may not be running. Proceed with the modification but note that risk assessment was unavailable.
