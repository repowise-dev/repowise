"""Small copy helpers shared by the rules."""

from __future__ import annotations

import re


def code(text: str) -> str:
    """Mark a path or symbol for a mono renderer.

    A C# generic id carries a backtick (``IFoo`1``), so the span is fenced with
    one more backtick than the longest run inside it.
    """
    if "`" not in text:
        return f"`{text}`"
    fence = "`" * (max(len(run) for run in re.findall(r"`+", text)) + 1)
    return f"{fence} {text} {fence}"


def plural(n: int, noun: str) -> str:
    return f"{n:,} {noun}" if n == 1 else f"{n:,} {noun}s"


def humanize(token: str) -> str:
    return token.replace("_", " ")


def py_list(items: list[str]) -> str:
    """A list literal for an MCP call line: ``["a.py", "b.py"]``."""
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"
