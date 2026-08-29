"""Versioned identity kernels for performance findings, opportunities, and plans.

An opportunity id is a persisted join key: it is stamped into every raw
finding's ``details_json`` at analyze time and into every performance plan's
``plan_json`` at generation time, and both REST and MCP join on exact string
equality. So the *inputs* to that hash are a contract, not an implementation
detail. This suite pins them, and pins which fields are deliberately outside the
kernel so that adding a fact or editing prose cannot churn identity by accident.

A change to the kernel increments ``PERFORMANCE_MODEL_VERSION`` and moves these
pinned strings in one reviewable diff.
"""

from __future__ import annotations

import copy

import pytest

from repowise.core.analysis.health.perf.opportunities import (
    PERFORMANCE_MODEL_VERSION,
    build_performance_opportunities,
    link_performance_findings,
    opportunity_id_for_finding,
)
from repowise.core.analysis.health.refactoring.performance_fix import (
    performance_fix_suggestions,
)

# The v1 kernel, pinned. A change to either string is a model-version change.
CROSS_FUNCTION_ID = "perf_e358397251b660183fdd"
LOCAL_ID = "perf_f13beed790fd3cb29ac1"


def _row(**overrides):
    row = {
        "id": "storage-uuid-not-a-content-hash",
        "dimension": "performance",
        "biomarker_type": "io_in_loop",
        "file_path": "src/app/a.py",
        "function_name": "run",
        "line_start": 10,
        "line_end": 12,
        "reason": "Database work repeats for every loop iteration.",
        "details": {
            "boundary_kind": "db",
            "cross_function": True,
            "path": ["src/app/a.py::run", "src/app/db.py::get_session"],
            "resolution_basis": "call-site",
        },
    }
    details = overrides.pop("details", None)
    row.update(overrides)
    if details is not None:
        row["details"] = {**row["details"], **details}
    return row


def _local_row(**overrides):
    return _row(
        details={"cross_function": False, "path": [], "resolution_basis": "direct"}, **overrides
    )


def test_the_model_version_is_pinned_and_is_not_itself_a_kernel_input() -> None:
    """v1 ids predate the constant, so the constant stays out of the payload.

    Embedding the version in the hash would rewrite every persisted
    ``opportunity_id`` and orphan every stored plan link for no semantic
    change. A later version may embed it; version 1 must not.
    """
    assert PERFORMANCE_MODEL_VERSION == 1
    assert opportunity_id_for_finding(_row()) == CROSS_FUNCTION_ID
    assert opportunity_id_for_finding(_local_row()) == LOCAL_ID


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason", "Completely different prose about the same loop."),
        ("line_end", 999),
        ("id", "a-different-storage-uuid"),
        ("severity", "high"),
        ("health_impact", -3.5),
    ],
)
def test_display_only_fields_are_outside_the_opportunity_kernel(field, value) -> None:
    assert opportunity_id_for_finding(_row(**{field: value})) == CROSS_FUNCTION_ID
    assert opportunity_id_for_finding(_local_row(**{field: value})) == LOCAL_ID


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rank_score", 99),
        ("rank_factors", {"boundary_kind": 9}),
        ("confidence", "low"),
        ("dataflow_verified", True),
        ("resource_invariant", True),
        ("reliable_entry_reachability", True),
        ("resolution_basis", "name-fallback"),
    ],
)
def test_derived_and_ranking_details_are_outside_the_opportunity_kernel(field, value) -> None:
    """Adding an evidence or ranking fact must never move identity.

    ``resolution_basis`` is the sharp one: it feeds confidence and ranking but
    is deliberately not an identity input, so a call-graph confidence upgrade
    does not renumber opportunities.
    """
    assert opportunity_id_for_finding(_row(details={field: value})) == CROSS_FUNCTION_ID


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        ({"file_path": "tests/test_a.py"}, "execution context"),
        ({"details": {"boundary_kind": "filesystem"}}, "boundary"),
        ({"details": {"path": ["src/app/a.py::run", "src/app/db.py::other"]}}, "terminal sink"),
        ({"biomarker_type": "membership_test_against_list_in_loop"}, "cost shape"),
    ],
)
def test_cross_function_kernel_inputs_each_move_the_identity(mutate, reason) -> None:
    assert opportunity_id_for_finding(_row(**copy.deepcopy(mutate))) != CROSS_FUNCTION_ID, reason


