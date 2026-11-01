"""Compact rendering of ``cat <one indexed source file>``.

A whole-file ``cat`` of a large source file renders as the file's outline
(every indexed symbol with its line range) plus the first lines of the file,
so the next read can be a ranged one. The outline comes from the symbol rows
the index already holds; nothing is parsed here.

It stays silent (raw output) unless every condition holds: the output is the
whole file, at least ``MIN_OUTPUT_CHARS`` and ``MIN_FILE_LINES`` long, and the
file has indexed symbols whose names still sit on their recorded lines. A file
edited since the last index fails that last check and passes through.
"""

from __future__ import annotations

import os
import re
from typing import ClassVar

from repowise.core.distill.cat_target import (
    MIN_FILE_LINES,
    MIN_OUTPUT_CHARS,
    index_mtime,
    indexed_symbols,
    locate_index,
    resolve_cat_path,
)
from repowise.core.distill.filters.base import OutputFilter
from repowise.core.distill.registry import filter_registry

# `cat <path>`, optionally after one `cd <dir> &&` or `cd <dir>;`. Matched on
# the normalized (lowercased) command; the path is read from the original.
_COMMAND_RE = re.compile(r"^(?:cd\s+\S+\s*(?:&&|;)\s*)?cat\s+[^\s|;&<>-][^\s|;&<>]*$")

#: Lines of the file shown after the outline, at most.
HEAD_LINES = 120
#: Budget for the rendering, before the engine appends its omission marker.
MAX_RENDER_CHARS = 3_600
#: The outline's share of the budget; the head lines get the rest.
MAX_OUTLINE_CHARS = 1_800
#: Width an outline row is cut at.
OUTLINE_WIDTH = 90


@filter_registry.register
class FileReadFilter(OutputFilter):
    name: ClassVar[str] = "file_read"
    priority: ClassVar[int] = 15
    min_lines: ClassVar[int] = MIN_FILE_LINES

    def matches_command(self, command: str) -> bool:
        return bool(_COMMAND_RE.match(command))

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        if exit_code != 0 or len(output) < MIN_OUTPUT_CHARS:
            raise ValueError("not a large whole-file read")
        path = resolve_cat_path(command, os.getcwd())
        if path is None:
            raise ValueError("not a single-file cat")
        located = locate_index(path)
        if located is None:
            raise ValueError("file is not in an indexed repository")
        db_path, rel = located
        try:
            edited_since_index = os.path.getmtime(path) > index_mtime(db_path)
        except OSError:
            edited_since_index = True
        if edited_since_index:
            raise ValueError("file changed after the last index")
        return render(rel, output, indexed_symbols(db_path, rel))


def render(rel: str, content: str, symbols: list[tuple[str, str, int, int]]) -> str:
    """Outline plus the first lines of *content*; raises when the index cannot vouch for it."""
    lines = content.splitlines()
    total = len(lines)
    if total < MIN_FILE_LINES or not symbols:
        raise ValueError("small or unindexed file")
    symbols = sorted(symbols, key=lambda s: (s[2], -s[3]))
    if not all(_still_there(lines, name, start, end) for name, _, start, end in symbols):
        # Edited since the index: the recorded lines no longer hold the
        # symbols, so an outline from them would point at the wrong lines.
        raise ValueError("index is stale for this file")
    out = [f"{rel}: {total} lines, {len(content)} chars. Outline (lines, symbol):"]
    out.extend(_outline(symbols))
    budget = MAX_RENDER_CHARS - sum(len(line) + 1 for line in out) - 80
    shown = 0
    for line in lines[:HEAD_LINES]:
        budget -= len(line) + 1
        if budget < 0:
            break
        shown += 1
    out.append(f"--- lines 1-{shown} of {total} ---")
    out.extend(lines[:shown])
    out.append(f"--- lines {shown + 1}-{total} not shown; read a range with sed -n ---")
    return "\n".join(out)


def _still_there(lines: list[str], name: str, start: int, end: int) -> bool:
    """True when *name* is a whole word on the symbol's first lines and the range fits.

    The first few lines, not only the first: a recorded start can sit on a
    decorator or attribute above the name.
    """
    if end > len(lines):
        return False
    short = name.rsplit(".", 1)[-1].rsplit("::", 1)[-1]
    word = re.compile(rf"(?<![\w$]){re.escape(short)}(?![\w$])")
    return any(word.search(line) for line in lines[start - 1 : min(end, start + 3)])


def _outline(symbols: list[tuple[str, str, int, int]]) -> list[str]:
    """One row per symbol, nested symbols indented; trimmed to the outline budget."""
    rows: list[tuple[int, str]] = []
    open_ends: list[int] = []
    for name, kind, start, end in symbols:
        while open_ends and start > open_ends[-1]:
            open_ends.pop()
        depth = len(open_ends)
        row = f"  {start}-{end} {'  ' * depth}{kind} {name}".rstrip()
        rows.append((depth, row[:OUTLINE_WIDTH]))
        open_ends.append(end)
    if _chars(r for _, r in rows) <= MAX_OUTLINE_CHARS:
        return [r for _, r in rows]
    top = [r for depth, r in rows if depth == 0]
    nested = len(rows) - len(top)
    kept: list[str] = []
    for row in top:
        if _chars(kept) + len(row) + 1 > MAX_OUTLINE_CHARS - 60:
            break
        kept.append(row)
    left = len(top) - len(kept)
    note = f"  ({nested} nested symbols not listed"
    note += f"; {left} more top-level after line {kept[-1].split('-')[0].strip()})" if left and kept else ")"
    return [*kept, note]


def _chars(rows) -> int:
    return sum(len(r) + 1 for r in rows)
