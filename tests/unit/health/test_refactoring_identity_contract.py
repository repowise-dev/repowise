"""The refactoring identity kernel is a contract, pinned by hash.

Readers join plans on exact string equality and dismissals are recorded against
the id, so what enters the kernel cannot drift silently. These tests pin two
real ids and then prove, field by field, which inputs move an id and which
cannot. A failure here is either a bug or a deliberate
``REFACTORING_MODEL_VERSION`` bump — never something to regenerate.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.refactoring.identity import (
    REFACTORING_MODEL_VERSION,
    assign_public_ids,
    model_state,
    public_id_model_version,
    refactoring_public_id,
)

EXTRACT_METHOD_ID = "refac2_f98f466eecc2dd332485"
CLONE_ID = "refac2_e0d9feee45201e014bc3"


def _extract_method(**overrides):
    base = dict(
        refactoring_type="extract_method",
        file_path="packages/core/src/repowise/core/pipeline/persist.py",
        target_symbol="persist_analysis",
        line_start=100,
        line_end=118,
        plan={
            "span": {"start": 100, "end": 118},
            "params": ["session", "report"],
            "returns": ["written"],
            "suggested_name": "compute_written",
        },
        evidence={"slice_nloc": 18, "ccn_removed": 4},
        impact_delta=1.5,
        effort_bucket="S",
        blast_radius={"scope": "local"},
        confidence="high",
        source_biomarker="complex_method",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _clone(**overrides):
    base = dict(
        refactoring_type="extract_helper",
        file_path="packages/core/src/repowise/core/a.py",
        target_symbol="a.py:10-24",
        line_start=10,
        line_end=24,
        plan={
            "occurrences": [
                {"file": "packages/core/src/repowise/core/a.py", "line_start": 10, "line_end": 24},
                {"file": "packages/core/src/repowise/core/b.py", "line_start": 55, "line_end": 69},
            ],
            "suggested_site": {"directory": "packages/core/src/repowise/core"},
            "duplicated_lines": 15,
            "snippet": "value = compute()\nreturn value\n",
            "snippet_start_line": 10,
            "snippet_truncated": False,
            "suggested_name": None,
        },
        evidence={
            "occurrence_count": 2,
            "duplicated_lines": 15,
            "token_count": 320,
            "co_change_count": 3,
            "is_intra_file": False,
        },
        impact_delta=0.0,
        effort_bucket="S",
        blast_radius={"files": ["b.py"], "file_count": 1, "co_change_count": 3},
        confidence="medium",
        source_biomarker="dry_violation",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_the_model_version_is_pinned_and_is_carried_by_the_id_prefix() -> None:
    assert REFACTORING_MODEL_VERSION == 2
    assert refactoring_public_id(_extract_method()) == EXTRACT_METHOD_ID
    assert refactoring_public_id(_clone()) == CLONE_ID
    assert EXTRACT_METHOD_ID.startswith(f"refac{REFACTORING_MODEL_VERSION}_")
    assert public_id_model_version(EXTRACT_METHOD_ID) == REFACTORING_MODEL_VERSION


@pytest.mark.parametrize(
    ("public_id", "expected"),
    [
        (EXTRACT_METHOD_ID, "current"),
        ("refac_" + "0" * 20, "stale_model"),
        ("refac1_" + "0" * 20, "stale_model"),
        ("plan_" + "0" * 20, "unrecognized"),
        ("", "unrecognized"),
    ],
)
def test_model_state_classifies_an_id_from_the_string_alone(public_id, expected) -> None:
    state = model_state(public_id)
    assert state["state"] == expected
    assert state["refresh_required"] is (expected == "stale_model")


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"impact_delta": 9.9}, id="recovered-health-is-rescored-every-run"),
        pytest.param({"effort_bucket": "XL"}, id="effort-follows-the-file-size"),
        pytest.param({"confidence": "medium"}, id="confidence-is-re-bucketed"),
        pytest.param({"blast_radius": {}}, id="blast-radius-is-a-measurement"),
        pytest.param({"evidence": {"slice_nloc": 2, "ccn_removed": 1}}, id="evidence-is-display"),
        pytest.param({"line_start": 400, "line_end": 418}, id="the-whole-span-moved-down"),
    ],
)
def test_display_and_derived_fields_cannot_move_an_extract_method_id(overrides) -> None:
    assert refactoring_public_id(_extract_method(**overrides)) == EXTRACT_METHOD_ID


def test_a_renamed_helper_suggestion_does_not_move_the_extract_method_id() -> None:
    plan = dict(_extract_method().plan, suggested_name="compute_something_else")
    assert refactoring_public_id(_extract_method(plan=plan)) == EXTRACT_METHOD_ID


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"file_path": "packages/core/src/repowise/core/pipeline/other.py"}, "another file"),
        ({"target_symbol": "persist_metrics"}, "another host function"),
        ({"source_biomarker": "large_method"}, "another diagnosis"),
        ({"line_end": 130}, "a longer span is a different extraction"),
    ],
)
def test_each_extract_method_kernel_input_moves_the_id(overrides, reason) -> None:
    assert refactoring_public_id(_extract_method(**overrides)) != EXTRACT_METHOD_ID, reason


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("params", ["session"], "a different signature is a different extraction"),
        ("returns", [], "returning nothing is a different extraction"),
    ],
)
def test_the_lifted_signature_is_part_of_the_extract_method_kernel(key, value, reason) -> None:
    plan = dict(_extract_method().plan, **{key: value})
    assert refactoring_public_id(_extract_method(plan=plan)) != EXTRACT_METHOD_ID, reason


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"file_path": "packages/core/src/repowise/core/b.py"}, id="anchor-moved"),
        pytest.param({"target_symbol": "b.py:55-69"}, id="anchor-label-moved"),
        pytest.param({"line_start": 900, "line_end": 914}, id="anchor-lines-moved"),
        pytest.param({"confidence": "high"}, id="confidence"),
    ],
)
def test_the_clone_id_survives_its_anchor_moving(overrides) -> None:
    """The anchor hops to the smallest surviving site; the group is the identity."""
    assert refactoring_public_id(_clone(**overrides)) == CLONE_ID


def test_the_clone_id_ignores_trailing_whitespace_in_the_duplicated_block() -> None:
    plan = dict(_clone().plan, snippet="value = compute()   \nreturn value\n")
    assert refactoring_public_id(_clone(plan=plan)) == CLONE_ID


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("snippet", "value = compute_other()\nreturn value\n", "a different block"),
        ("duplicated_lines", 40, "a different amount of duplication"),
        (
            "occurrences",
            [
                {"file": "packages/core/src/repowise/core/a.py", "line_start": 10, "line_end": 24},
                {"file": "packages/core/src/repowise/core/c.py", "line_start": 55, "line_end": 69},
            ],
            "a different set of sites",
        ),
    ],
)
def test_each_clone_kernel_input_moves_the_id(key, value, reason) -> None:
    plan = dict(_clone().plan, **{key: value})
    assert refactoring_public_id(_clone(plan=plan)) != CLONE_ID, reason


def test_a_third_occurrence_makes_it_a_different_clone_group() -> None:
    plan = dict(_clone().plan)
    plan["occurrences"] = [
        *plan["occurrences"],
        {"file": "packages/core/src/repowise/core/c.py", "line_start": 5, "line_end": 19},
    ]
    assert refactoring_public_id(_clone(plan=plan)) != CLONE_ID


def test_a_performance_plan_takes_its_identity_from_the_causal_id() -> None:
    """Deriving a second id here would let the two disagree about one row."""
    plan = {"opportunity_id": "perf2_abc", "strategy": "batch", "safety": "review"}
    first = SimpleNamespace(
        refactoring_type="performance_fix",
        file_path="a.py",
        target_symbol="a.py::f",
        line_start=1,
        line_end=2,
        plan=plan,
        evidence={},
        impact_delta=0.0,
        effort_bucket="S",
        blast_radius={},
        confidence="high",
        source_biomarker="io_in_loop",
    )
    moved = SimpleNamespace(**{**vars(first), "file_path": "b.py", "line_start": 90, "line_end": 95})
    assert refactoring_public_id(first) == refactoring_public_id(moved)
    other = SimpleNamespace(**{**vars(first), "plan": {**plan, "opportunity_id": "perf2_def"}})
    assert refactoring_public_id(other) != refactoring_public_id(first)


def test_a_persisted_row_and_the_dataclass_reach_the_same_id() -> None:
    """The three shapes that describe one plan must not disagree about its name."""
    import json

    suggestion = _extract_method()
    row = SimpleNamespace(
        refactoring_type=suggestion.refactoring_type,
        file_path=suggestion.file_path,
        target_symbol=suggestion.target_symbol,
        line_start=suggestion.line_start,
        line_end=suggestion.line_end,
        plan_json=json.dumps(suggestion.plan),
        evidence_json=json.dumps(suggestion.evidence),
        source_biomarker=suggestion.source_biomarker,
    )
    as_dict = {
        "refactoring_type": suggestion.refactoring_type,
        "file_path": suggestion.file_path,
        "target_symbol": suggestion.target_symbol,
        "line_start": suggestion.line_start,
        "line_end": suggestion.line_end,
        "plan": suggestion.plan,
        "evidence": suggestion.evidence,
        "source_biomarker": suggestion.source_biomarker,
    }
    assert refactoring_public_id(row) == EXTRACT_METHOD_ID
    assert refactoring_public_id(as_dict) == EXTRACT_METHOD_ID


def test_colliding_kernels_get_distinct_ids_ordered_by_coordinates() -> None:
    """Two extractions can agree on host, signature and length; ids may not."""
    first = _extract_method(line_start=100, line_end=118)
    second = _extract_method(line_start=300, line_end=318)
    ids = assign_public_ids([second, first])
    assert len(set(ids)) == 2
    # Assignment follows the plans' coordinates, not the list order they arrived in.
    assert assign_public_ids([first, second]) == [ids[1], ids[0]]
    # The lower span keeps the collision-free id, so a batch that later stops
    # colliding does not rename the survivor.
    assert ids[1] == EXTRACT_METHOD_ID


def test_a_uniform_line_shift_renames_nothing_in_a_batch() -> None:
    batch = [_extract_method(), _clone()]
    shifted = [
        _extract_method(line_start=140, line_end=158),
        _clone(line_start=50, line_end=64),
    ]
    assert assign_public_ids(batch) == assign_public_ids(shifted)


def _extract_class(**overrides):
    base = dict(
        refactoring_type="extract_class",
        file_path="packages/core/src/repowise/core/report.py",
        target_symbol="GenerationReport",
        line_start=20,
        line_end=180,
        plan={
            "groups": [
                {"name": None, "methods": ["render", "to_dict"], "fields": ["rows"]},
                {"name": None, "methods": ["validate"], "fields": ["errors"]},
            ]
        },
        evidence={"lcom4": 7, "method_count": 10, "field_count": 7, "wmc": 22},
        impact_delta=2.4,
        effort_bucket="L",
        blast_radius={"dependents_count": 3},
        confidence="high",
        source_biomarker="low_cohesion",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_a_class_split_is_named_by_the_clusters_it_proposes() -> None:
    """The class name alone would hold still while the proposed split moved."""
    baseline = refactoring_public_id(_extract_class())
    reordered = dict(_extract_class().plan)
    reordered["groups"] = list(reversed(reordered["groups"]))
    assert refactoring_public_id(_extract_class(plan=reordered)) == baseline

    moved = _extract_class(line_start=500, line_end=660, impact_delta=0.1, effort_bucket="S")
    assert refactoring_public_id(moved) == baseline

    regrouped = dict(_extract_class().plan)
    regrouped["groups"] = [
        {"name": None, "methods": ["render"], "fields": ["rows"]},
        {"name": None, "methods": ["to_dict", "validate"], "fields": ["errors"]},
    ]
    assert refactoring_public_id(_extract_class(plan=regrouped)) != baseline
