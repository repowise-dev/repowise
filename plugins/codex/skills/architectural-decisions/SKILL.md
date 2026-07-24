---
name: architectural-decisions
description: Use when a task asks why code is built a certain way, proposes architectural changes, compares implementation approaches, or mentions decision markers such as WHY, DECISION, TRADEOFF, or ADR in a Repowise-indexed repository.
---

# Architectural Decisions With Repowise

Repowise captures architectural decisions: the rationale behind how code is built.

`get_why` has four modes — pick by what you pass:

1. `get_why(query="why is auth using JWT?")` — keyword + semantic decision search.
2. `get_why(query="src/auth/service.py")` — decisions governing that file, plus
   its origin story and an alignment score (does the file still follow its own ADRs?).
3. `get_why(query="why was caching added?", targets=["src/auth/cache.py"])` —
   target-anchored search; decisions touching the targets get boosted.
4. `get_why()` — the decision-health dashboard.

Decisions are mined from eight sources (ADR files, CHANGELOG, PR/commit bodies,
inline markers, git archaeology, README/docs, code comments, and the doc pass)
and linked by `supersedes` / `refines` / `relates_to` / `conflicts_with` edges,
so a single answer can return a whole lineage chain. When no decision exists for
a path, `get_why` falls back to git archaeology so the call is never empty.

## When The User Asks Why Something Exists

Call `get_why(query="specific area or decision")`.

## Before Architectural Changes

1. Call `get_why(query="the area being changed")` before introducing new patterns, restructuring modules, replacing infrastructure, or choosing between approaches.
2. If decisions are found, summarize the relevant rationale and tradeoffs before editing.
3. If no decision is found, state that no recorded decision governs the area and continue with normal source inspection.

## When No Specific Query Exists

Call `get_why()` to inspect decision health: stale decisions, conflicts, and ungoverned hotspots.

The same signals surface in the CLI via `repowise decision health`, and you can
review auto-proposed decisions with `repowise decision confirm`.

## When Decision Markers Appear In Code

If a file contains `WHY:`, `DECISION:`, `TRADEOFF:`, or `ADR:`, call `get_context(targets=["path/to/file"])` to retrieve the full file context and related decisions.

## Recording New Decisions

When the user makes a new architectural decision, suggest recording it with a `DECISION:` comment in the relevant code or with `repowise decision add`.
