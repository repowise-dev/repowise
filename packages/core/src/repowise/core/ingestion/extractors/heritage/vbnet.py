"""VB.NET heritage extraction — regex fallback over class-body text.

tree-sitter-vbnet 0.1.0 fails to parse `Inherits` / `Implements` clauses:
they land in an ERROR node instead of a structured base-list (see
docs/architecture/vbnet-support.md D3). This module recovers the
relationship by scanning the type body's raw source text rather than the
AST — a documented stopgap, not a permanent design, tracked for removal
once the grammar parses these clauses natively.
"""

from __future__ import annotations

import re

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text

_INHERITS_RE = re.compile(r"^Inherits\s+(.+)$", re.IGNORECASE)
_IMPLEMENTS_RE = re.compile(r"^Implements\s+(.+)$", re.IGNORECASE)


def _clean_type_name(raw: str) -> str:
    """Strip a `(Of ...)` generic-argument clause and namespace qualification."""
    text = raw.strip()
    paren_idx = text.find("(")
    if paren_idx != -1:
        text = text[:paren_idx].strip()
    return text.rsplit(".", 1)[-1].strip()


def _split_top_level_commas(text: str) -> list[str]:
    """Split on commas outside `(...)` — keeps `IDict(Of String, Integer)` intact."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _extract_vbnet_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """VB.NET: `Inherits Base` / `Implements IFoo, IBar` via text scan.

    `Inherits` / `Implements` are always the first statements in a VB type
    body (before any member or nested type), so the scan starts right
    after the header line and stops at the first line that is not blank,
    a comment, `Inherits`, or `Implements`. Stopping there is what keeps
    this scan from descending into a nested type's own heritage clauses,
    and from misreading a member's explicit-interface-implementation
    clause (`Public Sub Foo() Implements IBar.Foo` doesn't start with
    `Implements`, so it isn't a scan target and isn't reached — a real
    Inherits/Implements clause always precedes the first member anyway).

    Unlike C#'s single colon-delimited base list, VB's two distinct
    keywords say directly which relationship a base name is — no
    interface-naming-convention heuristic needed. `Inherits` supports
    multiple comma-separated targets only for interface-to-interface
    inheritance (a class has at most one); both keywords' targets are
    comma-split the same way.
    """
    text = node_text(def_node, src)
    lines = text.splitlines()[1:]  # skip the "Class Foo" / "Interface IFoo" header line

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("'"):
            continue

        m = _INHERITS_RE.match(stripped)
        if m:
            for part in _split_top_level_commas(m.group(1)):
                parent = _clean_type_name(part)
                if parent and parent != name:
                    out.append(
                        HeritageRelation(
                            child_name=name, parent_name=parent, kind="extends", line=line
                        )
                    )
            continue

        m = _IMPLEMENTS_RE.match(stripped)
        if m:
            for part in _split_top_level_commas(m.group(1)):
                parent = _clean_type_name(part)
                if parent and parent != name:
                    out.append(
                        HeritageRelation(
                            child_name=name, parent_name=parent, kind="implements", line=line
                        )
                    )
            continue

        break
