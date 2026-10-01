"""The reader-facing label for a biomarker id, as the web glossary words it.

A copy of the labels in ``packages/ui/src/health/biomarker-glossary.ts``; that
file stays the source (it also carries categories and descriptions core does not
need). ``test_wire_vocabulary_parity`` fails when the two disagree.
"""

from __future__ import annotations

BIOMARKER_LABELS: dict[str, str] = {
    "brain_method": "Brain method",
    "nested_complexity": "Nested complexity",
    "bumpy_road": "Bumpy road",
    "complex_method": "Complex method",
    "large_method": "Large method",
    "primitive_obsession": "Long parameter list",
    "dry_violation": "DRY violation",
    "untested_hotspot": "Untested hotspot",
    "coverage_gap": "Coverage gap",
    "coverage_gradient": "Coverage gradient",
    "developer_congestion": "Developer congestion",
    "knowledge_loss": "Knowledge loss",
    "hidden_coupling": "Hidden coupling",
    "complex_conditional": "Complex conditional",
    "function_hotspot": "Function hotspot",
    "code_age_volatility": "Code age volatility",
    "low_cohesion": "Low cohesion",
    "god_class": "God class",
    "ownership_risk": "Ownership risk",
    "churn_risk": "Churn risk",
    "change_entropy": "Change entropy",
    "co_change_scatter": "Co-change scatter",
    "prior_defect": "Prior defects",
    "large_assertion_block": "Large assertion block",
    "assertion_free_test": "Assertion free test",
    "mock_saturated_test": "Mock saturated test",
    "duplicated_assertion_block": "Duplicated assertions",
    "error_handling": "Error handling",
    "ungoverned_hotspot": "Ungoverned hotspot",
    "stale_governance": "Stale governance",
    "contradictory_decision": "Contradictory decision",
    "io_in_loop": "I/O in loop",
    "string_concat_in_loop": "String concat in loop",
    "blocking_sync_in_async": "Blocking call in async",
    "regex_compile_in_loop": "Regex compiled in loop",
    "defer_in_loop": "Defer in loop",
    "resource_construction_in_loop": "Resource built in loop",
    "lock_in_loop": "Lock in loop",
    "serial_await_in_loop": "Serial await in loop",
    "unbounded_read_reduced_in_memory": "Unbounded read reduced in memory",
    "lazy_load_in_loop": "Lazy load in loop",
    "membership_test_against_list_in_loop": "List membership in loop",
    "nested_loop_with_io": "I/O in nested loop",
    "hot_path_sync_io": "Blocking I/O on a hot path",
    "blocking_io_under_lock": "Blocking I/O under a lock",
    "nested_loop_quadratic": "Quadratic nested loop",
    "sql_high_complexity": "Complex SQL routine",
    "sql_select_star": "SELECT * in a view",
    "sql_update_delete_without_where": "UPDATE/DELETE without WHERE",
    "sql_cartesian_join": "Cartesian join",
}


def biomarker_label(name: str) -> str:
    """The glossary label; an id it does not know reads with spaces for underscores."""
    return BIOMARKER_LABELS.get(name) or name.replace("_", " ")
