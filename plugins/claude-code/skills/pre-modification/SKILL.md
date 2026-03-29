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

Call `get_risk(targets=["path/to/file.py"])` to understand:
- **Hotspot status** — is this a high-churn file? Extra care needed.
- **Dependents** — what other files/modules depend on this? How wide is the blast radius?
- **Co-change partners** — what files typically change together with this one? You may need to update them too.
- **Ownership** — who owns this code? Relevant for PR review routing.
- **Bus factor** — if only 1 person owns this, changes need extra review.

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

## Error handling

If `get_risk` returns a tool error, the MCP server may not be running. Proceed with the modification but note that risk assessment was unavailable.
