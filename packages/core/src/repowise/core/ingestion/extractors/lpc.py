"""LDMud declaration visibility."""

from __future__ import annotations


def lpc_visibility(_name: str, modifiers: list[str]) -> str:
    """Map LPC access modifiers onto the shared symbol visibility vocabulary."""
    words = set(" ".join(modifiers).split())
    if "private" in words:
        return "private"
    if words & {"static", "protected"}:
        return "protected"
    return "public"
