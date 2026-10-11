"""``SELECT *`` inside a maintained relation (view / materialized view /
routine body).

A bare star projection in a *maintained* relation silently changes shape when
the underlying table gains a column: downstream consumers break at a
distance. Ad-hoc scripts are deliberately out of scope (exploration is what
``*`` is for); the walker only emits the hit inside ``CREATE VIEW`` /
``FUNCTION`` / ``PROCEDURE``. Maintainability-only, never the defect score.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "sql_select_star"


class SqlSelectStarDetector:
    name = _KIND
    category = "sql"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=Severity.LOW,
                function_name=hit.function,
                line_start=hit.line,
                line_end=hit.line,
                details={"relation_kind": hit.detail},
                reason=(
                    f"SELECT * inside a {hit.detail or 'view'}: the relation's shape "
                    "silently changes when the source table gains a column"
                ),
            )
            for hit in ctx.perf_hits
            if hit.kind == _KIND
        ]


BIOMARKER = SqlSelectStarDetector()
