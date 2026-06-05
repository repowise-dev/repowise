"""Omission-marker rendering and parsing — ONE format everywhere.

Every distill surface (CLI executor, hooks, MCP truncation) renders dropped
content as the same marker so a single ``expand`` implementation can resolve
any ref it encounters. The marker is deliberately ASCII-only: distilled output
is echoed to consoles that may not be UTF-8 (Windows cp1252), and a marker
that crashes the terminal would violate the never-make-things-worse rule.
"""

from __future__ import annotations

import re

REF_LENGTH = 12

_MARKER_TEMPLATE = (
    "[repowise#{ref}: {lines} lines omitted (~{tokens} tokens); restore: repowise expand {ref}]"
)

MARKER_RE = re.compile(r"\[repowise#(?P<ref>[0-9a-f]{12}):[^\]]*\]")


def render_marker(ref: str, lines_omitted: int, tokens_omitted: int) -> str:
    """Render the omission marker for *ref*."""
    if not is_valid_ref(ref):
        raise ValueError(f"invalid omission ref: {ref!r}")
    return _MARKER_TEMPLATE.format(
        ref=ref, lines=max(lines_omitted, 0), tokens=max(tokens_omitted, 0)
    )


def parse_marker_refs(text: str) -> list[str]:
    """Return every omission ref embedded in *text*, in order, deduplicated."""
    seen: set[str] = set()
    refs: list[str] = []
    for match in MARKER_RE.finditer(text):
        ref = match.group("ref")
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def is_valid_ref(ref: str) -> bool:
    """True when *ref* looks like a store key (12 lowercase hex chars)."""
    return bool(re.fullmatch(r"[0-9a-f]{12}", ref))
