"""The Fix-first builder over plain rows: eligibility, ranking and identity."""

from __future__ import annotations

import copy

from repowise.core.analysis.health.fix_first import FIX_EXCLUSIONS, build_fix_first
from tests.unit.health.fix_first_rows import (
    FINDINGS,
    METRICS,
    PERFORMANCE,
    PLANS,
    REFACTORING,
    _perf,
)


def _build(**over):
    rows = {
        "metrics": METRICS,
        "findings": FINDINGS,
        "refactoring": REFACTORING,
        "performance": PERFORMANCE,
        "plans": PLANS,
    }
    return build_fix_first(**{**rows, **over})


def test_empty_repository_has_no_lead() -> None:
    queue = build_fix_first()
    assert queue.items == () and queue.lead is None
    assert queue.totals.candidates == 0
    assert queue.as_dict()["lead"] is None


def test_each_exclusion_is_counted_by_reason() -> None:
    queue = _build()
    assert set(queue.totals.excluded) == set(FIX_EXCLUSIONS)
    assert queue.totals.excluded == {
        "test": 1,
        "tooling": 1,
        "generated": 0,
        "expected": 1,
        "no_plan": 1,
        "below_min_worth": 1,
        "history_only": 1,
    }
    assert queue.totals.eligible == 3
    assert queue.totals.candidates == 3 + 6
    assert queue.by_improves == {"defect": 2, "maintainability": 0, "performance": 1}


def test_history_never_leads_and_rides_along_as_context() -> None:
    queue = _build()
    assert [i.target.file_path for i in queue.items] == [
        "src/core.py",
        "src/repo.py",
        "src/plain.py",
    ]
    core = queue.lead
    assert core.kind == "refactor"
    assert "change" not in core.title.lower() and "entropy" not in core.why
    assert [c.label for c in core.context] == ["changes in 90 days", "change entropy"]
    # The history-only file is no item at all.
    assert all(i.target.file_path != "src/hist.py" for i in queue.items)


def test_trivial_extractions_drop_and_cost_their_credit() -> None:
    core = _build().lead
    assert core.action.steps_total == 1
    assert core.action.steps[0].text == "Extract lines 20-35 of run into a helper"
    assert core.gain.value == 1.8
    assert core.source.plan_ids == ("refac2_big",)
    assert len(core.verify.tests) == 5 and core.verify.tests_total == 7
    assert core.verify.command == "pytest tests/test_core_0.py"


def test_all_steps_trivial_is_below_min_worth() -> None:
    tiny = copy.deepcopy(REFACTORING[0])
    tiny["details"]["steps"] = tiny["details"]["steps"][1:]
    queue = _build(refactoring=[tiny])
    assert queue.totals.excluded["below_min_worth"] == 1
    assert all(i.kind != "refactor" for i in queue.items)


def test_tiers_follow_value_confidence_and_readiness() -> None:
    by_kind = {i.kind: i for i in _build().items}
    assert by_kind["refactor"].tier == "now"
    assert by_kind["perf_fix"].tier == "next"  # advisory plan: needs judgment
    assert by_kind["finding"].tier == "later"
    assert all(i.why_ranked[-1].factor == "tier" for i in by_kind.values())


def test_perf_rows_group_by_intervention() -> None:
    perf = next(i for i in _build().items if i.kind == "perf_fix")
    assert perf.source.opportunity_id == "perf2_a"
    assert ("sinks this fix covers", "2") in [(f.label, f.value) for f in perf.facts]
    assert perf.gain.text == "one database call per loop iteration, grows with the data"
    assert perf.title == "Batch the database calls loops make through load_all"


def test_ids_are_stable_and_independent_of_rank() -> None:
    first = _build()
    moved = copy.deepcopy(REFACTORING)
    moved[0]["rank_position"] = 9
    second = _build(refactoring=moved)
    assert [i.id for i in first.items] == [i.id for i in second.items]
    assert all(i.id.startswith("fix1_") and len(i.id) == 25 for i in first.items)
    assert [i.rank for i in first.items] == [0, 1, 2]


def test_no_kind_takes_more_than_three_of_the_first_five() -> None:
    refactors = []
    for n in range(5):
        row = copy.deepcopy(REFACTORING[0])
        row["opportunity_id"] = f"refop2_{n}"
        row["file_path"] = f"src/core{n}.py"
        row["details"]["steps"] = row["details"]["steps"][:1]
        refactors.append(row)
    queue = _build(refactoring=refactors, performance=[_perf("perf2_a", "s")], findings=[])
    # The fourth place goes to the perf fix; the fifth back to a refactor,
    # since no other kind has anything left at value 2 or above.
    assert [i.kind for i in queue.items[:5]] == [*["refactor"] * 3, "perf_fix", "refactor"]


def test_scope_all_keeps_tests_and_labels_them() -> None:
    queue = _build(scope="all")
    test_item = next(i for i in queue.items if i.target.file_path == "tests/test_core.py")
    assert ("file kind", "test") in [(c.label, c.value) for c in test_item.context]
    assert queue.totals.excluded["test"] == 0


def test_limit_caps_items_not_totals() -> None:
    queue = _build(limit=1)
    assert len(queue.items) == 1 and queue.totals.shown == 1 and queue.totals.eligible == 3
    assert queue.find(queue.lead.id) is queue.lead


def test_titles_and_whys_carry_no_biomarker_ids() -> None:
    for item in _build().items:
        for text in (item.title, item.why, item.gain.text):
            assert "_" not in text.replace("load_all", ""), text
        assert len(item.title) <= 90
