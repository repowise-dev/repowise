"""A finding id survives an edit above it and still tells two symbols apart."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.duplication import DuplicationReport
from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.analysis.health.finding_identity import (
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
)


def _ids(source: str) -> dict[str, str]:
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
    hits = [f for f in findings if f.biomarker_type == "io_in_loop"]
    assert {f.function_name for f in hits} == {"load_users", "load_orders"}
    assert all(SYMBOL_LINE_KEY in f.details for f in hits)
    return {f.function_name: finding_public_id(f) for f in hits}


def test_lines_inserted_above_a_function_keep_its_finding_id() -> None:
    before = _ids(_SOURCE)
    after = _ids("import os\n\n\nCONFIG = os.environ\n\n" + _SOURCE)
    assert after == before


def test_the_same_marker_in_another_function_has_another_id() -> None:
    ids = _ids(_SOURCE)
    assert ids["load_users"] != ids["load_orders"]


def _row(**overrides):
    base = {
        "file_path": "src/a.py",
        "biomarker_type": "complex_method",
        "function_name": "f",
        "line_start": 10,
        "line_end": 30,
        "details": {"ccn": 12},
        "dimension": "defect",
    }
    base.update(overrides)
    return base


def test_an_offset_into_the_symbol_is_what_the_id_holds() -> None:
    anchored = _row(details={"ccn": 12, SYMBOL_LINE_KEY: 10})
    shifted = _row(line_start=40, line_end=60, details={"ccn": 12, SYMBOL_LINE_KEY: 40})
    assert finding_public_id(anchored) == finding_public_id(shifted)
    moved_inside = _row(line_start=41, details={"ccn": 12, SYMBOL_LINE_KEY: 40})
    assert finding_public_id(moved_inside) != finding_public_id(anchored)


def test_without_an_anchor_the_id_keeps_absolute_lines() -> None:
    assert finding_public_id(_row()) != finding_public_id(_row(line_start=11))
    no_symbol = _row(function_name=None, details={"ccn": 12, SYMBOL_LINE_KEY: 10})
    assert finding_public_id(no_symbol) != finding_public_id(
        _row(function_name=None, line_start=11, details={"ccn": 12, SYMBOL_LINE_KEY: 11})
    )


def test_the_legacy_id_ignores_the_anchor() -> None:
    assert legacy_finding_public_id(_row()) == legacy_finding_public_id(
        _row(details={"ccn": 12, SYMBOL_LINE_KEY: 10})
    )
    assert legacy_finding_public_id(_row()) != finding_public_id(_row())
