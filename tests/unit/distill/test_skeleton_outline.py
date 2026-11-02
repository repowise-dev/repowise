"""Outline mode: one line per symbol, every kept line at its real line number."""

from __future__ import annotations

import re

from repowise.core.distill.skeleton import SkeletonSymbol, build_skeleton

_MARKER_RE = re.compile(r"^\s*\.\.\. (\d+) lines \((\d+)-(\d+)\)$")

_SOURCE = '''"""Mod doc."""
import os

LIMIT = 3


@decorator
def top(
    first,
    second,
):
    return first + second


class Box:
    """Box doc."""

    def size(self) -> int:
        value = 1
        other = 2
        return value + other
'''

_SYMBOLS = [
    SkeletonSymbol("LIMIT", "variable", 4, 4, "LIMIT = 3"),
    SkeletonSymbol("top", "function", 7, 12, "def top(first, second)"),
    SkeletonSymbol("Box", "class", 15, 21, "class Box"),
    SkeletonSymbol("size", "method", 18, 21, "def size(self) -> int"),
]


def _numbered(text: str) -> list[tuple[int, str]]:
    """Walk the outline as the Read hook does: markers jump, other lines count."""
    out: list[tuple[int, str]] = []
    line_no = 1
    for line in text.splitlines():
        match = _MARKER_RE.match(line)
        if match:
            line_no = int(match.group(3)) + 1
            continue
        out.append((line_no, line))
        line_no += 1
    return out


def test_outline_lists_each_symbol_once_on_its_real_line() -> None:
    result = build_skeleton(_SOURCE, _SYMBOLS, mode="outline")
    assert result.mode == "outline"
    lines = _SOURCE.splitlines()
    kept = dict(_numbered(result.text))
    # The decorator is skipped; the signature is joined onto the def line.
    assert kept[8] == "def top(first, second):"
    assert kept[15] == "class Box:"
    assert kept[18] == "    def size(self) -> int:"
    assert "import os" not in result.text
    # Every line that was not rewritten is the file's own line at that number.
    for number, text in kept.items():
        if number not in (8, 15, 18):
            assert text == lines[number - 1]


def test_outline_cuts_long_signatures_at_the_width() -> None:
    result = build_skeleton(_SOURCE, _SYMBOLS, mode="outline", outline_width=10)
    kept = dict(_numbered(result.text))
    assert kept[8] == "def top..."
    assert len(kept[18].strip()) <= 10


def test_a_signature_past_the_scan_window_uses_the_indexed_one() -> None:
    params = [f"    p{i}," for i in range(20)]
    source = "\n".join(["def wide(", *params, "):", "    return 1", ""])
    symbols = [SkeletonSymbol("wide", "function", 1, 23, "def wide(p0, p1)")]
    result = build_skeleton(source, symbols, mode="outline")
    assert result.text.splitlines()[0] == "def wide(p0, p1)"
