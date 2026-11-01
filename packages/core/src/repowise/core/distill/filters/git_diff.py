"""Compact rendering of ``git diff`` / ``git show`` / ``git log -p`` output.

Every changed line and every hunk header is kept. What folds: runs of
unchanged context (one line per run; the hunk header still gives the line
numbers), the ``index``/``---``/``+++`` lines the ``diff --git`` line already
states, and lockfile or generated files (one line each, with their counts).
When the folded diff is still over the budget, the files carrying the most
change keep their hunks and the rest are listed with counts only. Lines that
are not part of a hunk (a commit header, a chained command's output) are kept
verbatim.
"""

from __future__ import annotations

import fnmatch
import re
from typing import ClassVar

from repowise.core.distill.filters.base import OutputFilter, is_error_line
from repowise.core.distill.registry import filter_registry

_COMMAND_RE = re.compile(r"^git(?:\.exe)? (?:diff|show)\b")
# `git log -p` prints the same file blocks between commit headers.
_LOG_PATCH_RE = re.compile(r"^git(?:\.exe)? log\b.*\s(?:-p|--patch|-u)(?=\s|$)")
_FILE_HEADER_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,(?P<old>\d+))? \+\d+(?:,(?P<new>\d+))? @@")
# File header lines the `diff --git` line already states.
_REDUNDANT_HEADER_RE = re.compile(r"^(?:index [0-9a-f]+\.\.[0-9a-f]+|--- a/|\+\+\+ b/)")
# Diffstat row: " <path> | <count> +++---" or " <path> | Bin 120 -> 240 bytes".
_STAT_LINE_RE = re.compile(r"^ (?P<path>.+?) +\| +(?:(?P<count>\d+)|Bin\b.*?)(?: +[+-]+)?\s*$")
# Trailing roll-up: " 494 files changed, 30000 insertions(+), 12000 deletions(-)"
_STAT_SUMMARY_RE = re.compile(
    r"^\s*\d+ files? changed(?:, \d+ insertions?\(\+\))?(?:, \d+ deletions?\(-\))?\s*$"
)

#: Lockfiles and generated files, whose hunks fold to one line. The lockfile
#: names mirror the indexer's filename blocklist (``ingestion/traverser.py``);
#: kept here so the filter imports nothing heavy on the hook path.
_GENERATED_PATTERNS = (
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "go.sum",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.snap",
    "*_pb2.py",
    "*_pb2_grpc.py",
    "*.pb.go",
    "*_pb.js",
    "*_pb.ts",
)
#: Outputs under this size pass through: folding them saves too little.
MIN_OUTPUT_CHARS = 5_000
#: Budget for the rendering, before the engine appends its omission marker.
#: Sized on a month of real agent diffs: at 14k chars the folded rendering
#: keeps more changed lines than the old top-five-files rendering did while
#: delivering fewer bytes. A 4k budget saved twice as much but dropped almost
#: half of all changed lines, so an agent reviewing a diff would have to
#: expand or re-run it.
MAX_RENDER_CHARS = 14_000
#: Files listed by count when their hunks do not fit; the rest are counted.
MAX_SUMMARY_ROWS = 100
#: Stat-only mode: rows kept (ranked by churn); the rest collapse to a count.
KEEP_STAT_FILES = 20


