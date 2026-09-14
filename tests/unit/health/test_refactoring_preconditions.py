"""What the layer claims to know about a step, and what that licenses."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.preconditions import (
    JUDGMENT_REASONS,
    MECHANICAL_REASONS,
    classify_step,
    step_facts,
)


def step(kind: str, **kwargs) -> RefactoringSuggestion:
    payload = {
        "refactoring_type": kind,
        "file_path": "svc/orders.py",
        "target_symbol": "handle",
        "line_start": 10,
        "line_end": 40,
        "plan": {},
        "evidence": {},
        "impact_delta": 1.0,
        "effort_bucket": "S",
        "blast_radius": {},
        "confidence": "high",
        "source_biomarker": "",
    }
    payload.update(kwargs)
    return RefactoringSuggestion(**payload)


def go_split(**kwargs) -> RefactoringSuggestion:
    kwargs.setdefault(
        "plan",
        {
            "shim_required": False,
            "groups": [
                {"symbols": ["Stock"], "suggested_file": "inventory/stock.go"},
                {"symbols": ["Supplier"], "suggested_file": "inventory/supplier.go"},
            ],
        },
    )
    return step(
        "split_file",
        file_path="inventory/inventory.go",
        blast_radius={"dependent_count": 0, "import_rewrites": 0},
        **kwargs,
    )


# --------------------------------------------------------------------------
# what is licensed
# --------------------------------------------------------------------------


def test_a_proved_local_extraction_is_mechanical() -> None:
    applicability = classify_step(step("extract_method", blast_radius={"scope": "local"}))
    assert applicability.classification == "mechanical"
    assert applicability.reasons == ("dataflow_proved_local_extraction",)


def test_an_unsure_detector_keeps_its_own_extraction_a_judgment_call() -> None:
    applicability = classify_step(
        step("extract_method", blast_radius={"scope": "local"}, confidence="medium")
    )
    assert applicability.classification == "judgment"
    assert applicability.reasons == ("detector_confidence_below_high",)


def test_no_split_is_mechanical_even_when_no_import_moves() -> None:
    """Go keeps the qualified name, but the file still decides what compiles.

    A ``_linux.go`` basename or a ``//go:build`` line gates compilation on the
    file a symbol sits in, and neither reaches this layer, so the same-package
    case reports what it could not establish rather than claiming a proof.
    """
    applicability = classify_step(go_split())
    assert applicability.classification == "judgment"
    assert applicability.reasons == ("build_constraints_unknown",)


def test_extraction_is_the_only_mechanical_class() -> None:
    kinds = (
        "extract_method",
        "split_file",
        "extract_helper",
        "move_method",
        "extract_class",
        "break_cycle",
    )
    mechanical = {
        kind
        for kind in kinds
        if classify_step(
            step(kind, blast_radius={"scope": "local"}, plan=go_split().plan)
        ).classification
        == "mechanical"
    }
    assert mechanical == {"extract_method"}


def test_a_split_that_needs_a_shim_is_a_judgment_call() -> None:
    plan = dict(go_split().plan)
    plan["shim_required"] = True
    applicability = classify_step(go_split(plan=plan))
    assert applicability.classification == "judgment"
    assert "rewrites_dependent_imports" in applicability.reasons


def test_a_split_nobody_can_name_a_target_for_is_not_mechanical() -> None:
    plan = dict(go_split().plan)
    plan["groups"] = [{"symbols": ["Stock"], "suggested_file": None}]
    applicability = classify_step(go_split(plan=plan))
    assert applicability.classification == "judgment"
    assert applicability.reasons == ("no_named_target",)


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("extract_helper", "call_site_bindings_unproven"),
        ("move_method", "changes_symbol_home"),
        ("extract_class", "reshapes_class_surface"),
        ("break_cycle", "inverts_imports_across_files"),
    ],
)
def test_every_symbol_moving_type_stays_a_judgment_call(kind: str, reason: str) -> None:
    applicability = classify_step(step(kind))
    assert applicability.classification == "judgment"
    assert reason in applicability.reasons


def test_an_unknown_type_defaults_to_judgment_rather_than_to_a_promise() -> None:
    applicability = classify_step(step("extract_interface"))
    assert applicability.classification == "judgment"
    assert applicability.reasons == ("unclassified_refactoring_type",)


def test_every_reason_comes_from_the_declared_vocabulary() -> None:
    known = set(MECHANICAL_REASONS) | set(JUDGMENT_REASONS)
    kinds = (
        "extract_method",
        "split_file",
        "extract_helper",
        "move_method",
        "extract_class",
        "break_cycle",
        "made_up",
    )
    emitted = {reason for kind in kinds for reason in classify_step(step(kind)).reasons}
    assert emitted <= known


# --------------------------------------------------------------------------
# what is known
# --------------------------------------------------------------------------


def test_facts_are_scoped_to_what_the_type_can_carry() -> None:
    facts, _ = step_facts(step("extract_method", blast_radius={"scope": "local"}))
    assert "shim_required" not in facts, "a lifted span has no re-export shim to need"
    assert facts["local_scope"] is True


def test_a_fact_the_type_carries_but_the_row_lacks_reads_as_unknown() -> None:
    facts, unknowns = step_facts(step("move_method"))
    assert facts["callers"] is None
    assert "callers" in unknowns


def test_a_measured_zero_is_not_an_unknown() -> None:
    facts, unknowns = step_facts(step("move_method", blast_radius={"callers": 0}))
    assert facts["callers"] == 0
    assert "callers" not in unknowns


def test_framework_registration_is_never_claimed_to_be_known() -> None:
    for kind in ("split_file", "move_method", "extract_class", "break_cycle"):
        facts, unknowns = step_facts(step(kind))
        assert facts["framework_registration"] is None
        assert "framework_registration" in unknowns


def test_classification_is_a_pure_function_of_the_row() -> None:
    row = go_split()
    assert classify_step(row).as_dict() == classify_step(row).as_dict()
