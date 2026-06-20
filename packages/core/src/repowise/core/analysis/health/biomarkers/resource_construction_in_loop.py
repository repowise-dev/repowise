"""Resource-construction-in-loop — a heavy I/O client built each iteration.

Constructing a database connection, HTTP client, or SDK client
(``sqlite3.connect`` / ``httpx.Client`` / ``boto3.client`` / ``new
PrismaClient`` / ``new HttpClient`` / ``sql.Open``) inside a loop opens a fresh
connection or pool every iteration instead of reusing a single hoisted one — the
classic connection-churn / socket-exhaustion bug. A ``performance`` dimension
signal.

Precision-first: the walker's perf pass flags this ONLY for a distinctive,
classified resource constructor (matched per language in the dialect), never a
plain object construction. This detector lifts the pre-collected hits into
findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "resource_construction_in_loop"


class ResourceConstructionInLoopDetector:
    name = _KIND
    category = "performance"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.perf_hits:
            if hit.kind != _KIND:
                continue
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.MEDIUM,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={},
                    reason=(
                        "a heavy client / connection is constructed each loop "
                        "iteration; hoist and reuse a single instance"
                    ),
                )
            )
        return out


BIOMARKER = ResourceConstructionInLoopDetector()
