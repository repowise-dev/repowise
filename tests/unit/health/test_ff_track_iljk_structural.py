"""Regression tests for structural / SQL false-flag fixes.

Each test drives the REAL project functions against a hand-built counterexample
(no reimplementations): ``walk_file`` for the AST metrics that feed the class /
method biomarkers, ``_statement_smells`` for the SQL DML smell, and
``_severity_for`` for the coupling confidence cap.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.god_class import GodClassDetector
from repowise.core.analysis.health.biomarkers.hidden_coupling import (
    HiddenCouplingDetector,
    _severity_for,
)
from repowise.core.analysis.health.biomarkers.large_method import LargeMethodDetector
from repowise.core.analysis.health.complexity.walker import walk_file
from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.sql_complexity import _statement_smells

# --------------------------------------------------------------------------
# Fixture builders
# --------------------------------------------------------------------------


def _god_class_src() -> str:
    """A class that fires god_class, with a genuine brain method (``brainy``,
    high NLOC + moderate CCN) plus a short but very-high-CCN method
    (``shorty``) that has the highest CCN in the class but is NOT the brain."""
    lines = ["class Big:", "    def brainy(self, x):", "        y = 0"]
    for i in range(9):  # 9 branches -> ccn ~10
        lines.append(f"        if x == {i}:")
        lines.append(f"            y += {i}")
    for i in range(140):  # padding to push brainy (and the class) over the gates
        lines.append(f"        y = y + {i}")
    lines.append("        return y")
    lines.append("")
    lines.append("    def shorty(self, z):")  # nloc < 70, ccn ~31 (highest in class)
    lines.append("        if z == 0: return 0")
    for i in range(1, 30):
        lines.append(f"        elif z == {i}: return {i}")
    lines.append("        else: return -1")
    lines.append("")
    for i in range(14):  # filler methods -> method_count 16
        lines.append(f"    def m{i}(self): return {i}")
    return "\n".join(lines) + "\n"


def _flat_match_src() -> str:
    """A flat ``match``/``case`` dispatch table: every arm a single-expression
    return. Long enough to trip the NLOC threshold; CCN is 2 (base 1 + the lone
    match keyword point) — a layout artefact, not a complexity smell."""
    lines = ["def dispatch(status):", "    match status:"]
    for i in range(31):
        lines.append(f"        case {i}:")
        lines.append(f'            return "s{i}"')
    lines.append("        case _:")
    lines.append('            return "unknown"')
    return "\n".join(lines) + "\n"


def _branchy_src() -> str:
    """A long method with real branching (several ``if`` statements) — a genuine
    large_method that must still fire after the CCN floor bump."""
    lines = ["def branchy(n):", "    total = 0"]
    for i in range(5):
        lines.append(f"        if n == {i}:")
        lines.append(f"            total += {i}")
    for i in range(60):
        lines.append(f"    total = total + {i}")
    lines.append("    return total")
    return "\n".join(lines) + "\n"


def _class_ctx(classes) -> FileContext:
    return FileContext(
        file_path="src/example.py",
        language="python",
        nloc=400,
        has_test_file=False,
        module=None,
        class_metrics=classes,
    )


def _fn_ctx(functions) -> FileContext:
    return FileContext(
        file_path="src/example.py",
        language="python",
        nloc=200,
        has_test_file=False,
        module=None,
        function_metrics={f.name: f for f in functions},
    )


# --------------------------------------------------------------------------
# god_class: quote the brain method's own CCN, not the class-wide max
# --------------------------------------------------------------------------


def test_god_class_reason_quotes_the_brain_methods_own_ccn():
    fc = walk_file("big.py", "python", _god_class_src().encode())
    assert fc.classes, "expected a class to be parsed (tree-sitter python present)"
    cls = fc.classes[0]

    # Sanity-check the fixture reproduces the false-flag setup.
    by_name = {m.name: m for m in cls.methods}
    assert by_name["brainy"].nloc >= 70 and by_name["brainy"].ccn >= 9  # the brain
    assert by_name["shorty"].nloc < 70  # NOT a brain method...
    assert cls.max_method_ccn == by_name["shorty"].ccn  # ...yet has the highest CCN
    assert cls.max_method_ccn > by_name["brainy"].ccn

    out = GodClassDetector().detect(_class_ctx([cls]))
    assert len(out) == 1
    result = out[0]

    brain_ccn = by_name["brainy"].ccn
    # The reason quotes the brain method's OWN CCN (~10), never shorty's (31).
    assert result.details["brain_method_name"] == "brainy"
    assert result.details["brain_method_ccn"] == brain_ccn
    assert f"CCN {brain_ccn}" in result.reason
    assert str(cls.max_method_ccn) not in result.reason.split("CCN ")[1]


# --------------------------------------------------------------------------
# sql_update_delete_without_where: a LIMIT bounds the row count
# --------------------------------------------------------------------------


def _smell_kinds(sql: str, dialect: str | None) -> list[str]:
    return [h.kind for h in _statement_smells(sql, dialect)]


@pytest.mark.parametrize("dialect", [None, "mysql"])
def test_limit_bounded_update_is_not_flagged(dialect):
    sql = "UPDATE logs SET archived = 1 ORDER BY created_at LIMIT 1000;\n"
    assert "sql_update_delete_without_where" not in _smell_kinds(sql, dialect)


@pytest.mark.parametrize("dialect", [None, "mysql"])
def test_unbounded_update_still_flagged(dialect):
    sql = "UPDATE logs SET archived = 1;\n"
    assert _smell_kinds(sql, dialect) == ["sql_update_delete_without_where"]


# --------------------------------------------------------------------------
# large_method: a flat match dispatch is layout, not a size smell
# --------------------------------------------------------------------------


def test_large_method_skips_flat_match_dispatch():
    fm = walk_file("m.py", "python", _flat_match_src().encode())
    assert fm.functions, "expected a function to be parsed"
    fn = fm.functions[0]
    # The flat dispatch trips the NLOC threshold but sits at CCN 2 (layout).
    assert fn.nloc >= LargeMethodDetector._NLOC_THRESHOLD
    assert fn.ccn == 2
    assert LargeMethodDetector().detect(_fn_ctx([fn])) == []


def test_large_method_still_fires_on_real_branching():
    fb = walk_file("b.py", "python", _branchy_src().encode())
    fn = fb.functions[0]
    assert fn.nloc >= LargeMethodDetector._NLOC_THRESHOLD
    assert fn.ccn >= LargeMethodDetector._CCN_FLOOR
    out = LargeMethodDetector().detect(_fn_ctx([fn]))
    assert len(out) == 1
    assert out[0].function_name == "branchy"


# --------------------------------------------------------------------------
# hidden_coupling: small-sample correlations are capped at MEDIUM
# --------------------------------------------------------------------------


def test_severity_capped_at_medium_for_few_shared_commits():
    # 4 shared of 5 = 80% correlation would be CRITICAL on ratio alone, but the
    # absolute count is far too small to trust that confidence.
    assert _severity_for(0.8, co_count=4) == Severity.MEDIUM
    assert _severity_for(0.7, co_count=3) == Severity.MEDIUM


def test_severity_reaches_high_and_critical_with_enough_shared_commits():
    assert _severity_for(0.8, co_count=16) == Severity.CRITICAL
    assert _severity_for(0.65, co_count=12) == Severity.HIGH


def test_detector_caps_small_sample_pair_at_medium():
    ctx = FileContext(
        file_path="src/payments.py",
        language="python",
        nloc=120,
        has_test_file=False,
        module=None,
        function_metrics={},
        git_meta={
            "commit_count_total": 5,
            "co_change_partners_json": json.dumps(
                [{"file_path": "src/billing.py", "co_change_count": 4}]
            ),
        },
        repo_commit_counts={"src/payments.py": 5, "src/billing.py": 5},
    )
    out = HiddenCouplingDetector().detect(ctx)
    assert len(out) == 1
    # 4 / min(5, 5) = 0.8 — CRITICAL on ratio alone, capped to MEDIUM here.
    assert out[0].severity == Severity.MEDIUM
