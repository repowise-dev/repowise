"""Inline decision markers (``# WHY:``, ``# DECISION:`` ...) in source text."""

from __future__ import annotations

import re

# The keyword is deliberately case-SENSITIVE. These are annotation
# conventions, written in caps like TODO:/FIXME:/HACK:, and matching them
# case-insensitively turns ordinary prose into architectural decisions. Two
# real examples from this repo, both of which reached the store as `active`
# records: a wrapped sentence whose continuation line began "# decision:
# namespace, batched like the pages", and a test's "# Rejected: nothing to
# extract." Across 3,860 tracked files those were the ONLY two matches — a
# 100% false-positive rate — because no genuine marker was written in lower
# case. A missed marker costs one record; a false positive publishes a
# sentence fragment as a decision governing every file it touches.
MARKER_RE = re.compile(
    r"^\s*(?:#|//|--|/\*|\*)\s*"
    r"(?P<keyword>WHY|DECISION|TRADEOFF|ADR|RATIONALE|REJECTED)"
    r"\s*:\s*(?P<text>.+)",
)

# Regex to detect fenced code blocks in markdown files (``` or ~~~).
_CODE_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks from markdown to avoid parsing examples."""
    lines = text.splitlines()
    out: list[str] = []
    in_fence = False
    for line in lines:
        if _CODE_FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)
