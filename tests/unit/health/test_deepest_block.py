"""Each function's deepest nested block, stored where a fix can start.

A "break up this function" item needs a first concrete step. When no
extraction plan covers the function, the deepest nested block is that step,
so the walker records its lines and size findings carry them as
``deepest_block``. No CCN, nesting or score moves, and the finding id does not.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.duplication import DuplicationReport
from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.analysis.health.finding_identity import finding_public_id
from repowise.core.analysis.health.models import HealthFindingData, Severity

_DEEP = """
def deep(rows, flag):
    total = 0
    for row in rows:
        if row.a:
            total += 1
        if row.b:
            for item in row.items:
                if item.ok:
                    while item.more:
                        if flag:
                            total += 2
                            item = item.next
    return total

def flat(x):
    return x + 1
"""


def _functions() -> dict:
    fcx = walk_file("/tmp/deep.py", "python", _DEEP.encode())
    if not fcx.functions:
        pytest.skip("python grammar unavailable")
    return {fc.name: fc for fc in fcx.functions}


def test_the_first_block_at_the_deepest_level_is_recorded() -> None:
    deep = _functions()["deep"]
    assert deep.max_nesting == 6
    assert deep.deepest_block == (11, 13)


def test_a_flat_function_records_none() -> None:
    assert _functions()["flat"].deepest_block is None


def test_size_findings_carry_it_and_keep_their_id() -> None:
    fcx = walk_file("/tmp/deep.py", "python", _DEEP.encode())
    pf = SimpleNamespace(
        file_info=SimpleNamespace(
            path="src/deep.py", language="python", abs_path="/tmp/deep.py", is_test=False
        ),
        symbols=[],
    )
    _, findings, _ = HealthAnalyzer(graph=None)._evaluate_file(
        pf,
        fcx,
        paired_tests=set(),
        package_roots=set(),
        disabled=[],
        dup_report=DuplicationReport(),
    )
    nested = next(f for f in findings if f.biomarker_type == "nested_complexity")
    assert nested.details["deepest_block"] == {"start": 11, "end": 13}
    without = HealthFindingData(
        **{
            **nested.__dict__,
            "details": {k: v for k, v in nested.details.items() if k != "deepest_block"},
        }
    )
    assert finding_public_id(nested) == finding_public_id(without)


def test_other_findings_do_not_carry_it() -> None:
    finding = HealthFindingData(
        biomarker_type="error_handling",
        severity=Severity.MEDIUM,
        file_path="src/a.py",
        function_name="deep",
        line_start=2,
        line_end=2,
        details={},
        health_impact=0.5,
    )
    from repowise.core.analysis.health.engine import _mark_deepest_block

    _mark_deepest_block([finding], list(_functions().values()))
    assert "deepest_block" not in finding.details
