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
        # The test file's plan and its own findings are two candidates.
        "test": 2,
        "tooling": 1,
        "unknown": 0,
        "generated": 0,
        "expected": 1,
        # An investigate cause has no strategy: the perf default queue's reason.
        "no_strategy": 1,
        "no_plan": 0,
        "below_min_worth": 1,
        "history_only": 1,
        "vendored": 0,
        "docs_example": 0,
        "deprecated": 0,
        "inherent_dispatch": 0,
        "small_function": 0,
        "no_concrete_step": 0,
        "low_value_kind": 0,
    }
    assert queue.totals.eligible == 3
    assert queue.totals.candidates == 3 + 7
    assert queue.by_improves == {"defect": 2, "maintainability": 0, "performance": 1}


def test_history_never_leads_and_rides_along_as_context() -> None:
    queue = _build()
    # The production, entry-reachable, growing database loop shares the top
    # band with the refactor and leads it on score.
    assert [i.target.file_path for i in queue.items] == [
        "src/repo.py",
        "src/core.py",
        "src/plain.py",
    ]
    core = queue.items[1]
    assert core.kind == "refactor"
    assert "change" not in core.title.lower() and "entropy" not in core.why
    assert [(c.label, c.value) for c in core.context] == [
        ("recent changes", "changed 12 times in 90 days"),
        ("scattered changes", "its changes are spread across many unrelated commits"),
    ]
    # The history-only file is no item at all.
    assert all(i.target.file_path != "src/hist.py" for i in queue.items)


def test_text_quotes_the_stored_numbers() -> None:
    core = next(i for i in _build().items if i.kind == "refactor")
    assert core.title == (
        "Start breaking up run (CCN 44, 50 lines): first lift lines 20-35 into sum_rows"
    )
    # The size is a fact; the why says why it matters here.
    assert core.why == (
        "run has many independent paths through it; 9 files import it, "
        "changed 12 times in 90 days."
    )
    assert ("size", "CCN 44, 50 lines, nests 4 deep") in [(f.label, f.value) for f in core.facts]
    assert core.action.steps[0].text == "Extract lines 20-35 of run into sum_rows(rows, limit) -> total"
    assert core.action.steps[1].text == "Extract lines 40-41 of run into a helper"
    # The model's credit is the gain, with one decimal; the builder does not re-judge it.
    assert (core.gain.value, core.gain.text) == (2.0, "+2.0 health on this file")
    assert core.source.plan_ids == ("refac2_big", "refac2_tiny")
    assert len(core.verify.tests) == 5 and core.verify.tests_total == 7
    assert core.verify.command == "pytest tests/test_core_0.py"


def test_an_excluded_plan_leaves_the_files_findings_to_compete() -> None:
    """Every candidate lands in exactly one bucket, whatever the other one decided."""
    small = {**REFACTORING[1], "opportunity_id": "refop2_plain", "file_path": "src/plain.py"}
    queue = _build(refactoring=[small], performance=[])
    plain = [i for i in queue.items if i.target.file_path == "src/plain.py"]
    assert [i.kind for i in plain] == ["finding"]
    assert queue.totals.excluded["below_min_worth"] == 1
    assert queue.totals.candidates == queue.totals.eligible + sum(queue.totals.excluded.values())


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
    # Same value as the refactors (production, loop size unknown, reachable),
    # lower tier.
    perf = _perf("perf2_a", "s")
    perf["details"] = {
        **perf["details"],
        "facets": {"loop_magnitude": "unknown", "exposure": "entry_reachable"},
    }
    queue = _build(refactoring=refactors, performance=[perf], findings=[])
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
    from repowise.core.analysis.health.scoring import _BIOMARKER_CATEGORY

    for item in _build().items:
        for text in (item.title, item.why, item.gain.text):
            assert not any(marker in text for marker in _BIOMARKER_CATEGORY), text
        assert len(item.title) <= 90


def test_cli_code_ships_for_code_shape_but_not_for_performance() -> None:
    from repowise.core.analysis.health.perf.causal import code_context, execution_context

    path = "packages/cli/src/app/cli/commands/update.py"
    assert (code_context(path), execution_context(path)) == ("production", "tooling")
    assert code_context("scripts/run.py") == execution_context("scripts/run.py") == "tooling"
    cli = {**METRICS[0], "file_path": path}
    finding = {**FINDINGS[1], "file_path": path}
    queue = _build(metrics=[cli], findings=[finding], refactoring=[], performance=[])
    assert [i.target.file_path for i in queue.items] == [path]


def test_value_leads_tier_and_a_huge_function_says_so() -> None:
    """A function far past every bar outranks tidy work that is safer to start."""
    huge = {**FINDINGS[1], "file_path": "src/plain.py", "function_name": "walk",
            "public_id": "finding_huge", "details": {"ccn": 249, "nloc": 1280, "max_nesting": 5}}
    queue = _build(findings=[*FINDINGS, huge], performance=[])
    lead = queue.lead
    assert (lead.kind, lead.tier, lead.target.file_path) == ("finding", "next", "src/plain.py")
    assert lead.title == "Break up walk (CCN 249, 1,280 lines)"
    assert ("problem size", "4") in [(f.factor, f.value) for f in lead.why_ranked]
    # With a plan, the title names the problem and the first concrete step.
    big = {**FINDINGS[1], "details": {"ccn": 120, "nloc": 900, "max_nesting": 6}}
    planned = _build(findings=[big], performance=[]).lead
    assert planned.title == (
        "Start breaking up run (CCN 120, 900 lines): first lift lines 20-35 into sum_rows"
    )


def test_every_open_refactoring_opportunity_carries_its_reason() -> None:
    queue = _build(limit=0)
    assert queue.items == ()
    assert queue.refactoring_reasons == {
        "refop2_core": None,
        "refop2_small": "below_min_worth",
        "refop2_test": "test",
    }
    # Off the wire: the reasons are a read-model input, not part of the queue.
    assert "refactoring_reasons" not in queue.as_dict()
