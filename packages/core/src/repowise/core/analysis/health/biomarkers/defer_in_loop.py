"""Defer-in-loop — a Go ``defer`` accumulated on every loop iteration.

A ``defer`` inside a ``for`` loop does not run at the end of the iteration; it
runs when the *enclosing function* returns. Each iteration pushes another
deferred call onto the stack, so a file/handle/row opened-and-deferred in a loop
stays open until the whole function exits — the classic Go resource leak (open
files, unclosed ``*sql.Rows``, held mutexes). ``go vet`` and ``gocritic`` ship
the same check. A ``performance`` dimension signal (Go only).

High-precision by construction: it is a pure syntactic shape (a ``defer``
statement whose nearest enclosing loop is inside the same function), gated in the
Go dialect's ``loop_stmt_marker``. This detector lifts the pre-collected hits
into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "defer_in_loop"


class DeferInLoopDetector:
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
                        "a deferred call inside a loop runs only when the "
                        "enclosing function returns, so the resource stays held "
                        "across every iteration; close it in the loop body (or "
                        "wrap the body in a function) instead"
                    ),
                )
            )
        return out


BIOMARKER = DeferInLoopDetector()
