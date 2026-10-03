"""The impact / effort plane: where each file sits, and what it states about itself."""

from __future__ import annotations

import json

from repowise.core.analysis.health.impact_effort import (
    EFFORT_MIDLINE_LINES,
    GAIN_MIDLINE_POINTS,
    build_impact_effort,
    fix_first_marks,
    plan_lines,
)


def _file(path: str, nloc: int = 200, impact: float = 1.0) -> dict:
    return {"file_path": path, "nloc": nloc, "total_impact": impact}


def _plan(path: str, gain: float, spans: list[tuple[int, int]]) -> dict:
    steps = [{"line_start": a, "line_end": b} for a, b in spans]
    return {"file_path": path, "recoverable_health": gain, "details_json": json.dumps({"steps": steps})}


def test_a_planned_file_is_placed_by_its_plan() -> None:
    plane = build_impact_effort([_file("a.py", nloc=900, impact=4.0)], [_plan("a.py", 1.2, [(10, 19), (40, 49)])])

    (point,) = plane.points
    assert point.effort_basis == "plan"
    assert point.effort_lines == 20
    assert point.recoverable_health == 1.2


def test_a_file_without_a_plan_is_placed_by_its_size_and_deduction() -> None:
    plane = build_impact_effort([_file("b.py", nloc=320, impact=2.5)])

    (point,) = plane.points
    assert (point.effort_basis, point.effort_lines, point.recoverable_health) == ("file", 320, 2.5)


def test_a_plan_that_recovers_nothing_does_not_speak_for_the_file() -> None:
    plane = build_impact_effort([_file("c.py", nloc=80, impact=3.0)], [_plan("c.py", 0.0, [(1, 5)])])

    assert plane.points[0].effort_basis == "file"
    assert plane.points[0].recoverable_health == 3.0


def test_a_plan_with_no_span_falls_back_to_the_file() -> None:
    assert plan_lines({"steps": [{"line_start": None, "line_end": None}]}) == 0
    plane = build_impact_effort([_file("d.py", nloc=60)], [_plan("d.py", 1.0, [])])
    assert plane.points[0].effort_basis == "file"


def test_effort_is_never_below_one_line_so_a_log_axis_can_hold_it() -> None:
    plane = build_impact_effort([_file("empty.py", nloc=0)])
    assert plane.points[0].effort_lines == 1


def test_the_cap_keeps_the_largest_gains_and_says_how_many_were_left_out() -> None:
    files = [_file(f"f{i}.py", impact=float(i)) for i in range(10)]

    plane = build_impact_effort(files, cap=3)

    assert [p.file_path for p in plane.points] == ["f9.py", "f8.py", "f7.py"]
    assert (plane.plotted, plane.total, plane.cap) == (3, 10, 3)


def test_midlines_are_fixed_whatever_the_data() -> None:
    small = build_impact_effort([_file("a.py", nloc=5, impact=0.1)])
    large = build_impact_effort([_file("b.py", nloc=5000, impact=40.0)])

    for plane in (small, large):
        assert plane.effort_midline_lines == EFFORT_MIDLINE_LINES
        assert plane.gain_midline_points == GAIN_MIDLINE_POINTS


def test_a_file_is_marked_by_its_first_fix_first_item() -> None:
    items = [
        {"rank": 0, "tier": "now", "target": {"file_path": "a.py"}},
        {"rank": 1, "tier": "next", "target": {"file_path": "b.py"}},
        {"rank": 4, "tier": "later", "target": {"file_path": "a.py"}},
    ]
    marks = fix_first_marks(items)
    assert marks == {"a.py": ("now", 1), "b.py": ("next", 2)}

    plane = build_impact_effort([_file("a.py"), _file("c.py")], marks=marks)
    got = {p.file_path: (p.tier, p.fix_rank) for p in plane.points}
    assert got == {"a.py": ("now", 1), "c.py": (None, None)}


def test_as_dict_carries_the_scope_fields() -> None:
    body = build_impact_effort([_file("a.py")]).as_dict()
    assert set(body) == {
        "points",
        "plotted",
        "total",
        "cap",
        "effort_midline_lines",
        "gain_midline_points",
    }
