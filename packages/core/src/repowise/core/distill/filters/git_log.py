"""Compact rendering of ``git log`` output: subject and author, capped count.

Full-format logs (commit/Author/Date/body) collapse to one line per commit;
already-oneline logs are simply capped. Commit bodies rarely matter for
orientation, and when they do the marker round-trips the full log.
"""

from __future__ import annotations

import re
from typing import ClassVar

from repowise.core.distill.filters.base import OutputFilter
from repowise.core.distill.registry import filter_registry

_COMMAND_RE = re.compile(r"^git(?:\.exe)? log\b")
_COMMIT_RE = re.compile(r"^commit (?P<sha>[0-9a-f]{7,40})")
_ONELINE_RE = re.compile(r"^[0-9a-f]{7,12} \S")
_AUTHOR_RE = re.compile(r"^Author:\s+(?P<name>.*?)\s*(?:<[^>]*>)?\s*$")
_DATE_RE = re.compile(r"^Date:\s+(?P<date>.+)$")
# ``-n 60``, ``-n60``, ``--max-count=60``, ``--max-count 60`` or ``-60``.
_COUNT_RE = re.compile(r"(?:^|\s)(?:-n\s*|--max-count[=\s]|-)(?P<n>\d+)(?=\s|$)")

#: Most-recent subjects kept in the compact rendering.
MAX_COMMITS = 20
#: Cap for logs that are already oneline-formatted.
MAX_ONELINE = 40
#: Ceiling on an explicit ``-n``. 100 commit lines are about 10 KB, a third of
#: the 30,000 characters a host delivers per tool result, so an explicit count
#: is honoured without the compact view crowding out the rest of the result.
MAX_REQUESTED = 100


@filter_registry.register
class GitLogFilter(OutputFilter):
    name: ClassVar[str] = "git_log"
    priority: ClassVar[int] = 10
    min_lines: ClassVar[int] = 12

    def matches_command(self, command: str) -> bool:
        return bool(_COMMAND_RE.match(command))

    def matches_content(self, output: str) -> bool:
        lines = output.splitlines()
        if not lines:
            return False
        commit_lines = sum(1 for ln in lines[:200] if _COMMIT_RE.match(ln))
        return commit_lines >= 3 and "Author:" in output

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        lines = output.splitlines()
        commits = _parse_full_format(lines)
        if commits:
            return _render(commits, _limit(command, MAX_COMMITS))
        oneline = [ln for ln in lines if _ONELINE_RE.match(ln)]
        if len(oneline) >= self.min_lines:
            kept = oneline[: _limit(command, MAX_ONELINE)]
            return "\n".join([_header(len(oneline), len(kept), "oneline"), *kept])
        raise ValueError("not a recognizable git log format")


def _limit(command: str, default: int) -> int:
    """Commits to keep: an explicit ``-n`` up to the ceiling, else *default*."""
    counts = _COUNT_RE.findall(command)
    # git honours the last count given.
    return min(int(counts[-1]), MAX_REQUESTED) if counts else default


def _header(total: int, kept: int, shape: str) -> str:
    if kept < total:
        return f"{total} commits (showing {kept} most recent; {shape})"
    return f"{total} commits ({shape})"


def _parse_full_format(lines: list[str]) -> list[tuple[str, str, str, str]]:
    """Extract (sha, date, author, subject) from full-format git log."""
    commits: list[tuple[str, str, str, str]] = []
    sha = date = author = ""
    subject: str | None = None
    for line in lines:
        if m := _COMMIT_RE.match(line):
            if sha:
                commits.append((sha, date, author, subject or ""))
            sha, date, author, subject = m.group("sha"), "", "", None
        elif m := _AUTHOR_RE.match(line):
            author = m.group("name")
        elif m := _DATE_RE.match(line):
            date = _short_date(m.group("date"))
        elif sha and subject is None and line.startswith("    ") and line.strip():
            subject = line.strip()
    if sha:
        commits.append((sha, date, author, subject or ""))
    return commits


def _short_date(date: str) -> str:
    """'Thu Jun 5 10:12:01 2026 +0530' -> 'Jun 5 2026' (best effort)."""
    parts = date.split()
    if len(parts) >= 5:
        return f"{parts[1]} {parts[2]} {parts[4]}"
    return date


def _render(commits: list[tuple[str, str, str, str]], limit: int) -> str:
    kept = commits[:limit]
    # Authors as one column; the name only, the email adds tokens and no answer.
    width = max((len(author) for _, _, author, _ in kept), default=0)
    body = [
        f"{sha[:8]}  {date:<12} {author:<{width}}  {subject}".rstrip()
        for sha, date, author, subject in kept
    ]
    return "\n".join([_header(len(commits), len(kept), "subject and author"), *body])
