"""Inline decision markers (``# WHY:``, ``# DECISION:`` ...) in source text."""

from __future__ import annotations

import re
from pathlib import Path

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


def find_markers(rel_path: str, text: str) -> list[dict]:
    """Every decision marker in one file, with its continuation and context.

    Markers inside fenced code blocks of a markdown file are examples, not
    decisions, and are skipped.
    """
    lines = text.splitlines()
    # Track whether we're inside a fenced code block in markdown
    # files so we don't treat example markers as real decisions.
    is_markdown = Path(rel_path).suffix.lower() in (".md", ".mdx", ".rst")
    in_code_fence = False
    found: list[dict] = []
    for line_num, line in enumerate(lines, start=1):
        if is_markdown:
            if _CODE_FENCE_RE.match(line):
                in_code_fence = not in_code_fence
                continue
            if in_code_fence:
                continue
        m = MARKER_RE.match(line)
        if not m:
            continue
        # Context window: ±20 lines
        ctx_start = max(0, line_num - 21)
        ctx_end = min(len(lines), line_num + 20)
        found.append(
            {
                "keyword": m.group("keyword"),
                "text": _with_continuation(m.group("text").strip(), lines[line_num : line_num + 5]),
                "line": line_num,
                "context": "\n".join(lines[ctx_start:ctx_end]),
            }
        )
    return found


def _with_continuation(marker_text: str, following: list[str]) -> str:
    """Append continuation lines (same comment prefix, no keyword) to a marker."""
    for cont_line in following:
        cont = cont_line.strip()
        if not (cont.startswith(("#", "//", "--", "*")) and ":" not in cont[:20]):
            break
        # Strip comment prefix
        cleaned = re.sub(r"^\s*(?:#|//|--|/\*|\*)\s*", "", cont)
        if cleaned:
            marker_text += " " + cleaned
    return marker_text
