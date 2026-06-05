---
name: architectural-decisions
description: >
  Use when encountering questions about WHY code is built a certain way, when about to make
  architectural changes (new patterns, restructuring, choosing between approaches), or when
  the user asks about design rationale in a Repowise-indexed codebase (.repowise/ directory exists).
  Also activates when commit messages or code comments contain decision signals like "WHY:",
  "DECISION:", "TRADEOFF:", "ADR:".
user-invocable: false
---

# Architectural Decisions with Repowise

Repowise captures architectural decisions — the *why* behind how code is built.

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

## When the user asks "why is X built this way?"

Call `get_why(query="X")`.

## When about to make an architectural change

1. Call `get_why(query="the specific area you're changing")` to find existing decisions that govern that area.
2. If decisions are found, present them to the user before proceeding — they may not want to contradict an existing architectural choice.
3. If no decisions are found, proceed but note that no recorded decision governs this area.

## When called with no specific query

Call `get_why()` with no arguments to get the decision-health dashboard:
- Stale decisions that may no longer apply
- Proposed decisions awaiting confirmation
- Ungoverned hotspots (high-churn files with no recorded decisions)

The same signals surface in the CLI via `repowise decision health`, and you can
review auto-proposed decisions with `repowise decision confirm`.

## When a file has decision markers

If you see `# WHY:`, `# DECISION:`, `# TRADEOFF:`, or `# ADR:` comments in code, call `get_context(targets=["that_file.py"])` to see the full decision record with context and affected modules.

## Recording new decisions

If the user makes an architectural decision during the conversation, suggest: "Want to record this decision? Add a `# DECISION:` comment in the relevant code, or run `repowise decision add` to capture it formally."
