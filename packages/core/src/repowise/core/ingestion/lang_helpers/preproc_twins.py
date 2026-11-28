"""Keep one definition of a C++ name written once per ``#if`` arm.

Platform and build-option code writes the same function in each arm::

    #ifdef VIRTUAL_FILTER
    bool Filter::pass(int ref) const { .. }
    #else
    inline bool Filter::pass(int ref) const { .. }
    #endif

Both arms balance their braces, so tree-sitter reads both as live code and the
file defines the name twice. A compiler sees one arm. The parser does the same
here: the twin in the first live arm (the first not inside an ``#if 0``)
keeps the name, and each twin in another arm of the same group, declaration
or definition, is dropped with everything nested in it, so the graph holds no
node, call or edge from an arm that is not the one read. A name defined in one arm only is untouched, and so
are types: an alias in one arm and a fallback class in the other
(``using weekday = std::chrono::weekday;`` / ``class weekday {..}``) both stay,
since receiver typing reads the class when the alias names nothing in the repo.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable

from ..models import CallSite, Symbol
from .preproc_arms import _ARM_SWITCH_RE, _DEAD_ARM_RE, _TOKEN_RE, _Arm, _Group, _GroupScan

_CALLABLE_KINDS = frozenset({"function", "method"})
_HEAD_COMMENT = re.compile(rb"/\*.*?\*/|//[^\n]*")


class _RecordingScan(_GroupScan):
    """The group scan, keeping every settled group."""

    def __init__(self) -> None:
        super().__init__()
        self.groups: list[_Group] = []

    def _settle(self, group: _Group) -> None:
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
    callables = Counter(s.id for s in symbols if s.kind in _CALLABLE_KINDS)
    if not any(n > 1 for n in callables.values()):
        return []
    every = _groups(source)
    if not any(len(g.arms) > 1 for g in every):
        return []
    line_starts = [0] + [m.end() for m in re.finditer(rb"\n", source)]
    offsets = [line_starts[min(s.start_line, len(line_starts)) - 1] for s in symbols]
    dead_arms = [a for g in every for a in g.arms if _is_dead(a.head)]
    dropped: set[int] = set()
    for group in every:
        if len(group.arms) > 1:
            dropped |= _later_twins(group, symbols, offsets, dead_arms)
    spans = [(symbols[i].start_line, symbols[i].end_line) for i in dropped]
    return [
        s
        for i, s in enumerate(symbols)
        if i in dropped or any(lo < s.start_line and s.end_line < hi for lo, hi in spans)
    ]


def _later_twins(
    group: _Group, symbols: list[Symbol], offsets: list[int], dead_arms: list[_Arm]
) -> set[int]:
    """The positions of the callables of *group* whose twin sits in the arm that is kept."""
    by_id: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for index, offset in enumerate(offsets):
        arm = _arm_of(group, offset) if symbols[index].kind in _CALLABLE_KINDS else None
        if arm is not None:
            by_id[symbols[index].id].append((arm, index))
    dropped: set[int] = set()
    for found in by_id.values():
        if len({arm for arm, _ in found}) < 2:
            continue
        live = [arm for arm, i in found if not _inside_any(offsets[i], dead_arms)]
        keep = min(live or [arm for arm, _ in found])
        dropped.update(index for arm, index in found if arm != keep)
    return dropped


def _is_dead(head: bytes) -> bool:
    """Is the arm opened by *head* ``#if 0`` / ``#elif 0``, a trailing comment allowed?"""
    return _DEAD_ARM_RE.match(_HEAD_COMMENT.sub(b"", head).rstrip()) is not None


def _inside_any(offset: int, arms: list[_Arm]) -> bool:
    return any(arm.body_start <= offset < arm.body_end for arm in arms)


def _arm_of(group: _Group, offset: int) -> int | None:
    """The index of the arm of *group* whose body holds *offset*, or None."""
    starts = [arm.body_start for arm in group.arms]
    index = bisect_right(starts, offset) - 1
    if index < 0 or offset >= group.arms[index].body_end:
        return None
    return index


def without_inactive_arm_twins(
    language: str, symbols: list[Symbol], source: bytes
) -> tuple[list[Symbol], list[Symbol]]:
    """``(kept, dropped)``: a C++ file's symbols split by :func:`inactive_arm_twins`."""
    dropped = inactive_arm_twins(symbols, source) if language == "cpp" else []
    if not dropped:
        return symbols, []
    gone = {id(s) for s in dropped}
    return [s for s in symbols if id(s) not in gone], dropped


def outside_dropped(sites: list[CallSite], dropped: list[Symbol]) -> list[CallSite]:
    """*sites* without those inside a *dropped* symbol's lines."""
    if not dropped:
        return sites
    return [c for c in sites if not any(s.start_line <= c.line <= s.end_line for s in dropped)]