@filter_registry.register
class GitDiffFilter(OutputFilter):
    name: ClassVar[str] = "git_diff"
    # Ahead of git_log, so `git log -p` keeps its hunks.
    priority: ClassVar[int] = 9
    min_lines: ClassVar[int] = 40

    def matches_command(self, command: str) -> bool:
        if _LOG_PATCH_RE.match(command):
            return True
        return bool(_COMMAND_RE.match(command)) and "--stat" not in command

    def matches_content(self, output: str) -> bool:
        head = output.splitlines()[:5]
        return any(_FILE_HEADER_RE.match(ln) for ln in head)

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        if len(output) < MIN_OUTPUT_CHARS:
            raise ValueError("small diff passes through")
        preamble, blocks = _split_file_blocks(output)
        if not blocks:
            raise ValueError("not a recognizable unified diff")
        total_add = sum(b.added for b in blocks)
        total_del = sum(b.deleted for b in blocks)
        # Output ahead of the diff (a commit header, a chained command) is
        # kept as it is: only the diff itself is folded.
        head = [f"diff: {len(blocks)} files changed, +{total_add} -{total_del}", *preamble]
        rendered = [(block, _fold_block(block)) for block in blocks]
        body = [line for block, folded in rendered for line in (*folded, *block.tail)]
        if _size(head) + _size(body) > MAX_RENDER_CHARS:
            body = _fit(rendered, max(0, MAX_RENDER_CHARS - _size(head)))
        return "\n".join(head + body)


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
    __slots__ = ("added", "deleted", "generated", "lines", "path", "tail")

    def __init__(self, path: str) -> None:
        self.path = path
        self.lines: list[str] = []
        # Lines after the last hunk that are not part of the diff (the next
        # commit's header in `git log -p`, a chained command's output). They
        # are always shown, whatever happens to the file's hunks.
        self.tail: list[str] = []
        self.added = 0
        self.deleted = 0
        name = path.rsplit("/", 1)[-1]
        self.generated = any(fnmatch.fnmatchcase(name, p) for p in _GENERATED_PATTERNS)


class _HunkCursor:
    """Tracks hunk bodies by the line counts in each ``@@`` header.

    A changed line that itself starts with ``---`` or ``+++`` is still a
    changed line, and a line after a hunk's last counted line is not part of
    the diff.
    """

    __slots__ = ("new", "old", "seen")

    def __init__(self) -> None:
        self.old = self.new = 0
        self.seen = False

    def classify(self, line: str) -> str:
        """``hunk``, ``context``, ``removed``, ``added`` or ``outside``."""
        in_hunk = self.old > 0 or self.new > 0
        if hunk := _HUNK_RE.match(line):
            self.old = int(hunk.group("old") or 1)
            self.new = int(hunk.group("new") or 1)
            self.seen = True
            return "hunk"
        if not in_hunk:
            return "outside"
        if line.startswith(" ") or line == "":
            self.old -= 1
            self.new -= 1
            return "context"
        if line.startswith("-"):
            self.old -= 1
            return "removed"
        if line.startswith("+"):
            self.new -= 1
            return "added"
        return "outside"


def _split_file_blocks(output: str) -> tuple[list[str], list[_FileBlock]]:
    """The lines before the first file header, and one block per file."""
    preamble: list[str] = []
    blocks: list[_FileBlock] = []
    current: _FileBlock | None = None
    cursor = _HunkCursor()
    for line in output.splitlines():
        if m := _FILE_HEADER_RE.match(line):
            current = _FileBlock(m.group("b"))
            blocks.append(current)
            cursor = _HunkCursor()
        if current is None:
            preamble.append(line)
        else:
            _place(current, cursor, line)
    return preamble, blocks


def _place(block: _FileBlock, cursor: _HunkCursor, line: str) -> None:
    """Add *line* to *block*: its hunks and headers, or the tail after them."""
    if block.tail:
        block.tail.append(line)
        return
    kind = cursor.classify(line)
    if kind == "added":
        block.added += 1
    elif kind == "removed":
        block.deleted += 1
    elif kind == "outside" and cursor.seen and not line.startswith("\\"):
        block.tail.append(line)
        return
    block.lines.append(line)


def _size(lines: list[str]) -> int:
    return sum(len(line) + 1 for line in lines)


def _fold_block(block: _FileBlock) -> list[str]:
    """One file block with its context runs and redundant header lines folded."""
    if block.generated:
        return [block.lines[0], f"  ... lockfile or generated: +{block.added} -{block.deleted}"]
    out: list[str] = []
    context: list[str] = []
    cursor = _HunkCursor()
    for line in block.lines:
        kind = cursor.classify(line)
        if kind == "context":
            context.append(line)
            continue
        _flush_context(context, out)
        if kind == "outside" and not cursor.seen and _REDUNDANT_HEADER_RE.match(line):
            continue
        out.append(line)
    _flush_context(context, out)
    return out


