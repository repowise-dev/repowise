"""Keep one definition of a C++ name written once per ``#if`` arm.

Platform and build-option code writes the same function in each arm::

    #ifdef VIRTUAL_FILTER
    bool Filter::pass(int ref) const { .. }
    #else
    inline bool Filter::pass(int ref) const { .. }
    #endif

Both arms balance their braces, so tree-sitter reads both as live code and the
file defines the name twice. A compiler sees one arm. The parser does the same
here: the twin in the first live arm (the first not written ``#if 0``) keeps
the name, and each twin in a later arm of the same group is dropped, with
everything nested in it, so the graph holds no node, call or edge from an arm
that is not the one read. A name defined in one arm only is untouched, and so
are types: an alias in one arm and a fallback class in the other
(``using weekday = std::chrono::weekday;`` / ``class weekday {..}``) both stay,
since receiver typing reads the class when the alias names nothing in the repo.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable

from ..models import Symbol
from .preproc_arms import _ARM_SWITCH_RE, _DEAD_ARM_RE, _TOKEN_RE, _Group, _GroupScan

_CALLABLE_KINDS = frozenset({"function", "method"})


class _RecordingScan(_GroupScan):
    """The group scan, keeping every settled group with two or more arms."""

    def __init__(self) -> None:
        super().__init__()
        self.groups: list[_Group] = []

    def _settle(self, group: _Group) -> None:
        if len(group.arms) > 1:
            self.groups.append(group)
        super()._settle(group)


def _groups(source: bytes) -> list[_Group]:
    if not _ARM_SWITCH_RE.search(source):
        return []
    scan = _RecordingScan()
    for match in _TOKEN_RE.finditer(source):
        brace = match["brace"]
        if brace is not None:
            scan.brace(1 if brace == b"{" else -1)
        elif match["dir"] is not None:
            scan.directive(match["dir"], *match.span("dir"))
    return scan.groups


def inactive_arm_twins(symbols: Iterable[Symbol], source: bytes) -> list[Symbol]:
    """The symbols to drop: each later-arm twin of a name, and what nests inside one.

    Ids are compared before any discriminator is added, so twins whose
    parameters differ by arm still count as one name.
    """
    symbols = list(symbols)
    groups = _groups(source)
    if not groups or not symbols:
        return []
    line_starts = [0] + [m.end() for m in re.finditer(rb"\n", source)]
    offsets = [line_starts[min(s.start_line, len(line_starts)) - 1] for s in symbols]
    dropped: set[int] = set()
    for group in groups:
        by_id: dict[tuple[str, bool], list[tuple[int, int]]] = defaultdict(list)
        for index, offset in enumerate(offsets):
            if symbols[index].kind not in _CALLABLE_KINDS:
                continue
            arm = _arm_of(group, offset)
            if arm is not None:
                by_id[(symbols[index].id, symbols[index].is_declaration)].append((arm, index))
        for found in by_id.values():
            arms = {arm for arm, _ in found}
            if len(arms) < 2:
                continue
            keep = next(
                (a for a in sorted(arms) if not _DEAD_ARM_RE.match(group.arms[a].head)), min(arms)
            )
            dropped.update(index for arm, index in found if arm != keep)
    spans = [(symbols[i].start_line, symbols[i].end_line) for i in dropped]
    return [
        s
        for i, s in enumerate(symbols)
        if i in dropped or any(lo <= s.start_line and s.end_line <= hi for lo, hi in spans)
    ]


def _arm_of(group: _Group, offset: int) -> int | None:
    """The index of the arm of *group* whose body holds *offset*, or None."""
    starts = [arm.body_start for arm in group.arms]
    index = bisect_right(starts, offset) - 1
    if index < 0 or offset >= group.arms[index].body_end:
        return None
    return index


def inside_spans(line: int, dropped: Iterable[Symbol]) -> bool:
    """Is *line* inside one of the *dropped* symbols?"""
    return any(s.start_line <= line <= s.end_line for s in dropped)
