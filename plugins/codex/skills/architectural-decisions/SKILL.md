---
name: architectural-decisions
description: Use when a task asks why code is built a certain way, proposes architectural changes, compares implementation approaches, or mentions decision markers such as WHY, DECISION, TRADEOFF, or ADR in a Repowise-indexed repository.
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

Decisions are mined from five sources (ADR files, PR and squash-commit bodies,
inline markers, git archaeology, and centrality-bounded code comments). Each
rationale traces to a verbatim source span, stamped exact / fuzzy / unverified.
When no decision exists for a path, `get_why` falls back to git archaeology so
the call is never empty.

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

The same signals surface in the CLI via `repowise decision health` /
`/prompts:repowise-decision`, and you can query *why* mid-task with `repowise why` /
`/prompts:repowise-why` (the `get_why` adapter). `repowise decision candidates --format json`
lists proposed decisions with what each still needs; `repowise decision confirm <id>`
accepts one (see `/prompts:repowise-decision`).

## When a file has decision markers

If you see `# WHY:`, `# DECISION:`, `# TRADEOFF:`, or `# ADR:` comments in code, call `get_context(targets=["that_file.py"])` to see the full decision record with context and affected modules.

## Recording new decisions

When you settle an architectural choice yourself (you weighed options and picked one), record it:

`repowise decision add --agent <your slug> --title "..." --decision "..." --rationale "<why, and what you rejected>" --affects <path>`

Add `--evidence-commit <sha>` once it is committed. The record lands as a candidate: it never governs until accepted, so recording is safe.

## Reviewing candidates

You may review and accept candidates yourself. `repowise decision candidates --format json` lists each with its `blockers`. Accept one with `repowise decision confirm <id> --agent <your slug>`, adding `--reason`, `--scope <path>`, `--evidence <commit|file|link>` or `--decision` for whatever a blocker names. Dismiss a wrong one with `repowise decision dismiss <id> --agent <your slug> --reason ... --yes`, fold a duplicate with `repowise decision merge <id> <into-id> --agent <your slug>` (the target must already be accepted; confirm it first), and retire one with `repowise decision deprecate <id> --agent <your slug> --reason ...`. When an accepted decision is `needs_review` and still holds, renew it with `repowise decision reaffirm <id> --agent <your slug>`; `confirm` is for candidates only. With `--format json` every review command returns `results[]`, even for one id. Your acceptances are recorded as yours, never a person's. If the repository turned agent acceptance off, the confirm is refused; leave it for a person and mention it.

If the user states a decision, offer to record it the same way without `--agent`.
