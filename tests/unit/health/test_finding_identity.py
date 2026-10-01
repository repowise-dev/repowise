"""A finding id survives edits that do not change what the finding is.

It holds still when lines are inserted above the symbol and when a metric in
the evidence moves, and it still tells apart two findings that differ in
substance: other symbols, same-named symbols, and every marker that can emit
several findings on one symbol.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.duplication import DuplicationReport
from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.analysis.health.finding_identity import (
    IDENTITY_DETAIL_KEYS,
    SYMBOL_INDEX_KEY,
    SYMBOL_KEY,
    SYMBOL_LINE_KEY,
    finding_public_id,
    legacy_finding_public_id,
)

_SOURCE = (
    "def load_users(cur, ids):\n"
    "    for i in ids:\n"
    "        cur.execute('SELECT * FROM users WHERE id = %s', (i,))\n"
    "\n"
    "\n"
    "def load_orders(cur, ids):\n"
    "    for i in ids:\n"
    "        cur.execute('SELECT * FROM orders WHERE id = %s', (i,))\n"
    "    try:\n"
    "        pass\n"
    "    except:\n"
    "        pass\n"
    "\n"
    "\n"
    "class A:\n"
    "    def run(self, cur, ids):\n"
    "        for i in ids:\n"
    "            cur.execute('SELECT * FROM a WHERE id = %s', (i,))\n"
    "\n"
    "\n"
    "class B:\n"
    "    def run(self, cur, ids):\n"
    "        for i in ids:\n"
    "            cur.execute('SELECT * FROM b WHERE id = %s', (i,))\n"
)


def _findings(source: str):
    data = source.encode("utf-8")
    fcx = walk_file("/tmp/repo.py", "python", data)
    if not fcx.functions:
        pytest.skip("python tree-sitter pack missing")
    pf = SimpleNamespace(
        file_info=SimpleNamespace(
            path="src/repo.py", language="python", abs_path="/tmp/repo.py", is_test=False
        ),
        symbols=[],
    )
    _, findings, _ = HealthAnalyzer(graph=None)._evaluate_file(
        pf, fcx, paired_tests=set(), package_roots=set(), disabled=[], dup_report=DuplicationReport()
    )
    return findings


def _ids(source: str) -> dict[tuple[str, str, int], str]:
    ids = {}
    for f in _findings(source):
        if f.biomarker_type in ("io_in_loop", "error_handling"):
            symbol = f.function_name or f.details.get(SYMBOL_KEY)
            key = (f.biomarker_type, symbol, f.details.get(SYMBOL_INDEX_KEY, 0))
            ids[key] = finding_public_id(f)
    return ids


def test_engine_findings_are_anchored_on_their_symbol() -> None:
    assert set(_ids(_SOURCE)) == {
        ("io_in_loop", "load_users", 0),
        ("io_in_loop", "load_orders", 0),
        ("io_in_loop", "run", 0),
        ("io_in_loop", "run", 1),
        ("error_handling", "load_orders", 0),
    }


def test_lines_inserted_above_keep_every_finding_id() -> None:
    before = _ids(_SOURCE)
    after = _ids("import os\n\n\nCONFIG = os.environ\n\n" + _SOURCE)
    assert after == before


def test_the_same_marker_in_another_symbol_has_another_id() -> None:
    io = [v for k, v in _ids(_SOURCE).items() if k[0] == "io_in_loop"]
    assert len(set(io)) == len(io) == 4


def _row(**overrides):
    base = {
        "file_path": "src/a.py",
        "biomarker_type": "complex_method",
        "function_name": "f",
        "line_start": 10,
        "line_end": 30,
        "details": {"ccn": 12, SYMBOL_LINE_KEY: 10},
        "dimension": "defect",
    }
    base.update(overrides)
    return base


def test_a_metric_change_keeps_the_id() -> None:
    changed = _row(line_end=31, details={"ccn": 13, "cognitive": 20, SYMBOL_LINE_KEY: 10})
    assert finding_public_id(changed) == finding_public_id(_row())
    file_level = _row(
        biomarker_type="churn_risk", function_name=None, line_start=None, line_end=None
    )
    assert finding_public_id(file_level) == finding_public_id(
        {**file_level, "details": {"relative_churn": 9.9}}
    )


def test_an_offset_into_the_symbol_is_what_the_id_holds() -> None:
    shifted = _row(line_start=40, line_end=60, details={"ccn": 12, SYMBOL_LINE_KEY: 40})
    assert finding_public_id(shifted) == finding_public_id(_row())
    moved_inside = _row(line_start=41, details={"ccn": 12, SYMBOL_LINE_KEY: 40})
    assert finding_public_id(moved_inside) != finding_public_id(_row())


def test_same_named_symbols_are_told_apart() -> None:
    second = _row(details={"ccn": 12, SYMBOL_LINE_KEY: 10, SYMBOL_INDEX_KEY: 1})
    assert finding_public_id(second) != finding_public_id(_row())


def test_without_an_anchor_the_id_keeps_absolute_lines() -> None:
    bare = _row(details={})
    assert finding_public_id(bare) != finding_public_id(_row(details={}, line_start=11))


# One pair per allowlisted marker: same symbol and line, different substance.
_DISTINCT = {
    "io_in_loop": (
        {"cross_function": True, "path": ["a.py::f", "a.py::g", "db.py::q"]},
        {"cross_function": True, "path": ["a.py::f", "a.py::h", "db.py::q"]},
    ),
    "blocking_io_under_lock": (
        {"cross_function": False},
        {"cross_function": True, "path": ["a.py::f", "a.py::g", "db.py::q"]},
    ),
    "lazy_load_in_loop": ({"relationship": "author"}, {"relationship": "tags"}),
    "error_handling": ({"kind": "bare_except"}, {"kind": "swallowed_catch"}),
    "complex_conditional": (
        {"enclosing_construct": "if"},
        {"enclosing_construct": "ternary"},
    ),
    "duplicated_assertion_block": (
        {"assertion_lines": [10, 14]},
        {"assertion_lines": [10, 18]},
    ),
    "sql_cartesian_join": ({"table": "a"}, {"table": "b"}),
    "hidden_coupling": ({"partner": "b.py"}, {"partner": "c.py"}),
    "contradictory_decision": (
        {"src_decision_id": "d1", "dst_decision_id": "d2"},
        {"src_decision_id": "d1", "dst_decision_id": "d3"},
    ),
}


def test_every_allowlisted_marker_has_a_distinctness_case() -> None:
    assert set(_DISTINCT) == set(IDENTITY_DETAIL_KEYS)


@pytest.mark.parametrize("kind", sorted(_DISTINCT))
def test_two_findings_of_one_marker_on_one_symbol_stay_distinct(kind: str) -> None:
    a, b = _DISTINCT[kind]
    anchor = {SYMBOL_LINE_KEY: 10}
    first = _row(biomarker_type=kind, details={**a, **anchor})
    second = _row(biomarker_type=kind, details={**b, **anchor})
    assert finding_public_id(first) != finding_public_id(second)


@pytest.mark.parametrize(
    "kind", ["large_assertion_block", "complex_conditional", "string_concat_in_loop"]
)
def test_findings_at_different_lines_of_one_symbol_stay_distinct(kind: str) -> None:
    first = _row(biomarker_type=kind, line_start=12)
    second = _row(biomarker_type=kind, line_start=15)
    assert finding_public_id(first) != finding_public_id(second)


def test_assertion_lines_are_relative_to_the_symbol() -> None:
    def block(shift: int):
        return _row(
            biomarker_type="duplicated_assertion_block",
            function_name=None,
            line_start=12 + shift,
            line_end=14 + shift,
            details={
                "assertion_lines": [12 + shift, 14 + shift],
                "partner_file": "tests/test_b.py",
                SYMBOL_LINE_KEY: 10 + shift,
                SYMBOL_KEY: "test_a",
            },
        )

    assert finding_public_id(block(0)) == finding_public_id(block(7))


def test_the_legacy_id_ignores_the_stamps() -> None:
    bare = _row(details={"ccn": 12})
    assert legacy_finding_public_id(bare) == legacy_finding_public_id(_row())
    assert legacy_finding_public_id(bare) != finding_public_id(_row())
