"""``apex_see_all_data``: ``@IsTest(SeeAllData=true)`` on an Apex test class or
method (``health/apex_markers.py``).

Advisory: no defect corpus labels it, so it is listed and never deducts.
"""

from __future__ import annotations

from ..apex_markers import SEE_ALL_DATA
from ..models import Severity
from .base import BiomarkerResult, FileContext


class ApexSeeAllDataDetector:
    name = SEE_ALL_DATA
    category = "apex"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=Severity.LOW,
                function_name=hit.function,
                line_start=hit.line,
                line_end=hit.line,
                details={"value": hit.detail},
                reason=(
                    f"@IsTest(SeeAllData=true) on {hit.detail} {hit.function}: the test reads the "
                    "org's live data, so its result depends on the org it runs in"
                ),
            )
            for hit in ctx.perf_hits
            if hit.kind == self.name
        ]
