"""Cartesian join: a comma-join with no join predicate anywhere.

``FROM a, b`` with no ``WHERE`` produces the full cross product: O(n*m) rows
where the author almost certainly wanted a keyed join. An explicit ``CROSS
JOIN`` states intent and is not flagged; a comma-join whose statement carries
any ``WHERE`` is old-style join syntax and is not flagged either (the
predicate may live there). Performance-only, never the defect score.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "sql_cartesian_join"


class SqlCartesianJoinDetector:
    name = _KIND
    category = "performance"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=Severity.MEDIUM,
                function_name=hit.function,
                line_start=hit.line,
                line_end=hit.line,
                details={"table": hit.detail},
                reason=(
                    f"comma-join with '{hit.detail}' has no join predicate: "
                    "this is the full cross product"
                ),
            )
            for hit in ctx.perf_hits
            if hit.kind == _KIND
        ]


BIOMARKER = SqlCartesianJoinDetector()
