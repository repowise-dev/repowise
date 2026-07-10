"""``UPDATE``/``DELETE`` with no ``WHERE`` clause: the whole-table footgun.

A checked-in statement that rewrites or empties an entire table. Sometimes
intentional (seed resets), always worth a reviewer's eyes; near-zero false
positives because the AST shape is unambiguous. Statements inside procedural
bodies are not seen (those bodies don't parse); top-level DML in schema,
migration, and seed files is. Maintainability-only, never the defect score.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "sql_update_delete_without_where"


class SqlUpdateDeleteWithoutWhereDetector:
    name = _KIND
    category = "sql"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=Severity.MEDIUM,
                function_name=None,
                line_start=hit.line,
                line_end=hit.line,
                details={"statement": hit.detail},
                reason=f"{hit.detail} has no WHERE clause: it touches every row in the table",
            )
            for hit in ctx.perf_hits
            if hit.kind == _KIND
        ]


BIOMARKER = SqlUpdateDeleteWithoutWhereDetector()
