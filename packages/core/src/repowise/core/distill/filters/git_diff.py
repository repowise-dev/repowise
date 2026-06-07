"""Compact rendering of ``git diff`` output.

Diffstat-style summary first, then full hunks for the few files carrying most
of the change; the long tail is listed with counts only. Per-file hunks are
additionally capped with error-line-safe anchoring so one giant lockfile
rewrite cannot dominate the rendering.
"""

from __future__ import annotations

import re
from typing import ClassVar

from repowise.core.distill.filters.base import OutputFilter, cap_block, is_error_line
from repowise.core.distill.registry import filter_registry

_COMMAND_RE = re.compile(r"^git(?:\.exe)? (?:diff|show)\b")
_FILE_HEADER_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
# Diffstat row: " <path> | <count> +++---" or " <path> | Bin 120 -> 240 bytes".
_STAT_LINE_RE = re.compile(r"^ (?P<path>.+?) +\| +(?:(?P<count>\d+)|Bin\b.*?)(?: +[+-]+)?\s*$")
# Trailing roll-up: " 494 files changed, 30000 insertions(+), 12000 deletions(-)"
_STAT_SUMMARY_RE = re.compile(
    r"^\s*\d+ files? changed(?:, \d+ insertions?\(\+\))?(?:, \d+ deletions?\(-\))?\s*$"
)

#: Files whose hunks are kept in full (the rest are summarized).
KEEP_FILES = 5
#: Per-file line cap for kept hunk blocks.
MAX_BLOCK_LINES = 160
#: Stat-only mode: rows kept (ranked by churn); the rest collapse to a count.
KEEP_STAT_FILES = 20


@filter_registry.register
class GitDiffFilter(OutputFilter):
    name: ClassVar[str] = "git_diff"
    priority: ClassVar[int] = 10
    min_lines: ClassVar[int] = 40

    def matches_command(self, command: str) -> bool:
        return bool(_COMMAND_RE.match(command)) and "--stat" not in command

    def matches_content(self, output: str) -> bool:
        head = output.splitlines()[:5]
        return any(_FILE_HEADER_RE.match(ln) for ln in head)

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        blocks = _split_file_blocks(output)
        if not blocks:
            raise ValueError("not a recognizable unified diff")

        total_add = sum(b.added for b in blocks)
        total_del = sum(b.deleted for b in blocks)
        # Rank by change volume; render in original order for readability.
        keep_paths = {
            b.path
            for b in sorted(blocks, key=lambda b: b.added + b.deleted, reverse=True)[:KEEP_FILES]
        }

        lines = [f"diff: {len(blocks)} files changed, +{total_add} -{total_del}"]
        summarized: list[str] = []
        for block in blocks:
            if block.path in keep_paths:
                lines.append("")
                lines.extend(cap_block(block.lines, head=30, tail=MAX_BLOCK_LINES - 30))
            else:
                summarized.append(f"  {block.path} | +{block.added} -{block.deleted}")
        if summarized:
            lines.append("")
            lines.append(f"hunks omitted for {len(summarized)} smaller files:")
            lines.extend(summarized)
        return "\n".join(lines)


@filter_registry.register
class GitDiffStatFilter(OutputFilter):
    """Stat-only ``git diff --stat`` / ``git show --stat`` rendering.

    The hunk filter above deliberately skips ``--stat`` commands, and a
    stat-only dump has no hunks to drop — so a 494-file diffstat used to
    pass through raw. Keep the roll-up line plus the top rows by churn;
    the long tail collapses to a count (the engine's marker preserves it).
    """

    name: ClassVar[str] = "git_diff_stat"
    priority: ClassVar[int] = 11
    min_lines: ClassVar[int] = 40

    def matches_command(self, command: str) -> bool:
        return bool(_COMMAND_RE.match(command)) and "--stat" in command

    def matches_content(self, output: str) -> bool:
        lines = [ln for ln in output.splitlines() if ln.strip()]
        if not lines or any(_FILE_HEADER_RE.match(ln) for ln in lines[:5]):
            return False
        return bool(_STAT_SUMMARY_RE.match(lines[-1])) and bool(_STAT_LINE_RE.match(lines[0]))

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        all_lines = output.splitlines()
        if any(_FILE_HEADER_RE.match(ln) for ln in all_lines):
            # Mixed --stat -p output: the hunk renderer's territory; fall
            # back to raw rather than dropping hunks as "stat rows".
            raise ValueError("diffstat mixed with hunks")

        stat_rows: list[tuple[str, int]] = []  # (line, churn)
        summary: str | None = None
        extras: list[str] = []  # error lines and anything non-stat-shaped
        for line in all_lines:
            if not line.strip():
                continue
            if _STAT_SUMMARY_RE.match(line):
                summary = line.strip()
            elif m := _STAT_LINE_RE.match(line):
                stat_rows.append((line, int(m.group("count") or 0)))
            elif is_error_line(line):
                extras.append(line)

        if summary is None or len(stat_rows) <= KEEP_STAT_FILES:
            raise ValueError("not a large stat-only diff")

        top = sorted(stat_rows, key=lambda r: r[1], reverse=True)[:KEEP_STAT_FILES]
        omitted = len(stat_rows) - len(top)
        lines = [f"diff --stat: {summary}"]
        lines.append(f"top {len(top)} files by churn ({omitted} more files omitted):")
        lines.extend(ln for ln, _ in top)
        lines.extend(extras)
        return "\n".join(lines)


class _FileBlock:
    __slots__ = ("added", "deleted", "lines", "path")

    def __init__(self, path: str) -> None:
        self.path = path
        self.lines: list[str] = []
        self.added = 0
        self.deleted = 0


def _split_file_blocks(output: str) -> list[_FileBlock]:
    blocks: list[_FileBlock] = []
    current: _FileBlock | None = None
    for line in output.splitlines():
        if m := _FILE_HEADER_RE.match(line):
            current = _FileBlock(m.group("b"))
            blocks.append(current)
        if current is None:
            continue
        current.lines.append(line)
        if line.startswith("+") and not line.startswith("+++"):
            current.added += 1
        elif line.startswith("-") and not line.startswith("---"):
            current.deleted += 1
    return blocks
