---
name: pre-modification-check
description: Use before modifying, refactoring, moving, or deleting files in a Repowise-indexed repository, especially shared utilities, core modules, public APIs, or files the user did not explicitly identify.
---

# Pre-Modification Check With Repowise

Before editing a Repowise-indexed codebase, assess impact with the graph and git signals.

## Before Editing Files

Call `get_risk(targets=["path/to/file.py"])`. Per file it returns
`hotspot_score`, `trend`, `risk_type`, `impact_surface` (top 3),
`dependents_count`, `co_change_partners`, `primary_owner`, `bus_factor`,
`test_gap`, and `security_signals`. Read it for:

- **Bug-fix history** (`defect_profile`) — present only on files with counted
  fixes: `fix_count` over the trailing 6 months, `last_fix_days_ago`, a
  `bug_magnet` flag for sustained recent fix pressure, and `top_symbols` (the
  per-symbol counts are approximate — read them as "mostly here"). A file that
  keeps getting fixed is the strongest single signal that the next edit breaks
  something; lead with it.
- **Hotspot status** (`hotspot_score`, `trend`) — high-churn × complex? Extra care needed.
- **Dependents** (`dependents_count`, `impact_surface`) — how wide is the blast radius?
- **Co-change partners** — files that change together with this one (often without an import link); you may need to update them too.
- **Ownership / bus factor** — who owns it, and whether a single author maintains it.
- **Test gap & security signals** — flag untested or security-sensitive files before touching them.

## When Editing Multiple Files

Batch all targets in one call: `get_risk(targets=["file1.py", "file2.py", "module/"])`.

## When To Warn The User

Warn before editing when `get_risk` shows:

- A `defect_profile` with `bug_magnet` set — say so plainly: this file has been fixed repeatedly and recently.
- Hotspot score above the 90th percentile.
- More than 10 dependents — list the top dependents; API changes here will break consumers.
- Bus factor of 1.
- Risk type such as `bug-prone` or `high-coupling`.
- Missing tests around changed or affected files.

## Before Refactoring Or Moving Code

Call `get_context(targets=["path/to/file.py"])` first to understand what uses the file, which decisions govern it, and why it is structured that way.

For a heavy refactor, also call `get_health(targets=["file.py"])` — the
marker findings (complexity, deep nesting, low cohesion, duplication) tell
you *what* to improve while you're in there, and give you a before/after score.

## Error Handling

If `get_risk` fails or the MCP server is unavailable, proceed with normal inspection and mention that Repowise risk assessment was unavailable.
