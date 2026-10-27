"""Performance findings say where their loop starts (``loop_line``).

A reader showing the cost needs the loop header beside the call: the call can
sit many lines below it. The walker knows the loop node, so it stores the
header line; the finding id does not move with it.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.finding_identity import finding_public_id
from repowise.core.analysis.health.perf import collect_crossfn_io_in_loop
from tests.unit.health.test_perf_crossfn import _SCHEDULER, _build

_FAR = (
    "from sqlalchemy import select\n"
    "def sync(session, repos):\n"
    "    for repo in repos:\n"
    + "".join(f"        x{i} = repo.v{i}\n" for i in range(20))
    + "        session.execute(select(repo))\n"
)


def _hits(source: str):
    return walk_file("t.py", "python", source.encode()).perf_hits


def test_a_hit_far_below_its_loop_stores_the_header_line() -> None:
    hit = next(h for h in _hits(_FAR) if h.kind == "io_in_loop")
    assert hit.line == 24
    assert hit.loop_line == 3
    assert hit.loop_facts()["loop_line"] == 3


def test_the_innermost_loop_is_the_one_stored() -> None:
    src = (
        "from sqlalchemy import select\n"
        "def sync(session, groups):\n"
        "    for group in groups:\n"
        "        for repo in group:\n"
        "            session.execute(select(repo))\n"
    )
    hit = next(h for h in _hits(src) if h.kind == "io_in_loop")
    assert hit.loop_line == 4


def test_a_closure_defined_in_a_loop_does_not_inherit_the_outer_loop() -> None:
    src = (
        "from sqlalchemy import select\n"
        "def sync(session, groups):\n"
        "    for group in groups:\n"
        "        def inner(repos):\n"
        "            for repo in repos:\n"
        "                session.execute(select(repo))\n"
    )
    hit = next(h for h in _hits(src) if h.kind == "io_in_loop")
    assert hit.loop_line == 5


def test_a_hit_outside_any_loop_has_no_loop_line() -> None:
    src = "import time\nasync def f():\n    time.sleep(1)\n"
    hits = _hits(src)
    assert hits and all(h.loop_line == 0 and "loop_line" not in h.loop_facts() for h in hits)


def test_a_cross_function_hit_stores_the_loop_around_its_call_site(tmp_path: Path) -> None:
    walked, graph = _build(tmp_path, {"scheduler.py": _SCHEDULER})
    hit = next(h for hs in collect_crossfn_io_in_loop(walked, graph).values() for h in hs)
    assert hit.line == 15
    assert hit.loop_line == 14


def test_loop_line_does_not_move_the_finding_id() -> None:
    row = {
        "file_path": "a.py",
        "biomarker_type": "io_in_loop",
        "function_name": "sync",
        "line_start": 24,
        "dimension": "performance",
        "details": {"boundary_kind": "db"},
    }
    with_line = {**row, "details": {"boundary_kind": "db", "loop_line": 3}}
    assert finding_public_id(row) == finding_public_id(with_line)
