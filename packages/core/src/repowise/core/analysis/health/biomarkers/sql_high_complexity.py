"""SQL routine complexity: a stored procedure/function with high CCN.

Counted by the SQL health walker (``sql_complexity._walk_routines``) via
decision-keyword counting on the routine body (procedural SQL bodies do not
parse into an AST; see the walker's module docstring). Maintainability-only:
procedural SQL has no defect calibration, so this marker never moves the
surfaced defect score (``scoring._BIOMARKER_DIMENSIONS``).
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "sql_high_complexity"

_HIGH_CCN = 20


class SqlHighComplexityDetector:
    name = _KIND
    category = "sql"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.perf_hits:
            if hit.kind != _KIND:
                continue
            try:
                ccn = int(hit.detail)
            except ValueError:
                continue
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.HIGH if ccn >= _HIGH_CCN else Severity.MEDIUM,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={"ccn": ccn},
                    reason=(
                        f"SQL routine has cyclomatic complexity {ccn} "
                        "(decision keywords in the body)"
                    ),
                )
            )
        return out


BIOMARKER = SqlHighComplexityDetector()