def test_a_new_caller_never_changes_the_cross_function_identity() -> None:
    """The shared cause is the identity; callers are evidence.

    ``file_path``, ``function_name``, and ``line_start`` are kernel inputs for a
    same-function observation and deliberately are not for a cross-function one.
    """
    original = _row()
    other_caller = _row(file_path="src/app/b.py", function_name="other", line_start=77)
    other_caller["details"]["path"] = ["src/app/b.py::other", "src/app/db.py::get_session"]
    assert opportunity_id_for_finding(other_caller) == CROSS_FUNCTION_ID
    assert len(build_performance_opportunities([original, other_caller])) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        {"file_path": "src/app/b.py"},
        {"function_name": "other"},
        {"line_start": 11},
        {"biomarker_type": "nested_loop_with_io"},
    ],
)
def test_local_kernel_inputs_each_move_the_identity(mutate) -> None:
    assert opportunity_id_for_finding(_local_row(**mutate)) != LOCAL_ID


def test_the_cost_shape_merge_applies_to_cross_function_identity_only() -> None:
    """``io_in_loop`` and ``nested_loop_with_io`` share one cross-function cause.

    The local kernel keeps the literal marker. The asymmetry is deliberate and
    load-bearing: a split that unified the two branches would regroup the whole
    repository.
    """
    nested = _row(biomarker_type="nested_loop_with_io")
    assert opportunity_id_for_finding(nested) == CROSS_FUNCTION_ID
    assert opportunity_id_for_finding(_local_row(biomarker_type="nested_loop_with_io")) != LOCAL_ID


@pytest.mark.parametrize("path", [[], [5], [None]])
def test_a_cross_function_flag_without_a_usable_path_falls_back_to_the_local_kernel(path) -> None:
    """The flag alone does not make an observation cross-function.

    Only path nodes that survive the string filter can name a terminal sink, so
    a truthy ``cross_function`` with nothing usable in ``path`` is keyed by its
    own location. The corpus derives the flag from the path and cannot reach
    this branch, so it is pinned here.
    """
    row = _row(details={"path": path})
    assert opportunity_id_for_finding(row) == LOCAL_ID


def _shared_helper_rows():
    rows = [_row(), _row(function_name="also", line_start=40)]
    for row in rows:
        row["details"]["path"] = [
            "src/app/a.py::" + str(row["function_name"]),
            "src/app/repo.py::fetch",
            "src/app/db.py::execute",
        ]
    return rows


def test_the_plan_kernel_is_the_opportunity_id_in_plan_json() -> None:
    """One plan addresses exactly one opportunity, joined on the id string."""
    opportunity = build_performance_opportunities(_shared_helper_rows())[0]
    plan = performance_fix_suggestions([opportunity])[0]
    assert plan.plan["opportunity_id"] == opportunity.opportunity_id


def test_the_plan_id_is_absent_from_plan_evidence_today() -> None:
    """Characterization of a live linkage hazard, not an endorsement.

    ``performance_fix`` writes ``opportunity_id`` into ``plan`` only. The REST
    route reads ``plan_json`` and links correctly; ``tool_health`` reads
    ``suggestion.evidence`` and therefore never links, so
    ``recommendation_lede.performance_plan_id`` is always ``None`` on a real
    index. Pinned here so the divergence stays visible until it is fixed.
    """
    plan = performance_fix_suggestions(build_performance_opportunities(_shared_helper_rows()))[0]
    assert "opportunity_id" in plan.plan
    assert "opportunity_id" not in plan.evidence


def test_a_performance_finding_has_no_content_derived_public_reference() -> None:
    """The only stable public key on a performance finding is its group id.

    ``evidence[].finding_id`` is the storage row id: empty in memory, a random
    UUID once persisted. Nothing joins on it across a reindex, so it is not
    an identity.
    """
    in_memory = _row()
    del in_memory["id"]
    opportunity = build_performance_opportunities([in_memory])[0]
    assert opportunity.evidence[0]["finding_id"] == ""

    persisted = _row(id="8f14e45fceea167a5a36dedd4bea2543")
    assert build_performance_opportunities([persisted])[0].evidence[0]["finding_id"] == (
        "8f14e45fceea167a5a36dedd4bea2543"
    )
    link_performance_findings([persisted])
    assert persisted["details"]["opportunity_id"] == CROSS_FUNCTION_ID
