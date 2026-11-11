"""Inline allowlist and ignore markers that silence findings."""

from __future__ import annotations

import re

from .masking import mask_comments_and_strings, mask_dialect
from .paths import _PROSE_EXTENSIONS

# The inline markers other secret scanners honour, in a comment on the
# finding's line: detect-secrets, gitleaks, TruffleHog, Bandit and gosec
# ``nosec``, and Ruff's hard-coded-password codes. detect-secrets also has a
# marker for the line below.
_SAME_MARKER = (
    r"(?:pragma:[ \t]*allowlist[ \t]+secret|gitleaks:allow|trufflehog:ignore"
    r"|nosec|noqa:[ \t\w,]*\bS10[5-7])\b"
)
_NEXT_MARKER = r"pragma:[ \t]*allowlist[ \t]+nextline[ \t]+secret\b"
# Code: a marker only counts inside a real comment, so it needs an opener.
_COMMENT_OPENER = r"(?:#|//|/\*)[ \t]*"
# Prose (Markdown, reStructuredText, plain text) has no single comment syntax, so a
# marker counts anywhere on its line, as long as it is not the tail of a longer word.
_PROSE_LEAD = r"(?<![\w-])"
_ALLOWLIST_SAME_RE = re.compile(_COMMENT_OPENER + _SAME_MARKER)
_ALLOWLIST_NEXT_RE = re.compile(_COMMENT_OPENER + _NEXT_MARKER)
_ALLOWLIST_SAME_PROSE_RE = re.compile(_PROSE_LEAD + _SAME_MARKER)
_ALLOWLIST_NEXT_PROSE_RE = re.compile(_PROSE_LEAD + _NEXT_MARKER)
_ALLOWLIST_TOKENS = ("allowlist", "gitleaks:allow", "trufflehog:ignore", "nosec", "noqa:")


def _allowlisted_lines(file_path: str, source: str) -> set[int]:
    """Line numbers an allow marker in a comment (not a string) covers.

    Prose has no comments to set apart, so a marker anywhere on its line counts.
    """
    if not any(token in source for token in _ALLOWLIST_TOKENS):
        return set()
    uncommented = (
        None
        if file_path.lower().endswith(_PROSE_EXTENSIONS)
        else mask_comments_and_strings(source, strings=False, **mask_dialect(file_path))
    )
    patterns = (
        ((_ALLOWLIST_SAME_RE, 0), (_ALLOWLIST_NEXT_RE, 1))
        if uncommented is not None
        else ((_ALLOWLIST_SAME_PROSE_RE, 0), (_ALLOWLIST_NEXT_PROSE_RE, 1))
    )
    allowed: set[int] = set()
    for pattern, below in patterns:
        for m in pattern.finditer(source):
            if uncommented is None or uncommented[m.start() : m.end()] != m.group():
                allowed.add(source.count("\n", 0, m.start()) + 1 + below)
    return allowed


IGNORE_TOKEN = "repowise-security-ignore"
_IGNORE_RE = re.compile(
    rf"{IGNORE_TOKEN}(?![\w-])"
    r"(?:[ \t]*:[ \t]*([\w:-]+(?:[ \t]*,[ \t]*[\w:-]+)*))?"
    # A colon no kind list follows is a malformed scope, which silences nothing.
    r"(?P<stray>[ \t]*:)?"
)


def marker_silences(line: str, kind: str) -> bool:
    """Whether a marker on *line* silences *kind*: the bare token all kinds, ``: a, b`` those."""
    if IGNORE_TOKEN not in line:
        return False
    match = _IGNORE_RE.search(line)
    if match is None or match.group("stray"):
        return False
    if not match.group(1):
        return True
    return kind in {k.strip() for k in match.group(1).split(",")}
