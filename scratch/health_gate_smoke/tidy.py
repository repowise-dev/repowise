"""Smoke-test fixture: a clean, healthy module (human-authored side).

Part of a throwaway PR to exercise the Repowise bot health gate. Safe to
delete; not imported anywhere.
"""

from __future__ import annotations


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def greet(name: str) -> str:
    """Return a friendly greeting."""
    return f"Hello, {name}!"
