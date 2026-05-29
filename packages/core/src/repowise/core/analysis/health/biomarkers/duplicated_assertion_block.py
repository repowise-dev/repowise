"""Duplicated Assertion Block — copy-pasted assertion sequences in tests.

When the same run of assertions appears in more than one test, a change to
the asserted behaviour has to be edited in several places — and usually
isn't, so the copies drift. This biomarker reuses the engine's Rabin-Karp
clone detector (``ctx.clones``) and keeps only the clone regions that
overlap an assertion block on a **test file**.

It complements ``dry_violation`` (which flags clones anywhere, weighted by
co-change): this one is scoped to test assertions and lands in the milder
``test_quality`` category so a duplicated test never tanks a file's score.
"""

from __future__ import annotations

from ..coverage import is_test_file
from ..models import Severity
from .base import BiomarkerResult, FileContext


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


class DuplicatedAssertionBlockDetector:
    name = "duplicated_assertion_block"
    category = "test_quality"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if not is_test_file(ctx.file_path) or not ctx.clones:
            return []
        blocks = [
            (start, end)
            for fn in ctx.function_metrics.values()
            for start, end, _count in fn.assertion_blocks
        ]
        if not blocks:
            return []

        out: list[BiomarkerResult] = []
        seen: set[tuple[int, int]] = set()
        for clone in ctx.clones:
            spans: list[tuple[int, int]] = []
            if clone.file_a == ctx.file_path:
                spans.append((clone.a_start_line, clone.a_end_line))
            if clone.file_b == ctx.file_path:
                spans.append((clone.b_start_line, clone.b_end_line))
            partner = clone.file_b if clone.file_a == ctx.file_path else clone.file_a
            for cs, ce in spans:
                for bs, be in blocks:
                    if not _overlaps(cs, ce, bs, be) or (bs, be) in seen:
                        continue
                    seen.add((bs, be))
                    out.append(
                        BiomarkerResult(
                            biomarker_type=self.name,
                            severity=Severity.MEDIUM,
                            function_name=None,
                            line_start=bs,
                            line_end=be,
                            details={
                                "assertion_lines": [bs, be],
                                "partner_file": partner,
                            },
                            reason=(
                                f"assertion block at lines {bs}-{be} is duplicated in {partner}"
                            ),
                        )
                    )
        return out


BIOMARKER = DuplicatedAssertionBlockDetector()
