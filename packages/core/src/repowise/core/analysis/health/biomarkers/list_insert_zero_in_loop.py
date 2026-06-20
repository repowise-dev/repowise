"""List-insert-at-zero-in-loop — ``lst.insert(0, x)`` every iteration.

Inserting at the front of a Python list shifts every existing element, so doing
it per iteration is O(n^2). The fix is ``collections.deque.appendleft`` (O(1)) or
appending then reversing once. A ``performance`` dimension signal. Gated by the
Python dialect to a literal ``0`` first argument (a variable index is not the
front-insertion anti-pattern). This detector lifts the hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "list_insert_zero_in_loop"


class ListInsertZeroInLoopDetector:
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
                        "insert(0, ...) in a loop shifts the whole list each pass (O(n^2)); "
                        "use collections.deque.appendleft or append-then-reverse"
                    ),
                )
            )
        return out


BIOMARKER = ListInsertZeroInLoopDetector()
