"""Line to enclosing symbol, over symbol spans the caller already holds.

Every span here is 1-indexed with an inclusive end, the shape ``wiki_symbols``
and the parser's ``Symbol`` both carry. Callers filter by kind before calling;
these functions only look at lines.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


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


def enclosing_chains(
    spans: Iterable[S], nests: Callable[[S, S], bool] | None = None
) -> list[list[S]]:
    """For each span, in input order, the spans that enclose it, outermost first.

    A span encloses another that its lines contain. Two spans on the same
    lines (a one-line record and its accessors, a method and an anonymous
    class written on its line) enclose each other only where *nests(outer,
    inner)* says so; with no *nests* they enclose nothing of each other.
    """
    held = list(spans)
    lines = [(span.start_line, span.end_line) for span in held]
    chains = _on_wider_lines(held, lines)
    if nests is not None:
        _add_on_same_lines(held, lines, chains, nests)
    return chains


def _on_wider_lines(held: list[S], lines: list[tuple[int, int]]) -> list[list[S]]:
    """For each of *held*, the spans whose lines contain its own and are not the same."""
    chains: list[list[S]] = [[] for _ in held]
    open_: list[int] = []
    for i in sorted(range(len(held)), key=lambda i: (lines[i][0], -lines[i][1])):
        while open_ and lines[open_[-1]][1] < lines[i][0]:
            open_.pop()
        chains[i] = [held[j] for j in open_ if lines[j] != lines[i]]
        open_.append(i)
    return chains


def _add_on_same_lines(
    held: list[S],
    lines: list[tuple[int, int]],
    chains: list[list[S]],
    nests: Callable[[S, S], bool],
) -> None:
    """Append to each chain the spans on the same lines that *nests* puts around its span,
    or around a span already put around it."""
    by_lines: dict[tuple[int, int], list[S]] = {}
    for span, key in zip(held, lines, strict=True):
        by_lines.setdefault(key, []).append(span)
    for chain, span, key in zip(chains, held, lines, strict=True):
        same = [other for other in by_lines[key] if other is not span]
        outer = _outers_among(span, same, nests)
        # A span that holds another on these lines has fewer outers there itself.
        outer.sort(key=lambda o: sum(nests(p, o) for p in same if p is not o))
        chain.extend(outer)


def _outers_among(span: S, same: list[S], nests: Callable[[S, S], bool]) -> list[S]:
    """The spans of *same* that *nests* puts around *span*, or around one of those."""
    outer: list[S] = []
    inners = [span]
    while inners:
        inner = inners.pop()
        found = [o for o in same if nests(o, inner) and all(o is not p for p in outer)]
        outer += found
        inners += found
    return outer


__all__ = ["Span", "enclosing_chains", "innermost", "symbol_at", "symbols_overlapping"]
