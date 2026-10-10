"""Performance plan steps: one per call site, each naming the call it batches.

Two markers observing one call used to become two identical steps, and the
intro step added a bulk form to whichever member's sink happened to resolve,
even when the loop's own N+1 was a different call.
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.perf.opportunities import build_performance_opportunities
from repowise.core.analysis.health.refactoring.performance_fix import (
    performance_fix_suggestions,
)
from tests.unit.health.test_perf_opportunities import _finding

_EVOLUTION = "core/decisions/evolution.py"


def _steps(rows) -> list[dict]:
    opportunities = build_performance_opportunities(rows)
    assert len(opportunities) == 1
    return performance_fix_suggestions(opportunities)[0].plan["steps"]


def _hits(source: str):
    return walk_file("t.py", "python", source.encode()).perf_hits


def test_the_walker_stores_the_call_each_iteration_makes() -> None:
    src = (
        "from sqlalchemy import select\n"
        "async def sync(session, ids, groups):\n"
        "    for group in groups:\n"
        "        for key in ids:\n"
        "            await session.get(Record, key)\n"
        "            conn.execute(select(key)).fetchone()\n"
    )
    calls = {(h.kind, h.line): h.loop_facts().get("sink_call") for h in _hits(src)}
    assert calls[("io_in_loop", 5)] == "session.get"
    assert calls[("nested_loop_with_io", 5)] == "session.get"
    assert calls[("io_in_loop", 6)] == "conn.execute().fetchone"


def test_one_site_seen_twice_and_a_helper_sink_make_one_step_per_site() -> None:
    """The evolution.py shape: two markers on ``session.get`` at one line, and a
    helper call lower in the same loop whose path ends in ``upsert_decision_edge``."""
    rows = [
        _finding(_EVOLUTION, 471, loop_line=467, sink_call="session.get"),
        _finding(
            _EVOLUTION, 471, marker="nested_loop_with_io", loop_line=467, sink_call="session.get"
        ),
        _finding(
            _EVOLUTION,
            522,
            loop_line=467,
            call_path=(f"{_EVOLUTION}::run", "core/graph.py::upsert_decision_edge"),
        ),
    ]
    steps = _steps(rows)

    assert [(s["line"], s["loop_line"]) for s in steps] == [(471, 467), (522, 467)]
    assert not any("upsert_decision_edge" in str(s["symbol"]) for s in steps)
    assert "session.get" in steps[0]["action"]
    assert "upsert_decision_edge" in steps[1]["action"]
    assert all(s["symbol"] for s in steps)


def test_sites_that_reach_one_sink_keep_the_bulk_form_step_and_every_site() -> None:
    sink = ("shared.py::load", "git.py::head")
    rows = [
        _finding("a.py", 10, loop_line=8, call_path=("a.py::run", *sink)),
        _finding("b.py", 20, loop_line=18, call_path=("b.py::run", *sink)),
        _finding("b.py", 25, loop_line=18, call_path=("b.py::run", *sink)),
    ]
    steps = _steps(rows)

    assert steps[0]["symbol"] == "shared.py::load"
    assert steps[0]["line"] is None
    assert [(s["file_path"], s["line"]) for s in steps[1:]] == [
        ("a.py", 10),
        ("b.py", 20),
        ("b.py", 25),
    ]
    assert all("load call" in s["action"] for s in steps[1:])


def test_direct_calls_with_no_sink_get_no_unnamed_bulk_form_step() -> None:
    rows = [
        _finding("jobs.py", 12, loop_line=10, sink_call="session.execute"),
        _finding("jobs.py", 15, loop_line=10, sink_call="session.commit"),
    ]
    steps = _steps(rows)

    assert [s["line"] for s in steps] == [12, 15]
    assert all(s["symbol"] == "run" for s in steps)
    assert "session.execute" in steps[0]["action"]
    assert "session.commit" in steps[1]["action"]
