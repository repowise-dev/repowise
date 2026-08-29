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

from repowise.core.analysis.health.finding_identity import finding_public_id
from repowise.core.analysis.health.perf.opportunities import (
    PERFORMANCE_MODEL_VERSION,
    build_performance_opportunities,
    link_performance_findings,
    model_state,
    opportunity_id_for_finding,
)
from repowise.core.analysis.health.refactoring.performance_fix import (
    performance_fix_suggestions,
)

# The v2 kernel, pinned. A change to either string is a model-version change.
CROSS_FUNCTION_ID = "perf2_5662c38bc84c9a677164"
LOCAL_ID = "perf2_f13beed790fd3cb29ac1"


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


def test_the_model_version_is_pinned_and_is_carried_by_the_id_prefix() -> None:
    """The version is the prefix, not a hash input.

    Putting it in the prefix costs the hash nothing and buys the one thing an
    alias table cannot: an id says which model minted it, so a caller holding a
    stale one is told to refresh instead of being handed the wrong group.
    """
    assert PERFORMANCE_MODEL_VERSION == 2
    assert opportunity_id_for_finding(_row()) == CROSS_FUNCTION_ID
    assert opportunity_id_for_finding(_local_row()) == LOCAL_ID
    assert CROSS_FUNCTION_ID.startswith(f"perf{PERFORMANCE_MODEL_VERSION}_")


@pytest.mark.parametrize(
    ("opportunity_id", "state", "version"),
    [
        (CROSS_FUNCTION_ID, "current", 2),
        ("perf_e358397251b660183fdd", "stale_model", 1),
        ("perf9_e358397251b660183fdd", "stale_model", 9),
        ("not-an-opportunity-id", "unrecognized", None),
    ],
)
def test_an_id_reports_its_own_model_and_whether_it_still_resolves(
    opportunity_id, state, version
) -> None:
    """Ids are never translated across models.

    Grouping decides membership, so one v1 id can name observations v2 splits
    several ways. An alias would have to guess which split the caller meant, so
    the mismatch is reported instead.
    """
    result = model_state(opportunity_id)
    assert result["state"] == state
    assert result["requested_model_version"] == version
    assert result["performance_model_version"] == PERFORMANCE_MODEL_VERSION
    assert result["refresh_required"] is (state == "stale_model")


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
        (
            {"details": {"path": ["src/app/a.py::other", "src/app/db.py::get_session"]}},
            "meaningful predecessor",
        ),
        ({"biomarker_type": "membership_test_against_list_in_loop"}, "cost shape"),
    ],
)
def test_cross_function_kernel_inputs_each_move_the_identity(mutate, reason) -> None:
    assert opportunity_id_for_finding(_row(**copy.deepcopy(mutate))) != CROSS_FUNCTION_ID, reason


def test_a_new_call_site_behind_the_same_caller_never_changes_the_identity() -> None:
    """The shared cause is the identity; individual call sites are evidence.

    ``file_path``, ``function_name``, and ``line_start`` are kernel inputs for a
    same-function observation and deliberately are not for a cross-function one:
    a second loop in the same caller adds evidence, not a cause.
    """
    original = _row()
    same_caller = _row(line_start=77, line_end=79)
    assert opportunity_id_for_finding(same_caller) == CROSS_FUNCTION_ID
    assert len(build_performance_opportunities([original, same_caller])) == 1


def test_an_unrelated_caller_on_the_same_sink_is_a_different_cause() -> None:
    """A shared destination is not a shared cause.

    Naming a group by its sink alone merged every workflow that happened to
    open a session. Requiring the caller to match splits those, while callers
    that reach the sink through one helper stay together.
    """
    original = _row()
    unrelated = _row(file_path="src/app/b.py", function_name="other", line_start=77)
    unrelated["details"]["path"] = ["src/app/b.py::other", "src/app/db.py::get_session"]
    assert opportunity_id_for_finding(unrelated) != CROSS_FUNCTION_ID
    assert len(build_performance_opportunities([original, unrelated])) == 2

    through_helper = [_row(), _row(file_path="src/app/b.py", function_name="other")]
    for row in through_helper:
        row["details"]["path"] = [
            f"{row['file_path']}::{row['function_name']}",
            "src/app/repo.py::fetch",
            "src/app/db.py::get_session",
        ]
    merged = build_performance_opportunities(through_helper)
    assert len(merged) == 1
    assert merged[0].intervention_symbol == "src/app/repo.py::fetch"


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


@pytest.mark.parametrize(
    "path", [[], [5], [None], [""], ["", "src/app/db.py::get_session"], ["src/app/db.py::get_session"]]
)
def test_a_cross_function_flag_without_a_named_caller_falls_back_to_the_local_kernel(
    path,
) -> None:
    """The flag alone does not make an observation cross-function.

    A cause needs both ends: a sink that pays the cost and a caller that
    repeats it. A path with nothing usable in it, or with only the sink, names
    one end, so the observation is keyed by its own location instead.
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


def test_evidence_is_addressed_by_a_content_derived_public_reference() -> None:
    """Evidence carries the finding's public id, never a storage row id.

    The row id is republished on every analysis, so a caller who quoted one
    back was quoting a value that no longer existed. The public id is derived
    from the finding's own coordinates, so it survives the reindex and is the
    same string before and after persistence.
    """
    in_memory = _row()
    del in_memory["id"]
    reference = build_performance_opportunities([in_memory])[0].evidence[0]["finding_id"]
    assert reference == finding_public_id(in_memory)

    persisted = _row(id="8f14e45fceea167a5a36dedd4bea2543")
    assert build_performance_opportunities([persisted])[0].evidence[0]["finding_id"] == reference

    stored = _row(id="8f14e45fceea167a5a36dedd4bea2543", public_id="finding_already_stored")
    assert build_performance_opportunities([stored])[0].evidence[0]["finding_id"] == (
        "finding_already_stored"
    )
    link_performance_findings([persisted])
    assert persisted["details"]["opportunity_id"] == CROSS_FUNCTION_ID


def test_the_public_finding_id_ignores_prose_and_derived_details() -> None:
    """Rewording a detector, or a later model changing its mind, moves nothing.

    The id is stored on the row and quoted by agents, so anything that can
    change without the finding moving has to stay out of its kernel.
    """
    base = _row()
    assert finding_public_id(base) == finding_public_id(_row(reason="reworded entirely"))
    assert finding_public_id(base) == finding_public_id(
        _row(details={**base["details"], "opportunity_id": "perf9_deadbeef"})
    )
    assert finding_public_id(base) == finding_public_id(
        _row(details={**base["details"], "reliable_entry_reachability": True})
    )
    # Structural coordinates are in the kernel, so a real move is a new id.
    assert finding_public_id(base) != finding_public_id(_row(line_start=999))


def test_a_path_node_that_names_nothing_never_becomes_an_intervention_symbol() -> None:
    """An empty node is absence, not a symbol called \"\"."""
    row = _row(details={"path": ["", "src/app/db.py::get_session"]})
    opportunity = build_performance_opportunities([row])[0]
    assert opportunity.intervention_symbol is None
    assert opportunity.terminal_sink is None
    assert opportunity.opportunity_id == LOCAL_ID
