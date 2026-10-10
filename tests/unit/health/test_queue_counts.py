"""The count vocabulary, the judgements the index stores, and held-back steps."""

from __future__ import annotations

import copy

from repowise.core.analysis.health.fix_first import build_fix_first
from repowise.core.analysis.health.fix_first.build import judge_findings
from repowise.core.analysis.health.queue.counts import (
    NOT_JUDGED,
    counts_of,
    perf_judgement,
    queue_counts,
)
from repowise.core.analysis.health.queue.eligibility import perf_queue_verdict
from tests.unit.health.fix_first_rows import (
    FINDINGS,
    METRICS,
    PERFORMANCE,
    PLANS,
    REFACTORING,
    _step,
)


def test_the_levels_add_up_and_scope_reasons_leave_in_scope() -> None:
    counts = queue_counts(
        [(None, "now", 2), (None, "later", 3), ("test", None, 4), ("below_min_worth", None, 1),
         (NOT_JUDGED, None, 1)],
        shown=2,
    )
    assert counts.as_dict() == {
        "inventory": 11,
        "in_scope": 7,
        "eligible": 5,
        "due": 2,
        "shown": 2,
        "excluded": {"test": 4, "below_min_worth": 1, "not_judged": 1},
    }
    assert counts_of(5, 2, {"test": 4, "below_min_worth": 1, "not_judged": 1, "tooling": 0}, 2) == counts


def test_every_open_finding_is_judged_as_fix_first_judges_its_file() -> None:
    findings = [{**f, "id": f["public_id"]} for f in FINDINGS]
    judged = judge_findings(metrics=METRICS, findings=findings, plans=PLANS)
    assert judged["finding_t1"].reason == "test"
    assert judged["finding_h1"].reason == "history_only"
    assert judged["finding_h2"].reason == "history_only"
    # Resolved findings are no unit of the queue.
    assert "finding_r1" not in judged
    # An eligible finding carries the tier its item would.
    queue = build_fix_first(metrics=METRICS, findings=FINDINGS, limit=None)
    item = next(i for i in queue.items if i.target.file_path == "src/plain.py")
    assert judged["finding_n1"].reason is None and judged["finding_n1"].tier == item.tier


def test_a_cause_is_judged_by_the_default_queue_rule() -> None:
    for row in PERFORMANCE:
        facets = row["details"]["facets"]
        judgement = perf_judgement(row, facets, row["details"]["plan"])
        assert judgement.reason == perf_queue_verdict(row).reason
        assert (judgement.tier is None) == (judgement.reason is not None)


def test_unaudited_steps_stay_in_the_plan_not_the_item() -> None:
    """A plan led by an audited kind keeps its opportunity in Fix first, but a
    Move Method or Extract Class step behind the lead is held back."""
    refactoring = copy.deepcopy(REFACTORING[:1])
    steps = refactoring[0]["details"]["steps"]
    steps.append(_step("refac2_move", "move_method", "run", 0.4, mechanical=False))
    steps.append(_step("refac2_class", "extract_class", "Core", 0.4, mechanical=False))
    judged: dict = {}
    queue = build_fix_first(
        metrics=METRICS, findings=FINDINGS, refactoring=refactoring, plans=PLANS,
        limit=None, judged=judged,
    )
    item = next(i for i in queue.items if i.kind == "refactor")
    assert judged["refop2_core"].reason is None
    assert item.action.steps_total == 2
    assert {s.order for s in item.action.steps} == {1, 2}
    assert ("steps held back", "2 (move or extract-class, not yet audited; in the full plan)") in {
        (f.label, f.value) for f in item.facts
    }
