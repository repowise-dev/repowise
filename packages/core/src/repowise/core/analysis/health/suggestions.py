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
    "knowledge_loss": (
        "Document the surviving knowledge. The primary author(s) of "
        "this file are no longer active — pair-program with someone "
        "still on the team or write down the design intent before "
        "the next change."
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
