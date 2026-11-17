"""``apex_hardcoded_id``: a record Id written as a string literal in non-test
Apex (``health/apex_markers.py``).

Advisory: no defect corpus labels it, so it is listed and never deducts.
"""

from __future__ import annotations

from ..apex_markers import HARDCODED_ID
from ..models import Severity
from .base import BiomarkerResult, FileContext


class ApexHardcodedIdDetector:
    name = HARDCODED_ID
    category = "apex"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if ctx.is_test_material:
            return []
        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=Severity.MEDIUM,
                function_name=hit.function,
                line_start=hit.line,
                line_end=hit.line,
                details={"value": hit.detail},
                reason=(
                    f"Hardcoded record Id '{hit.detail}': Ids differ between orgs and sandboxes"
                ),
            )
            for hit in ctx.perf_hits
            if hit.kind == self.name
        ]
