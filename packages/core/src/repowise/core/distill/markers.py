"""Omission-marker rendering and parsing — ONE format everywhere.

Every distill surface (CLI executor, hooks, MCP truncation) renders dropped
content as the same marker so a single ``expand`` implementation can resolve
any ref it encounters. The marker is deliberately ASCII-only: distilled output
is echoed to consoles that may not be UTF-8 (Windows cp1252), and a marker
that crashes the terminal would violate the never-make-things-worse rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REF_LENGTH = 12

_MARKER_TEMPLATE = (
    "[repowise#{ref}: {lines} lines omitted (~{tokens} tokens); restore: repowise expand {ref}]"
)

#: Matches any marker and recovers its ref; the counts are optional groups so
#: one pattern serves callers that want only the ref and callers that want both.
MARKER_RE = re.compile(
    r"\[repowise#(?P<ref>[0-9a-f]{12}):"
    r"(?:\s*(?P<lines>\d+) lines omitted \(~(?P<tokens>\d+) tokens\))?"
    r"[^\]]*\]"
)
OMISSION_ID_RE = re.compile(r"^repowise#(?P<ref>[0-9a-f]{12})$")


@dataclass(frozen=True, slots=True)
class ParsedMarker:
    """One complete marker read back out of delivered text.

    ``text`` is the marker verbatim, so a reader reconstructing what an output
    cost before distillation can subtract the marker's own size.
    """

    ref: str
    text: str
    lines_omitted: int
    tokens_omitted: int


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


def parse_markers(text: str) -> list[ParsedMarker]:
    """Return every *complete* marker in *text*, in order, one per ref.

    Unlike :func:`parse_marker_refs` this requires both counts, which is also
    what keeps the documented example marker out: it prints a rounded
    ``~6.1k`` and is quoted into transcripts often enough to matter.
    """
    seen: set[str] = set()
    markers: list[ParsedMarker] = []
    for match in MARKER_RE.finditer(text):
        ref = match.group("ref")
        tokens = match.group("tokens")
        if tokens is None or ref in seen:
            continue
        seen.add(ref)
        markers.append(
            ParsedMarker(
                ref=ref,
                text=match.group(0),
                lines_omitted=int(match.group("lines")),
                tokens_omitted=int(tokens),
            )
        )
    return markers


def is_valid_ref(ref: str) -> bool:
    """True when *ref* looks like a store key (12 lowercase hex chars)."""
    return bool(re.fullmatch(r"[0-9a-f]{12}", ref))


def normalize_ref(value: str) -> str | None:
    """Return the bare store key from any public omission-reference shape."""

    candidate = value.strip()
    if is_valid_ref(candidate):
        return candidate
    direct = OMISSION_ID_RE.fullmatch(candidate)
    if direct:
        return direct.group("ref")
    marker = MARKER_RE.search(candidate)
    return marker.group("ref") if marker else None
