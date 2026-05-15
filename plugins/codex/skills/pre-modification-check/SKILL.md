---
name: pre-modification-check
description: Use before modifying, refactoring, moving, or deleting files in a Repowise-indexed repository, especially shared utilities, core modules, public APIs, or files the user did not explicitly identify.
---

# Pre-Modification Check With Repowise

Before editing a Repowise-indexed codebase, assess impact with the graph and git signals.

## Before Editing Files

Call `get_risk(targets=["path/to/file.py"])` to understand:

- Hotspot status and churn trend.
- Dependents and likely blast radius.
- Co-change partners that may need updates.
- Ownership and recommended review context.
- Bus factor and maintenance concentration.
- Test gaps or security signals.

## When Editing Multiple Files

Batch all targets in one call: `get_risk(targets=["file1.py", "file2.py", "module/"])`.

## When To Warn The User

Warn before editing when `get_risk` shows:

- Hotspot score above the 90th percentile.
- More than 10 dependents.
- Bus factor of 1.
- Risk type such as `bug-prone` or `high-coupling`.
- Missing tests around changed or affected files.

## Before Refactoring Or Moving Code

Call `get_context(targets=["path/to/file.py"])` first to understand what uses the file, which decisions govern it, and why it is structured that way.

## Error Handling

If `get_risk` fails or the MCP server is unavailable, proceed with normal inspection and mention that Repowise risk assessment was unavailable.
