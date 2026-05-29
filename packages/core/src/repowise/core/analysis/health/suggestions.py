"""Deterministic refactoring suggestions for biomarker findings.

Zero LLM calls — every suggestion is a static, rule-based template
keyed on biomarker type and severity. Used by:

  * ``get_health(include=["refactoring"])`` — attaches a ``suggestion``
    field to each finding in the response.
  * The dashboard's ``RefactoringCard`` — same text, no separate
    server round-trip needed.

Why this lives in the core (not in the MCP layer): suggestion text is
part of the health domain model, not the wire format. Keeping it here
means the CLI (``repowise health --refactoring-targets``) and the MCP
tool share one canonical source of truth.
"""

from __future__ import annotations

from typing import Any

_TEMPLATES: dict[str, str] = {
    "brain_method": (
        "Split this function. It carries high cyclomatic complexity and "
        "many dependents — extract cohesive responsibilities into helpers "
        "so each call site sees a smaller surface area."
    ),
    "low_cohesion": (
        "Split this class along its cohesion seams. Its methods form "
        "groups that share no fields or calls — each group is a smaller, "
        "single-responsibility class waiting to be extracted. Start by "
        "moving one disconnected method cluster (and the fields only it "
        "touches) into its own type."
    ),
    "god_class": (
        "Break up this god class. It is large, has many methods, and "
        "concentrates real logic in a brain method — extract cohesive "
        "responsibilities into collaborators so no single class owns the "
        "whole subsystem. Pair the split with characterization tests."
    ),
    "nested_complexity": (
        "Flatten the control flow. Pull early-return guards to the top, "
        "extract the deepest branch into a helper, and consider "
        "replacing nested conditionals with a strategy table or "
        "dispatch dict."
    ),
    "complex_method": (
        "Reduce cyclomatic complexity. Decompose the function along its "
        "conditional axes; if it dispatches on a type tag, replace the "
        "if/elif ladder with polymorphism or a lookup table."
    ),
    "bumpy_road": (
        "Smooth the road. Multiple branches at the same nesting depth "
        "usually means the function is doing several jobs — split it "
        "into stages so each stage stays at a single level of "
        "abstraction."
    ),
    "large_method": (
        "Shorten the function. Extract paragraphs (each comment block "
        "is usually a candidate) into named helpers; aim for a body "
        "that reads as a high-level outline of intent."
    ),
    "primitive_obsession": (
        "Introduce a parameter object. Group the related primitives "
        "passed in here into a dataclass so the type names tell the "
        "story and adding another field doesn't break every caller."
    ),
    "dry_violation": (
        "De-duplicate the clone. Extract the shared block into a "
        "private helper, or push it down to a base class if the "
        "structure is genuinely shared rather than coincidental."
    ),
    "untested_hotspot": (
        "Write tests before refactoring. This file is high-churn and "
        "high-dependents but lacks coverage — add characterization "
        "tests for the current behavior first, then refactor with "
        "confidence."
    ),
    "coverage_gap": (
        "Cover the uncovered branches. Start with the highest-impact "
        "uncovered code paths (errors, edge cases, security-sensitive "
        "branches) rather than just chasing the percentage."
    ),
    "developer_congestion": (
        "Cool the contention. Too many authors are touching this file "
        "at once — clarify ownership, or split the file along its "
        "natural seams so contributors don't collide."
    ),
    "hidden_coupling": (
        "Surface the hidden dependency. This file co-changes with a "
        "sibling that it doesn't import — promote the implicit contract "
        "into a shared module, type, or interface so the coupling is "
        "visible at the source level instead of hidden in commit history."
    ),
    "complex_conditional": (
        "Decompose the boolean expression. Extract sub-clauses into "
        "named predicates that explain *what* each branch checks; "
        "compound conditions of three or more operators are usually "
        "two policies fighting for one line."
    ),
    "function_hotspot": (
        "Refactor this specific function — it's where the file's churn "
        "concentrates. Split its responsibilities so future commits land "
        "on smaller, more focused units; pair the refactor with "
        "characterization tests if coverage is thin."
    ),
    "code_age_volatility": (
        "Slow down before editing more. This function has sat largely "
        "untouched for over a year and is suddenly being modified — that "
        "edit profile is one of the strongest defect predictors. Pull in "
        "the original author or write down the design intent before "
        "shipping the next change."
    ),
    "ownership_risk": (
        "Assign a clear owner and reduce drive-by contributors on this "
        "file. Fragmented ownership — many authors each touching a small "
        "slice — is one of the strongest defect predictors; nominate a "
        "DRI for reviews, or split the file along its natural seams so "
        "each part has a coherent owner."
    ),
    "churn_risk": (
        "This file is being rewritten faster than its size. Stabilize "
        "the interface and add characterization tests before the next "
        "change — high relative churn means the design hasn't settled, "
        "so lock in current behavior and slow the rate of structural "
        "edits."
    ),
    "change_entropy": (
        "Calm this file's change history. Its modifications arrive in wide, "
        "scattered commits — a strong history-based fault predictor. Land "
        "future edits in focused, single-purpose commits, and consider "
        "splitting the file so unrelated changes stop landing together."
    ),
    "co_change_scatter": (
        "Reduce the blast radius. This file co-changes with many others, so "
        "every edit ripples across the codebase. Tighten its interface, move "
        "shared concerns behind a stable boundary, or split it so callers "
        "depend on smaller, more cohesive units."
    ),
    "large_assertion_block": (
        "Split this test. A long run of assertions in one case tests "
        "several behaviours at once — when it fails it names a line, not a "
        "cause. Break it into focused cases (one behaviour each), or factor "
        "shared setup into a fixture so each assertion's intent is clear."
    ),
    "duplicated_assertion_block": (
        "De-duplicate this assertion block. The same checks are copy-pasted "
        "across tests, so a behaviour change must be edited in several "
        "places and usually isn't. Extract the shared assertions into a "
        "helper or parametrize the cases over the varying inputs."
    ),
    "knowledge_loss": (
        "Document the surviving knowledge. The primary author(s) of "
        "this file are no longer active — pair-program with someone "
        "still on the team or write down the design intent before "
        "the next change."
    ),
    "ungoverned_hotspot": (
        "This churn hotspot has no governing decision — capture an ADR "
        "with `repowise decision add` so future changes have a rationale "
        "to check against."
    ),
    "stale_governance": (
        "The architectural decision governing this file has gone stale: "
        "the code has changed but the decision hasn't been reviewed. "
        "Update or supersede the decision via `repowise decision edit` "
        "so the rationale reflects the current implementation."
    ),
    "contradictory_decision": (
        "Two active decisions make contradictory claims that affect this "
        "file. Resolve the conflict by superseding one with the other "
        "(`repowise decision supersede`) or by adding a 'relates_to' edge "
        "with a clarifying rationale."
    ),
}


def suggestion_for(biomarker_type: str) -> str:
    """Return the canonical refactoring suggestion for a biomarker.

    Unknown biomarker names fall back to a generic prompt — keeps the
    field non-null on every finding so the UI can rely on it.
    """
    return _TEMPLATES.get(
        biomarker_type,
        "Review this finding and decide whether the underlying smell is "
        "worth refactoring or suppressing via `.repowise/health-rules.json`.",
    )


def annotate_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Return *finding* with a ``suggestion`` field added (immutable copy)."""
    biomarker = str(finding.get("biomarker_type", ""))
    return {**finding, "suggestion": suggestion_for(biomarker)}
