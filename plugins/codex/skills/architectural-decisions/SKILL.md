---
name: architectural-decisions
description: Use when a task asks why code is built a certain way, proposes architectural changes, compares implementation approaches, or mentions decision markers such as WHY, DECISION, TRADEOFF, or ADR in a Repowise-indexed repository.
---

# Architectural Decisions With Repowise

Repowise captures architectural decisions: the rationale behind how code is built.

## When The User Asks Why Something Exists

Call `get_why(query="specific area or decision")` to search captured decisions. This covers inline decision markers, git-derived rationale, and decisions mined from documentation.

## Before Architectural Changes

1. Call `get_why(query="the area being changed")` before introducing new patterns, restructuring modules, replacing infrastructure, or choosing between approaches.
2. If decisions are found, summarize the relevant rationale and tradeoffs before editing.
3. If no decision is found, state that no recorded decision governs the area and continue with normal source inspection.

## When No Specific Query Exists

Call `get_why()` to inspect decision health: stale decisions, conflicts, and ungoverned hotspots.

## When Decision Markers Appear In Code

If a file contains `WHY:`, `DECISION:`, `TRADEOFF:`, or `ADR:`, call `get_context(targets=["path/to/file"])` to retrieve the full file context and related decisions.

## Recording New Decisions

When the user makes a new architectural decision, suggest recording it with a `DECISION:` comment in the relevant code or with `repowise decision add`.
