"""Membership-test-against-list-in-loop — ``x in big_list`` inside a loop.

Testing membership against a list is O(n); doing it once per loop iteration is
O(n·m). A ``set`` (or ``dict`` for keyed lookups) makes each probe O(1), turning
the loop linear. A ``performance`` dimension signal.

Precision-first: the walker's perf pass flags this ONLY when the right operand
(``big_list`` / the JS ``arr`` in ``arr.includes(x)``) is *provably* a list — a
name bound to a list literal / comprehension / ``list(...)`` / ``sorted(...)`` in
the same file. A set or dict membership test is already O(1) and never fires.
This detector lifts the pre-collected hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "membership_test_against_list_in_loop"


class MembershipTestAgainstListInLoopDetector:
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
                    severity=Severity.LOW,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={},
                    reason=(
                        "membership tested against a list inside a loop (O(n·m)); "
                        "use a set for O(1) lookups"
                    ),
                )
            )
        return out


BIOMARKER = MembershipTestAgainstListInLoopDetector()
