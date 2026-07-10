"""Regex-compile-in-loop — a regex recompiled on every loop iteration.

Compiling a regular expression (``Pattern.compile`` / ``regexp.MustCompile`` /
``regexp.Compile`` / ``Regex::new``) inside a loop rebuilds the same automaton
every iteration instead of hoisting it once. Compilation dominates matching, so
a static pattern recompiled per iteration is pure wasted work. A ``performance``
dimension signal.

Valid only where the language does NOT cache compiled patterns: Java, Go, and
Rust. Python's ``re`` module and .NET both cache internally, so the pattern is a
no-op there and the marker is intentionally absent from those dialects. Each
emitting dialect further gates on a *static literal* pattern (a dynamic
``MustCompile(pat)`` may legitimately vary each iteration and cannot be lifted).
This detector lifts the pre-collected, pre-gated hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "regex_compile_in_loop"


class RegexCompileInLoopDetector:
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
                        "a regex with a static pattern is recompiled every loop "
                        "iteration; compile it once outside the loop and reuse it"
                    ),
                )
            )
        return out


BIOMARKER = RegexCompileInLoopDetector()
