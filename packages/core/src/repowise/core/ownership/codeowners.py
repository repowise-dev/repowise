"""CODEOWNERS parsing and matching, shared by every reader of the file.

GitHub reads ``.github/``, the repository root and ``docs/``; GitLab reads the
root, ``docs/`` and ``.gitlab/``. The first file found is the one in force.

GitHub gives a file to the last pattern matching it. GitLab adds sections:
``[Name]``, optionally ``^[Name]`` (optional approval) and ``[Name][2]``
(approval count), each with optional default owners that owner-less lines in
the section take. Each section picks its own last match and a file's owners
are the union across sections; sections with the same name (any case) are
one section. Rules before the first header form the default section.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pathspec
from pathspec.patterns.gitwildmatch import GitWildMatchPatternError

#: Where GitHub and GitLab look for CODEOWNERS; the first found is read.
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS")

# A ``#`` at the start of a line or after whitespace (space or tab) opens a comment.
_COMMENT = re.compile(r"(?:^|\s)#.*$")
# Fields split on whitespace a backslash does not escape (``docs/my\ file.md``).
_FIELDS = re.compile(r"(?<!\\)\s+")
# A GitLab section header, ``[Name]`` or ``^[Name][2]``, with optional default owners.
_SECTION = re.compile(r"^\^?\[([^\]]+)\](?:\[\d+\])?(?:\s+(.*))?$")


@dataclass(frozen=True)
class CodeownersRule:
    pattern: str
    #: The line's owners, or its GitLab section's default owners when it names none.
    owners: tuple[str, ...]
    #: The GitLab section, lower-cased; ``None`` for rules before any header.
    section: str | None = None


def parse_codeowners(text: str) -> list[CodeownersRule]:
    """Every rule line in file order."""
    rules: list[CodeownersRule] = []
    section: str | None = None
    defaults: list[str] = []
    for raw in text.splitlines():
        line = _COMMENT.sub("", raw).strip()
        if not line:
            continue
        if header := _SECTION.match(line):
            section = header.group(1).strip().lower()
            defaults = _FIELDS.split(header.group(2)) if header.group(2) else []
            continue
        pattern, *owners = _FIELDS.split(line)
        rules.append(CodeownersRule(pattern, tuple(owners or defaults), section))
    return rules


def read_codeowners(repo_root: Path | str) -> tuple[str, str] | None:
    """``(repo-relative path, text)`` of the CODEOWNERS file in force, else ``None``."""
    root = Path(repo_root)
    for rel in CODEOWNERS_PATHS:
        path = root / rel
        if path.is_file():
            try:
                return rel, path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
    return None


class CodeownersMatcher:
    """Owners of a path under one parsed file; patterns compile once."""

    def __init__(self, rules: Iterable[CodeownersRule]):
        self._rules: list[tuple[pathspec.PathSpec, CodeownersRule]] = []
        for rule in rules:
            try:
                spec = pathspec.PathSpec.from_lines("gitwildmatch", [rule.pattern])
            except (GitWildMatchPatternError, ValueError):
                continue  # a pattern git cannot read owns nothing
            self._rules.append((spec, rule))

    def owners(self, path: str) -> tuple[str, ...]:
        """Union of each section's last matching rule's owners, in first-seen order."""
        last: dict[str | None, CodeownersRule] = {}
        for spec, rule in self._rules:
            if spec.match_file(path):
                last[rule.section] = rule
        return tuple(dict.fromkeys(o for rule in last.values() for o in rule.owners))


__all__ = [
    "CODEOWNERS_PATHS",
    "CodeownersMatcher",
    "CodeownersRule",
    "parse_codeowners",
    "read_codeowners",
]
