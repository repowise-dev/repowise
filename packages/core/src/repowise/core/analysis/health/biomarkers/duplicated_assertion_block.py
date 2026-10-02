"""Duplicated Assertion Block — copy-pasted assertion sequences in tests.

When the same run of assertions appears in more than one test, a change to
the asserted behaviour has to be edited in several places — and usually
isn't, so the copies drift. This biomarker reuses the engine's Rabin-Karp
clone detector (``ctx.clones``) and keeps only the clone regions that
overlap an assertion block on a **test file**, and only when the block's
text, whitespace aside, really appears in the partner region. Clones match
on token shape, so without that check two asserts on different values pair up.

A block must hold at least ``_MIN_CHECKS`` assertions, not counting flag
checks (a bare null or boolean check on a plain name, per language in
``asserts/lexicon.py``). Shorter copies, like
``assertFalse(plugin.isEnabled()); assertNull(plugin.extender());`` or the
three-line "found it, it has this name, it has N buckets" check, are how two
tests of one fixture read, not a helper waiting to be extracted. The floor was
set on elasticsearch (Java), where such copies were nine in ten of what the
marker reported; from five real checks up a verbatim copy is a response-shape
check worth one helper. A copy inside one file still counts: the same five
checks repeated in two tests of one class are as much a missing helper as
copies across files.

It complements ``dry_violation`` (which flags clones anywhere, weighted by
co-change): this one is scoped to test assertions and lands in the milder
``test_quality`` category so a duplicated test never tanks a file's score.
"""

from __future__ import annotations

from ..asserts.lexicon import is_flag_check
from ..coverage import is_test_file
from ..models import Severity
from .base import BiomarkerResult, FileContext

_MIN_CHECKS = 5


def _real_checks(language: str, lines: list[str], block: tuple[int, int, int]) -> int:
    """A block's assertions, less the flag checks (``asserts/lexicon.py``)."""
    start, end, count = block
    flags = sum(1 for line in lines[max(start, 1) - 1 : end] if is_flag_check(language, line))
    return count - flags


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def _normalized(lines: list[str], start: int, end: int) -> str:
    """Lines ``start..end`` (1-indexed, inclusive) with whitespace collapsed."""
    return " ".join(" ".join(lines[max(start, 1) - 1 : end]).split())


def _block_copied(
    ctx: FileContext,
    block: tuple[int, int],
    span: tuple[int, int],
    partner: str,
    partner_span: tuple[int, int],
) -> bool:
    """Does the whole assertion *block* appear verbatim (whitespace aside) in
    the partner region, widened by however far the block runs past the clone?"""
    own_lines = ctx.clone_sources.get(ctx.file_path)
    partner_lines = ctx.clone_sources.get(partner)
    if own_lines is None or partner_lines is None:
        return False
    (bs, be), (cs, ce) = block, span
    lo = partner_span[0] - max(0, cs - bs)
    hi = partner_span[1] + max(0, be - ce)
    # An intra-file clone overlapping itself would find the block in place.
    if partner == ctx.file_path and _overlaps(lo, hi, bs, be):
        return False
    text = _normalized(own_lines, bs, be)
    return bool(text) and f" {text} " in f" {_normalized(partner_lines, lo, hi)} "


class DuplicatedAssertionBlockDetector:
    name = "duplicated_assertion_block"
    category = "test_quality"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if not is_test_file(ctx.file_path) or not ctx.clones:
            return []
        own_lines = ctx.clone_sources.get(ctx.file_path) or []
        blocks = [
            (start, end)
            for fn in ctx.all_functions
            for start, end, count in fn.assertion_blocks
            if _real_checks(ctx.language, own_lines, (start, end, count)) >= _MIN_CHECKS
        ]
        if not blocks:
            return []

        out: list[BiomarkerResult] = []
        seen: set[tuple[int, int]] = set()
        for clone in ctx.clones:
            a_span = (clone.a_start_line, clone.a_end_line)
            b_span = (clone.b_start_line, clone.b_end_line)
            sides: list[tuple[tuple[int, int], str, tuple[int, int]]] = []
            if clone.file_a == ctx.file_path:
                sides.append((a_span, clone.file_b, b_span))
            if clone.file_b == ctx.file_path:
                sides.append((b_span, clone.file_a, a_span))
            for span, partner, partner_span in sides:
                for bs, be in blocks:
                    if not _overlaps(*span, bs, be) or (bs, be) in seen:
                        continue
                    if not _block_copied(ctx, (bs, be), span, partner, partner_span):
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
