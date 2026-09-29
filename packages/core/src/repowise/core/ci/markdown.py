"""Markdown pieces shared by CI step summaries and pull-request comments."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

#: Items listed in a section before the rest collapse into "and N more".
ROW_LIMIT = 10


def cell(value: Any) -> str:
    """*value* made safe for a markdown table cell."""
    text = str(value)
    for char, escaped in (("\\", "\\\\"), ("|", "\\|"), ("`", "\\`"), ("<", "&lt;")):
        text = text.replace(char, escaped)
    return text


def longest_backtick_run(text: str) -> int:
    """Length of the longest run of backticks in *text*, for choosing a fence."""
    return max((len(run) for run in re.findall(r"`+", text)), default=0)


def code(text: str) -> str:
    """*text* as an inline code span that no backtick inside it can close early."""
    fence = "`" * (longest_backtick_run(text) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def plural(n: int, word: str, many: str | None = None) -> str:
    """``"1 file"`` / ``"3 files"``."""
    return f"{n} {word if n == 1 else many or word + 's'}"


def more_line(n: int, noun: str) -> str:
    """``"and 4 more files."``, or ``""`` when nothing was cut."""
    return f"and {n} more {noun}." if n > 0 else ""


def details(summary: str, items: Sequence[str], limit: int = ROW_LIMIT) -> list[str]:
    """A collapsed ``<details>`` list of *items* (already formatted), capped at *limit*."""
    if not items:
        return []
    out = ["<details>", f"<summary>{summary}</summary>", "", *(f"- {i}" for i in items[:limit])]
    if len(items) > limit:
        out.append(f"- and {len(items) - limit} more")
    return [*out, "", "</details>"]
