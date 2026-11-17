"""``apex_soql_injection``: a dynamic query splicing an outside String into a
quoted SOQL literal with no escaping or bind (``health/apex_markers.py``).

Advisory: no defect corpus labels it, so it is listed and never deducts.
"""

from __future__ import annotations

from ..apex_markers import SOQL_INJECTION
from ..models import Severity
from .base import BiomarkerResult, FileContext


class ApexSoqlInjectionDetector:
    name = SOQL_INJECTION
    category = "apex"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if ctx.is_test_material:
            return []
        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=Severity.HIGH,
                function_name=hit.function,
                line_start=hit.line,
                line_end=hit.line,
                details={"value": hit.detail},
                reason=(
                    f"Dynamic query splices `{hit.detail}`, a String from outside the method, into a "
                    "quoted SOQL literal with no escaping: bind it (`:value`) or pass it through "
                    "String.escapeSingleQuotes"
                ),
            )
            for hit in ctx.perf_hits
            if hit.kind == self.name
        ]
