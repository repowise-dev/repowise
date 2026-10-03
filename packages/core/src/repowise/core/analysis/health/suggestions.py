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
    "coverage_gradient": (
        "Raise this file's line coverage. The uncovered fraction tracks "
        "defect risk directly — add tests for the untested paths, prioritising "
        "error handling and edge cases, and the deduction shrinks in step with "
        "the coverage you recover."
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
    "prior_defect": (
        "This file has been bug-fixed repeatedly in recent months — defects "
        "cluster, so it is among the likeliest places the next bug will land. "
        "Treat it as fragile: add regression tests around the areas that keep "
        "breaking, harden input handling, and review changes here with extra "
        "care before they ship."
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
    "error_handling": (
        "Handle or propagate the error. A swallowed exception, catch-all "
        "except, unguarded unwrap, or discarded Go error turns failures "
        "into silent misbehavior — log and re-raise, narrow the caught "
        "type, replace unwrap with the ? operator or a match, or check "
        "the error you are currently discarding."
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
    # Test quality, advisory.
    "assertion_free_test": (
        "Assert on the result. This test runs the code and checks nothing, so it "
        "passes whatever the code does; add an assertion on the value or the side "
        "effect the test exists to protect."
    ),
    "mock_saturated_test": (
        "Test behaviour, not wiring. Replace mocks of the code's own collaborators "
        "with real objects or a fake, and keep mocks for the slow or external "
        "boundaries."
    ),
    # Performance: per-iteration work.
    "io_in_loop": (
        "Batch the I/O. Fetch or write everything the loop needs in one call "
        "before or after it (a bulk query, an IN clause, a batch request) and "
        "look the results up in memory inside the loop."
    ),
    "nested_loop_with_io": (
        "Lift the I/O out of the inner loop. Batch the inner call across the "
        "outer loop's items, or load the data once and index it by key."
    ),
    "lazy_load_in_loop": (
        "Load the relationship with the parent rows. Add selectinload or "
        "joinedload (prefetch_related or select_related in Django) to the query "
        "the loop iterates."
    ),
    "serial_await_in_loop": (
        "Run independent awaits together. When the iterations do not depend on "
        "each other, gather them (asyncio.gather, Promise.all, Task.WhenAll) "
        "with a bound on concurrency."
    ),
    "string_concat_in_loop": (
        "Build the string once. Collect the pieces in a list or buffer inside the "
        "loop and join them after it."
    ),
    "regex_compile_in_loop": (
        "Compile the pattern once. Move the compile call out of the loop into a "
        "constant or a field and reuse it."
    ),
    "resource_construction_in_loop": (
        "Create the client once. Construct the connection or client before the "
        "loop and reuse it for every iteration."
    ),
    "lock_in_loop": (
        "Take the lock once. Acquire it around the whole loop, or collect the "
        "changes and apply them in one critical section."
    ),
    "defer_in_loop": (
        "Release the resource each iteration. Close it at the end of the loop "
        "body, or move the body into its own function so the defer runs per "
        "iteration."
    ),
    "json_parse_in_loop": (
        "Parse once. Hoist the parse or stringify out of the loop, and replace a "
        "JSON round-trip clone with structuredClone."
    ),
    "membership_test_against_list_in_loop": (
        "Look up in a set. Build a set from the list once before the loop and "
        "test membership against it."
    ),
    "list_insert_zero_in_loop": (
        "Stop inserting at the front. Use collections.deque with appendleft, or "
        "append and reverse once after the loop."
    ),
    "pandas_iterrows_in_loop": (
        "Vectorize the loop. Express the per-row work as a column operation, or "
        "use itertuples when row access is unavoidable."
    ),
    "pd_concat_in_loop": (
        "Concatenate once. Append each frame to a list inside the loop and call "
        "pd.concat on the list after it."
    ),
    "array_spread_in_reduce": (
        "Mutate the accumulator. Push into it and return it, so each step does "
        "not copy everything gathered so far."
    ),
    "goroutine_in_unbounded_loop": (
        "Bound the fan-out. Use a worker pool, a semaphore channel or an errgroup "
        "with a limit so the goroutine count does not grow with the input."
    ),
    "nested_loop_quadratic": (
        "Remove the inner scan. If the inner loop searches for a match, index the "
        "inner collection by key in a map or set once."
    ),
    # Performance: blocking work.
    "blocking_sync_in_async": (
        "Use the async form of the call (asyncio.sleep, an async HTTP client, "
        "asyncio subprocess), or run the blocking call in a thread executor."
    ),
    "hot_path_sync_io": (
        "Take the I/O off the hot path. Cache the result, move the call to "
        "startup, or make it asynchronous."
    ),
    "blocking_io_under_lock": (
        "Do the I/O outside the lock. Read or write first, then take the lock "
        "only to update the shared state."
    ),
    "unbounded_read_reduced_in_memory": (
        "Select in the query. Move the per-key reduction into SQL (DISTINCT ON, "
        "a window function, a view) so only the rows you keep are read."
    ),
    # SQL.
    "sql_cartesian_join": (
        "Add the join predicate. Rewrite the comma join as JOIN ... ON the "
        "columns that relate the tables, or as CROSS JOIN if the product is "
        "intended."
    ),
    "sql_high_complexity": (
        "Split the routine. Move branching business logic into the application "
        "or into smaller routines, each with one job."
    ),
    "sql_select_star": (
        "Name the columns. Replace * with the column list the view's consumers "
        "read, so a new source column does not change the view's shape."
    ),
    "sql_update_delete_without_where": (
        "Confirm the statement should touch every row. Add the WHERE clause it "
        "needs, or a comment stating that the full-table change is intended."
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
