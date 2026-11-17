"""Line to enclosing symbol, over symbol spans the caller already holds.

Every span here is 1-indexed with an inclusive end, the shape ``wiki_symbols``
and the parser's ``Symbol`` both carry. Callers filter by kind before calling;
these functions only look at lines.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Iterable


class Span(Protocol):
    @property
    def start_line(self) -> int: ...

    @property
    def end_line(self) -> int: ...


S = TypeVar("S", bound=Span)


def symbol_at(spans: Iterable[S], line: int) -> S | None:
    """The innermost span containing *line*, or None.

    Innermost is the latest start; a tie goes to the shorter span, and spans
    that are identical go to the last one in *spans*. Spans parsed from one
    tree nest, so this is also the narrowest span; for spans that cross, the
    later start wins.
    """
    best: S | None = None
    for span in spans:
        if span.start_line <= line <= span.end_line and (
            best is None
            or span.start_line > best.start_line
            or (span.start_line == best.start_line and span.end_line <= best.end_line)
        ):
            best = span
    return best


def symbols_overlapping(
    spans: Iterable[S], ranges: Iterable[tuple[int, int]] | Iterable[list[int]]
) -> list[S]:
    """Spans that overlap any inclusive ``(lo, hi)`` line range, in input order.

    A span with no usable lines (start below 1, or ending before it starts) is
    skipped: it is a symbol whose position was not recorded, not one at line 0.
    """
    bounds = [(int(lo), int(hi)) for lo, hi in ranges]
    if not bounds:
        return []
    return [
        span
        for span in spans
        if span.start_line > 0
        and span.end_line >= span.start_line
        and any(span.start_line <= hi and lo <= span.end_line for lo, hi in bounds)
    ]


def innermost(spans: Iterable[S]) -> list[S]:
    """*spans* minus any span that strictly contains another, in input order.

    A range that cuts across a class and two of its methods then names the
    methods only, so one change is not counted at every level of nesting.
    """
    held = list(spans)
    return [
        span
        for span in held
        if not any(
            other is not span
            and span.start_line <= other.start_line
            and other.end_line <= span.end_line
            and (other.end_line - other.start_line) < (span.end_line - span.start_line)
            for other in held
        )
    ]


__all__ = ["Span", "innermost", "symbol_at", "symbols_overlapping"]
