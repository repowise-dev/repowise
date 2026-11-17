"""``apex_missing_sharing``: a top-level Apex class that runs SOQL or DML and
declares no sharing keyword (``health/apex_markers.py``).

Advisory: no defect corpus labels it, so it is listed and never deducts.
"""

from __future__ import annotations

from ..apex_markers import MISSING_SHARING
from ..models import Severity
from .base import BiomarkerResult, FileContext


class ApexMissingSharingDetector:
    name = MISSING_SHARING
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
                    f"Class {hit.function} runs SOQL or DML ({hit.detail}) and declares no sharing "
                    "keyword, so whether record sharing applies depends on its caller"
                ),
            )
            for hit in ctx.perf_hits
            if hit.kind == self.name
        ]
