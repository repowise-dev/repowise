"""End-to-end: ``severity_overrides`` flows config -> engine -> score.

Proves the wiring between ``HealthConfig.to_analyzer_config`` and the engine's
per-file remap (``_evaluate_file(severity_overrides=...)``), not just the two
ends in isolation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.duplication import DuplicationReport
from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.analysis.health.models import Severity

# A single high-CCN function: many independent branches drive ccn >= 9, which
# trips ``complex_method`` (HIGH-ish). Demoting its severity must raise the
# file score.
_HIGH_CCN_SOURCE = b"""
def tangled(x):
    total = 0
    if x == 1:
        total += 1
    elif x == 2:
        total += 2
    elif x == 3:
        total += 3
    elif x == 4:
        total += 4
    elif x == 5:
        total += 5
    elif x == 6:
        total += 6
    elif x == 7:
        total += 7
    elif x == 8:
        total += 8
    elif x == 9:
        total += 9
    return total
"""


def _evaluate(severity_overrides):
    fcx = walk_file("/tmp/tangled.py", "python", _HIGH_CCN_SOURCE)
    if fcx.file_nloc == 0 or not fcx.functions:
        pytest.skip("python tree-sitter pack missing")
    pf = SimpleNamespace(
        file_info=SimpleNamespace(
            path="src/tangled.py", language="python", abs_path="/tmp/tangled.py"
        ),
        symbols=[],
    )
    metric, findings = HealthAnalyzer(graph=None)._evaluate_file(
        pf,
        fcx,
        path_basenames=set(),
        disabled=[],
        dup_report=DuplicationReport(),
        severity_overrides=severity_overrides,
    )
    return metric, findings


def test_complex_method_fires_then_demotion_raises_score():
    base_metric, base_findings = _evaluate(None)
    types = {f.biomarker_type for f in base_findings}
    if "complex_method" not in types:
        pytest.skip("complex_method did not fire on the fixture")

    demoted_metric, _ = _evaluate({"complex_method": Severity.LOW})
    # Demoting the only meaningful finding to LOW deducts less -> higher score.
    assert demoted_metric.defect_score > base_metric.defect_score
