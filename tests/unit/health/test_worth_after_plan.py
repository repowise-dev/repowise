"""A plan that leaves a function at the break-up bar says so and ranks after the rest."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.opportunity import compose_opportunities
from repowise.core.analysis.health.refactoring.opportunity_rank import rank_sort_key
from repowise.core.analysis.health.refactoring.recommendations import (
    build_recommendations,
    leaves_above_bar,
)
from repowise.core.analysis.health.worth import above_bar_after, worst_after
from repowise.core.persistence.crud.analysis.refactoring_opportunities import _diversified_order


def _stage(name: str, ccn: int, nloc: int) -> dict:
    return {"suggested_name": name, "ccn": ccn, "nloc": nloc}


def test_a_staged_plan_reads_the_target_after_from_its_orchestrator() -> None:
    # graphify build_from_json: 141 -> 83 after four stages.
    plan = {
        "stages": [_stage("_a", 20, 60), _stage("_b", 15, 40)],
        "orchestrator": {"ccn_before": 141, "ccn_after": 83},
    }
    after = worst_after({"ccn_removed": 58, "slice_nloc": 100}, plan, "build_from_json", {})
    assert after == {"function": "build_from_json", "ccn": 83, "nloc": 0}
    assert above_bar_after(after) == (
        "leaves build_from_json at CCN 83, still above the break-up bar; more stages needed"
    )


def test_an_unstaged_plan_reads_the_target_from_its_measured_shape() -> None:
    shape = {"ccn": 70, "nloc": 180}
    after = worst_after({"ccn_removed": 12, "slice_nloc": 40}, {}, "run", shape)
    # 70 - 12 = 58 left in the target; the one helper is at most CCN 12.
    assert after == {"function": "run", "ccn": 58, "nloc": 141}
    assert above_bar_after(after) is not None


def test_a_helper_that_takes_the_whole_body_is_still_judged() -> None:
    # Moving everything into one helper empties the target but not the work.
    plan = {"suggested_name": "_body"}
    after = worst_after(
        {"ccn_removed": 150, "slice_nloc": 300}, plan, "f", {"ccn": 151, "nloc": 310}
    )
    assert after["function"] == "_body"
    assert above_bar_after(after) == (
        "leaves _body at CCN 150, still above the break-up bar; more stages needed"
    )


def test_length_alone_trips_the_bar_and_names_the_lines() -> None:
    after = worst_after({"ccn_removed": 3, "slice_nloc": 20}, {}, "table", {"ccn": 8, "nloc": 260})
    assert above_bar_after(after) == (
        "leaves table at 241 lines, still above the break-up bar; more stages needed"
    )


def test_a_plan_that_brings_everything_under_the_bar_is_not_marked() -> None:
    plan = {
        "stages": [_stage("_a", 12, 50), _stage("_b", 10, 40)],
        "orchestrator": {"ccn_before": 45, "ccn_after": 25},
    }
    after = worst_after({"ccn_removed": 20, "slice_nloc": 90}, plan, "f", {"nloc": 150})
    assert above_bar_after(after) is None
    assert above_bar_after(None) is None


def _plan(target: str, removed: int, line: int, *, impact: float = 1.0) -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type="extract_method",
        file_path=f"pkg/{target}.py",
        target_symbol=target,
        line_start=line,
        line_end=line + 200,
        plan={"span": {"start": line + 5, "end": line + 30}},
        evidence={"ccn_removed": removed, "slice_nloc": 25},
        impact_delta=impact,
        effort_bucket="M",
        blast_radius={"scope": "local"},
        confidence="high",
        source_biomarker="complex_method",
    )


def _finding(target: str, ccn: int, line: int) -> dict:
    return {
        "file_path": f"pkg/{target}.py",
        "function_name": target,
        "biomarker_type": "complex_method",
        "severity": "high",
        "line_start": line,
        "line_end": line + 200,
        "details": {"ccn": ccn, "nloc": 150},
    }


def test_only_the_targets_own_size_findings_count() -> None:
    plan = _plan("big", 10, 10)
    elsewhere = {**_finding("big", 90, 10), "line_start": 500, "line_end": 600}
    other = _finding("other", 90, 10) | {"file_path": "pkg/big.py"}
    assert leaves_above_bar(plan, [elsewhere, other]) is None
    assert leaves_above_bar(plan, [_finding("big", 90, 10)]).startswith("leaves big at CCN 80")


def test_a_finding_without_lines_is_dropped_when_the_name_repeats() -> None:
    plan = _plan("big", 10, 10)
    unlined = {**_finding("big", 90, 10), "line_start": None, "line_end": None}
    assert leaves_above_bar(plan, [unlined]) is not None
    other_big = {**_finding("big", 20, 10), "line_start": 500, "line_end": 520}
    assert leaves_above_bar(plan, [unlined, other_big]) is None


def test_a_plan_that_leaves_the_bar_ranks_after_one_that_does_not() -> None:
    # The marked plan scores higher, and still ranks second.
    big, small = _plan("big", 10, 10, impact=3.0), _plan("small", 10, 10, impact=1.0)
    findings = [_finding("big", 90, 10), _finding("small", 30, 10)]
    ranked = build_recommendations([big, small], findings=findings)
    assert [r.suggestion.target_symbol for r in ranked] == ["small", "big"]
    assert ranked[1].leaves_above_bar and "leaves_above_bar" in ranked[1].rank_facts()
    assert "leaves_above_bar" not in ranked[0].as_dict()
    # Without findings nothing is marked.
    assert not any(r.leaves_above_bar for r in build_recommendations([big, small]))


def test_the_opportunity_carries_the_reason_and_queues_after_finished_work() -> None:
    big, small = _plan("big", 10, 10, impact=3.0), _plan("small", 10, 10, impact=1.0)
    findings = [_finding("big", 90, 10), _finding("small", 30, 10)]
    opportunities = compose_opportunities([big, small], findings=findings)
    assert [o.file_path for o in opportunities] == ["pkg/small.py", "pkg/big.py"]
    demoted = opportunities[1]
    assert demoted.leaves_above_bar == demoted.steps[0].leaves_above_bar
    assert demoted.as_dict()["steps"][0]["leaves_above_bar"].startswith("leaves big at")
    assert "leaves_above_bar" not in opportunities[0].as_dict()


def test_marked_credited_work_sorts_and_queues_after_the_interleaved_head() -> None:
    def item(path: str, health: float, rank: float, above: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            file_path=path,
            opportunity_id=path,
            lead_biomarker="complex_method" if health else None,
            lead_refactoring_type="extract_method" if health else "break_cycle",
            recoverable_health=health,
            rank_score=rank,
            leaves_above_bar=above,
        )

    marked = item("pkg/a/x.py", 0.9, 0.9, "leaves x at CCN 60")
    plain = item("pkg/b/y.py", 0.5, 0.3)
    cycle = item("pkg/c/z.py", 0.0, 0.8)
    ranked = sorted([cycle, marked, plain], key=rank_sort_key)
    assert [o.file_path for o in ranked] == ["pkg/b/y.py", "pkg/a/x.py", "pkg/c/z.py"]
    assert _diversified_order(ranked) == [0, 1, 2]
