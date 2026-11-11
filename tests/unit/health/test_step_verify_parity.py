"""The TypeScript step ``verify`` contract still says what the engine emits.

Read as text, like ``tests/unit/doc_drift/test_ts_contract_parity.py``: the
values are literals and a TypeScript parser is a dependency this suite lacks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from repowise.core.analysis.health.refactoring.recommendations import (
    ValidationPlan,
    VerifyCoverage,
    verify_of,
)

_TYPES = Path(__file__).resolve().parents[3] / "packages" / "types" / "src"


def _source(name: str = "refactoring.ts") -> str:
    return (_TYPES / name).read_text(encoding="utf-8")


def test_the_coverage_union_matches_the_engine() -> None:
    match = re.search(r"type StepVerifyCoverage\s*=\s*([^;]+);", _source())
    assert match, "StepVerifyCoverage is not declared in refactoring.ts"
    assert set(re.findall(r'"([^"]+)"', match.group(1))) == set(get_args(VerifyCoverage))


def test_the_verify_fields_match_the_engine() -> None:
    match = re.search(r"interface StepVerify \{([^}]+)\}", _source())
    assert match, "StepVerify is not declared in refactoring.ts"
    fields = set(re.findall(r"^\s*(\w+)\??:", match.group(1), re.MULTILINE))
    plan = ValidationPlan("unknown", None, 0, [], False, [], [], [])
    assert fields == set(verify_of(plan))


def test_a_performance_plan_step_carries_the_same_verify() -> None:
    """The drawer reads stored performance steps through this type."""
    match = re.search(r"interface PerformanceOpportunityPlanStep \{([^}]+)\}", _source("health.ts"))
    assert match, "PerformanceOpportunityPlanStep is not declared in health.ts"
    assert re.search(r"^\s*verify\?:\s*StepVerify;", match.group(1), re.MULTILINE)