def _flush_context(context: list[str], out: list[str]) -> None:
    """Fold a run of unchanged lines to one line; a single line stays as it is."""
    if len(context) == 1:
        out.append(context[0])
    elif context:
        out.append(f"  ... {len(context)} unchanged lines")
    context.clear()


def _cap_to(lines: list[str], budget: int) -> list[str]:
    """*lines* capped head and tail to about *budget* chars.

    Error lines from git itself (``fatal: ...``) are kept. Diff body lines are
    code, where "error" or "assert" is an identifier rather than a failure, so
    they are not.
    """
    if _size(lines) <= budget:
        return lines
    head = _take(lines, budget // 2)
    tail = _take(lines[len(head) :][::-1], budget // 2)[::-1]
    middle = lines[len(head) : len(lines) - len(tail)]
    errors = [ln for ln in middle if ln[:1] not in "+- @" and is_error_line(ln)]
    return [*head, *errors, f"  ... ({len(middle) - len(errors)} lines elided) ...", *tail]


def _take(lines: list[str], budget: int) -> list[str]:
    """The leading *lines* that fit in *budget* chars."""
    taken: list[str] = []
    used = 0
    for line in lines:
        used += len(line) + 1
        if used > budget:
            break
        taken.append(line)
    return taken


def _fit(rendered: list[tuple[_FileBlock, list[str]]], budget: int) -> list[str]:
    """Hunks for the files with the most change that fit *budget*; counts for the rest.

    Files keep their original order. Every file starts as its one-line count
    and is upgraded to its hunks, most change first, wherever the whole still
    fits. When the file with the most change is over the budget on its own it
    is capped head and tail first: on a month of agent diffs that kept more
    changed lines than skipping it for smaller files.
    """
    ranked = sorted(rendered, key=lambda item: item[0].added + item[0].deleted, reverse=True)
    counted = {id(b) for b, _ in ranked[:MAX_SUMMARY_ROWS] if not b.generated}
    kept = _choose_kept(rendered, ranked, counted, budget)
    out: list[str] = []
    summarized: list[str] = []
    for block, folded in rendered:
        if id(block) in kept:
            out.extend(kept[id(block)])
        elif block.generated:
            out.extend(folded)
        elif id(block) in counted:
            summarized.append(_count_row(block))
        out.extend(block.tail)
    hidden = sum(1 for b, _ in rendered if not b.generated and id(b) not in kept) - len(summarized)
    return out + _omitted_rows(summarized, hidden)


def _omitted_rows(summarized: list[str], hidden: int) -> list[str]:
    """The count rows for files shown without hunks, and how many were not listed."""
    if not summarized and not hidden:
        return []
    rows = [f"hunks omitted for {len(summarized) + hidden} files:", *summarized]
    if hidden:
        rows.append(f"  ... and {hidden} more")
    return rows


def _count_row(block: _FileBlock) -> str:
    return f"  {block.path} | +{block.added} -{block.deleted}"


def _choose_kept(
    rendered: list[tuple[_FileBlock, list[str]]],
    ranked: list[tuple[_FileBlock, list[str]]],
    counted: set[int],
    budget: int,
) -> dict[int, list[str]]:
    """The files whose folded hunks are shown, keyed by block id."""
    used = _size(["hunks omitted for 0000 files:", "  ... and 0000 more"])
    used += sum(len(_count_row(b)) + 1 for b, _ in rendered if id(b) in counted)
    used += sum(_size(folded) for b, folded in rendered if b.generated)
    used += sum(_size(b.tail) for b, _ in rendered)
    kept: dict[int, list[str]] = {}
    for block, folded in ranked:
        if block.generated:
            continue
        refund = len(_count_row(block)) + 1 if id(block) in counted else 0
        if used + _size(folded) - refund > budget:
            if kept:
                continue
            folded = _cap_to(folded, max(0, budget - used + refund))
        kept[id(block)] = folded
        used += _size(folded) - refund
    return kept
