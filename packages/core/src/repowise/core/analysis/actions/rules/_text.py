"""Small copy helpers shared by the rules."""

from __future__ import annotations


def code(text: str) -> str:
    """Mark a path or symbol for a mono renderer."""
    return f"`{text}`"


def plural(n: int, noun: str) -> str:
    return f"{n:,} {noun}" if n == 1 else f"{n:,} {noun}s"


def humanize(token: str) -> str:
    return token.replace("_", " ")


def py_list(items: list[str]) -> str:
    """A list literal for an MCP call line: ``["a.py", "b.py"]``."""
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"
