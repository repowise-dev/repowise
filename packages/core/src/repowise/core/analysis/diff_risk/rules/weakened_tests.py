"""``weakened-tests``: new skip or focus markers and loosened thresholds.

Removed test cases and assertion drops live in :mod:`.removed_tests`.
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import chain

from .. import markers as m
from ..models import RawFinding, Rule
from ..text import FileView, Hit, numeric_values

RULE: Rule = "weakened-tests"


def skip_hits(view: FileView, language: str | None, *, raw: bool) -> list[Hit]:
    """Skip and focus markers, minus the guarded ones.

    A skip on a branch line or right under one (``if not HAS_GO:
    pytest.skip(...)``, ``skip "x" unless windows?``, an ``except`` handler)
    guards an environment rather than turning a test off, so it does not count.
    """
    markers = [k for k in m.SKIP_MARKERS if k.applies_to(language, is_prose=False)]
    if raw:
        return view.raw_hits(markers, comment=False)
    return [h for h in view.code_hits(markers) if not _guarded(view.code_view, h.line - 1)]


def skip_message(line: str) -> str:
    return f"The change adds a skip or focus marker, so tests stop running: {line.strip()[:80]}"


def _guarded(code: list[str], index: int) -> bool:
    """Whether a guard sits on the skip's line, the code line right above it,
    or a line that encloses it (less indented) below the enclosing function
    or test header."""
    if m.SKIP_GUARDS.search(code[index]):
        return True
    above = (i for i in range(index - 1, -1, -1) if code[i].strip())
    first = next(above, None)
    if first is None:
        return False
    if m.SKIP_GUARDS.search(code[first]):
        return True
    return _enclosing_guard(code, chain([first], above), _indent(code[index]))


def _enclosing_guard(code: list[str], above: Iterable[int], indent: int) -> bool:
    """Walk outwards through the lines that enclose a skip, up to its header."""
    for i in above:
        line = code[i]
        if _indent(line) >= indent:
            continue
        if m.SKIP_GUARDS.search(line):
            return True
        indent = _indent(line)
        if m.BLOCK_HEADER.search(line) or indent == 0:
            return False
    return False


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def weakened_thresholds(path: str, spec: m.ThresholdFile, base: str, head: str) -> list[RawFinding]:
    """Threshold values the change made less strict.

    Occurrences of a key pair in file order, and only when both sides hold
    the same number of them: an added, removed or reordered entry cannot be
    paired by position, so the rule abstains.
    """
    keyed = [(key, True) for key in spec.minimums] + [(key, False) for key in spec.maximums]
    return [
        _threshold(path, key, change)
        for key, lower_is_weaker in keyed
        for change in _paired(base, head, key)
        if (change[1] < change[0] if lower_is_weaker else change[1] > change[0])
    ]


def _paired(base: str, head: str, key: str) -> list[tuple[float, float, int]]:
    """``(before, after, head line)`` per occurrence of *key*; none when counts differ."""
    olds, news = numeric_values(base, key), numeric_values(head, key)
    if len(olds) != len(news):
        return []
    return [(b, a, line) for (b, _), (a, line) in zip(olds, news, strict=True)]


def _threshold(path: str, key: str, change: tuple[float, float, int]) -> RawFinding:
    before, after, line = change
    evidence = f"{key}: {before:g} -> {after:g}"
    message = f"The change makes a threshold less strict ({evidence})"
    return RawFinding(RULE, "threshold", path, line, key, message, evidence)
