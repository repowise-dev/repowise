"""Compact rendering of ``git log`` output — subjects only, capped count.

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
_DATE_RE = re.compile(r"^Date:\s+(?P<date>.+)$")

#: Most-recent subjects kept in the compact rendering.
MAX_COMMITS = 20
#: Cap for logs that are already oneline-formatted.
MAX_ONELINE = 40


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
            return _render(commits)
        oneline = [ln for ln in lines if _ONELINE_RE.match(ln)]
        if len(oneline) >= self.min_lines:
            kept = oneline[:MAX_ONELINE]
            header = f"{len(oneline)} commits (showing {len(kept)} most recent)"
            return "\n".join([header, *kept])
        raise ValueError("not a recognizable git log format")


def _parse_full_format(lines: list[str]) -> list[tuple[str, str, str]]:
    """Extract (sha, date, subject) triples from full-format git log."""
    commits: list[tuple[str, str, str]] = []
    sha = date = ""
    subject: str | None = None
    for line in lines:
        if m := _COMMIT_RE.match(line):
            if sha:
                commits.append((sha, date, subject or ""))
            sha, date, subject = m.group("sha"), "", None
        elif m := _DATE_RE.match(line):
            date = _short_date(m.group("date"))
        elif sha and subject is None and line.startswith("    ") and line.strip():
            subject = line.strip()
    if sha:
        commits.append((sha, date, subject or ""))
    return commits


def _short_date(date: str) -> str:
    """'Thu Jun 5 10:12:01 2026 +0530' -> 'Jun 5 2026' (best effort)."""
    parts = date.split()
    if len(parts) >= 5:
        return f"{parts[1]} {parts[2]} {parts[4]}"
    return date


def _render(commits: list[tuple[str, str, str]]) -> str:
    kept = commits[:MAX_COMMITS]
    header = f"{len(commits)} commits (showing {len(kept)} most recent; subjects only)"
    body = [f"{sha[:8]}  {date:<12} {subject}".rstrip() for sha, date, subject in kept]
    return "\n".join([header, *body])
