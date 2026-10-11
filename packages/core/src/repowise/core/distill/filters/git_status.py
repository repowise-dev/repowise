"""Compact rendering of long-format ``git status`` output.

Turns the verbose hint-laden long format into a porcelain-style listing:
one branch line, one status-coded line per path, one totals line.
"""

from __future__ import annotations

import re
from typing import ClassVar

from repowise.core.distill.filters.base import OutputFilter
from repowise.core.distill.registry import filter_registry

_COMMAND_RE = re.compile(r"^git(?:\.exe)? status\b")

_BRANCH_RE = re.compile(r"^On branch (?P<branch>\S+)")
_DETACHED_RE = re.compile(r"^HEAD detached at (?P<at>\S+)")
_AHEAD_RE = re.compile(r"ahead of '(?P<remote>[^']+)' by (?P<n>\d+) commit")
_BEHIND_RE = re.compile(r"behind '(?P<remote>[^']+)' by (?P<n>\d+) commit")
_DIVERGED_RE = re.compile(r"have (?P<a>\d+) and (?P<b>\d+) different commits")
_UP_TO_DATE_RE = re.compile(r"up to date with '(?P<remote>[^']+)'")

_SECTIONS = {
    "Changes to be committed:": "staged",
    "Changes not staged for commit:": "unstaged",
    "Untracked files:": "untracked",
    "Unmerged paths:": "conflict",
}

_ENTRY_RE = re.compile(
    r"^(?:\t| {4,8})"
    r"(?:(?P<verb>modified|new file|deleted|renamed|copied|typechange|"
    r"both modified|both added|both deleted|added by us|added by them|"
    r"deleted by us|deleted by them):\s+)?"
    r"(?P<path>\S.*)$"
)

_CODES = {
    "modified": "M",
    "new file": "A",
    "deleted": "D",
    "renamed": "R",
    "copied": "C",
    "typechange": "T",
    "both modified": "UU",
    "both added": "AA",
    "both deleted": "DD",
    "added by us": "AU",
    "added by them": "UA",
    "deleted by us": "DU",
    "deleted by them": "UD",
}


@filter_registry.register
class GitStatusFilter(OutputFilter):
    name: ClassVar[str] = "git_status"
    priority: ClassVar[int] = 10
    min_lines: ClassVar[int] = 6

    def matches_command(self, command: str) -> bool:
        return bool(_COMMAND_RE.match(command)) and "--porcelain" not in command

    def matches_content(self, output: str) -> bool:
        head = output[:400]
        return ("On branch " in head or "HEAD detached at" in head) and (
            "git add" in output or "working tree clean" in output
        )

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        branch_line = _render_branch_line(output)
        entries, counts = _parse_entries(output)
        if branch_line is None and not entries:
            raise ValueError("not a recognizable git status long format")

        lines: list[str] = []
        if branch_line:
            lines.append(branch_line)
        if "working tree clean" in output:
            lines.append("clean: nothing to commit")
            return "\n".join(lines)
        lines.extend(entries)
        totals = ", ".join(f"{n} {section}" for section, n in counts.items() if n)
        if totals:
            lines.append(f"({totals})")
        return "\n".join(lines)


def _render_branch_line(output: str) -> str | None:
    branch = None
    m = _BRANCH_RE.search(output)
    if m:
        branch = m.group("branch")
    else:
        m = _DETACHED_RE.search(output)
        if m:
            branch = f"HEAD detached at {m.group('at')}"
    if branch is None:
        return None
    line = f"## {branch}"
    if m2 := _DIVERGED_RE.search(output):
        line += f" [ahead {m2.group('a')}, behind {m2.group('b')}]"
    elif m2 := _AHEAD_RE.search(output):
        line += f"...{m2.group('remote')} [ahead {m2.group('n')}]"
    elif m2 := _BEHIND_RE.search(output):
        line += f"...{m2.group('remote')} [behind {m2.group('n')}]"
    elif m2 := _UP_TO_DATE_RE.search(output):
        line += f"...{m2.group('remote')}"
    return line


def _parse_entries(output: str) -> tuple[list[str], dict[str, int]]:
    entries: list[str] = []
    counts: dict[str, int] = {}
    section: str | None = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped in _SECTIONS:
            section = _SECTIONS[stripped]
            continue
        if section is None:
            continue
        if stripped == "" or stripped.startswith("("):
            # Blank line or hint text ends/annotates a section; hints are
            # parenthesized ("use git add ..."), blank ends the block.
            if stripped == "":
                section = None
            continue
        m = _ENTRY_RE.match(line)
        if m is None:
            continue
        verb, path = m.group("verb"), m.group("path")
        if section == "untracked":
            entries.append(f"?? {path}")
        else:
            code = _CODES.get(verb or "modified", "M")
            # Porcelain-style column: staged in column 1, unstaged in column 2.
            prefix = f"{code} " if section in ("staged", "conflict") else f" {code}"
            entries.append(f"{prefix} {path}")
        counts[section] = counts.get(section, 0) + 1
    return entries, counts
