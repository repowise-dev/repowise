"""What other layers say about a plan's target: annotate only, served on detail."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from repowise.core.analysis.health.fix_first.build import _dead_spans, _Files
from repowise.core.analysis.health.refactoring.annotations import (
    AnnotationFacts,
    PlanAnnotations,
    PlanRisk,
    RiskKind,
    annotate,
    partner_rows,
)
from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.opportunity import compose_opportunities
from repowise.core.analysis.health.refactoring.preconditions import (
    JUDGMENT_REASONS,
    classify_step,
)
from repowise.core.analysis.health.refactoring.recommendations import (
    build_recommendations,
    stored_recommendation,
)

_TYPES = Path(__file__).resolve().parents[3] / "packages" / "types" / "src" / "refactoring.ts"
_WHEN = datetime(2026, 10, 1, tzinfo=UTC)


def plan(kind: str = "extract_method", **kwargs) -> RefactoringSuggestion:
    payload = {
        "refactoring_type": kind,
        "file_path": "svc/orders.py",
        "target_symbol": "handle",
        "line_start": 20,
        "line_end": 30,
        "plan": {},
        "evidence": {},
        "impact_delta": 1.0,
        "effort_bucket": "S",
        "blast_radius": {"scope": "local"},
        "confidence": "high",
        "source_biomarker": "large_method",
    }
    payload.update(kwargs)
    return RefactoringSuggestion(**payload)


def kinds(notes: PlanAnnotations) -> list[str]:
    return [risk.kind for risk in notes.risks]


def test_a_decision_naming_the_file_governs_the_plan() -> None:
    facts = AnnotationFacts(decisions={"svc/orders.py": [("d1", "Keep handlers flat")]})
    notes = annotate(plan(), facts)
    assert notes.governed_by == ("d1",)
    assert notes.risks == (
        PlanRisk(
            "decision",
            'Decision "Keep handlers flat" governs this file; check it allows this change first.',
            "d1",
        ),
    )
    # A module derived from a decision's files does not govern its other files.
    assert not annotate(plan(), AnnotationFacts(decisions={"svc": [("d1", "Keep handlers flat")]}))


def test_a_dead_target_says_delete_it_by_the_fix_first_rule() -> None:
    rows = [
        {
            "kind": "unused_function",
            "file_path": "svc/orders.py",
            "symbol_name": "handle",
            "start_line": 18,
            "end_line": 40,
            "confidence": 0.9,
            "safe_to_delete": False,
            "status": "open",
        }
    ]
    dead = _Files([], {}, {}, (0.0, 0.0), dead=_dead_spans(rows))
    notes = annotate(plan(), AnnotationFacts(dead=dead))
    assert notes.risks[0].text == "Nothing reaches `handle`; delete it instead of refactoring it."
    # A finding below the sure bar is not a dead target.
    unsure = _Files([], {}, {}, (0.0, 0.0), dead=_dead_spans([{**rows[0], "confidence": 0.5}]))
    assert not annotate(plan(), AnnotationFacts(dead=unsure))


def test_moving_a_public_method_names_its_callers() -> None:
    moved = plan(
        "move_method",
        target_symbol="Order.total",
        plan={"to_class": "Pricing"},
        blast_radius={"callers": 3},
    )
    notes = annotate(moved, AnnotationFacts())
    assert kinds(notes) == ["public_api"]
    assert "3 callers" in notes.risks[0].text and "`Pricing`" in notes.risks[0].text
    private = plan("move_method", target_symbol="Order._total", blast_radius={"callers": 3})
    assert not annotate(private, AnnotationFacts())
    # A split names its importers; a local extraction moves nothing out.
    split = plan("split_file", line_start=None, blast_radius={"dependent_count": 1})
    assert annotate(split, AnnotationFacts()).risks[0].text.startswith("1 file imports from")
    assert not annotate(plan(blast_radius={"callers": 9}), AnnotationFacts())


def _recent(*shas: str) -> dict[str, tuple[datetime, str]]:
    return {sha: (_WHEN, f"author-{index % 2}") for index, sha in enumerate(shas)}


def test_recent_commits_on_the_innermost_function_read_as_an_active_edit() -> None:
    functions = {
        "svc/orders.py": [
            (1, 100, ("a", "b", "old")),  # the module-level wrapper
            (15, 35, ("c", "d")),  # the function holding lines 20-30
        ]
    }
    notes = annotate(plan(), AnnotationFacts(functions=functions, recent=_recent("a", "b", "c", "d")))
    assert kinds(notes) == ["active_edit"]
    assert notes.risks[0].text.startswith("Changed in 2 commits by 2 authors")
    one = annotate(plan(), AnnotationFacts(functions=functions, recent=_recent("c")))
    assert not one


def test_shallow_history_gives_no_active_edit_signal() -> None:
    functions = {"svc/orders.py": [(15, 35, ("c", "d"))]}
    assert not annotate(plan(), AnnotationFacts(functions=functions, recent=None))


def test_annotations_round_trip_through_the_stored_rank() -> None:
    notes = PlanAnnotations(
        governed_by=("d1",),
        risks=(PlanRisk("decision", "text", "d1"),),
        co_change_partners=(("svc/models.py", 7),),
    )
    assert PlanAnnotations.from_dict(notes.as_dict()) == notes
    assert PlanAnnotations().as_dict() == {}
    assert PlanAnnotations.from_dict({}) is None


def test_plan_detail_carries_annotations_and_lists_do_not() -> None:
    (item,) = build_recommendations([plan()])
    item.suggestion.id = "row-1"  # type: ignore[attr-defined]
    notes = PlanAnnotations(
        governed_by=("d1",),
        risks=(PlanRisk("decision", "text", "d1"),),
        co_change_partners=(("svc/models.py", 7),),
    )
    import dataclasses

    annotated = dataclasses.replace(item, annotations=notes)
    assert annotated.as_dict() == item.as_dict()
    detail = annotated.detail_dict()
    assert detail["governed_by"] == ["d1"]
    assert detail["risks"] == [{"kind": "decision", "text": "text", "ref": "d1"}]
    assert detail["blast_radius"]["co_change_partners"] == partner_rows(notes.co_change_partners)
    assert "co_change_partners" not in annotated.as_dict()["blast_radius"]

    bare = item.detail_dict()
    assert bare["governed_by"] == [] and bare["risks"] == []

    row = {"id": "row-1", **item.as_dict(), "rank_json": annotated.rank_facts()}
    assert stored_recommendation(row).detail_dict() == detail  # type: ignore[union-attr]
    unannotated = {"id": "row-1", **item.as_dict(), "rank_json": item.rank_facts()}
    assert "annotations" not in item.rank_facts()
    assert stored_recommendation(unannotated).annotations is None  # type: ignore[union-attr]


def test_a_governing_decision_makes_a_proved_extraction_a_judgment_call() -> None:
    assert classify_step(plan()).classification == "mechanical"
    governed = classify_step(plan(), governed=True)
    assert governed.classification == "judgment"
    assert governed.reasons == ("dataflow_proved_local_extraction", "governed_by_decision")
    assert "governed_by_decision" in JUDGMENT_REASONS

    (free,) = compose_opportunities([plan()])
    plan_id = free.steps[0].plan_id
    (held,) = compose_opportunities([plan()], governed={plan_id})
    assert free.mechanical_steps == 1
    assert held.mechanical_steps == 0
    assert held.steps[0].applicability.reasons[-1] == "governed_by_decision"


def test_the_risk_contract_matches_the_typescript_one() -> None:
    source = _TYPES.read_text(encoding="utf-8")
    union = re.search(r"type PlanRiskKind\s*=\s*([^;]+);", source)
    assert union and set(re.findall(r'"([^"]+)"', union.group(1))) == set(get_args(RiskKind))
    for name, sample in (
        ("PlanRisk", PlanRisk("decision", "t").as_dict()),
        ("PlanCoChangePartner", partner_rows([("a.py", 1)])[0]),
    ):
        body = re.search(rf"interface {name} \{{([^}}]+)\}}", source)
        assert body, name
        assert set(re.findall(r"^\s*(\w+)\??:", body.group(1), re.MULTILINE)) == set(sample)
