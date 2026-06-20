"""Mermaid C4 emitters for L1 / L2 / L3 views.

Consumes the same ``C4L1`` / ``C4L2`` / ``C4L3`` dataclasses the API serves,
so there's a single source of truth for what a container or component is.

Mermaid's C4 plugin syntax: https://mermaid.js.org/syntax/c4.html
"""

from __future__ import annotations

import re
from collections import defaultdict

from .models import C4L1, C4L2, C4L3, Container, ExternalSystemView

_SAFE = re.compile(r"[^a-zA-Z0-9_]")

# Beyond this many external systems the L1/L2 diagram groups them into labelled
# category boundaries ("Frameworks", "Services & Infrastructure", …) instead of
# rendering N loose boxes, so the context view stays legible (plan §Phase 5).
_EXTERNAL_GROUP_THRESHOLD = 8
_CATEGORY_TITLES: dict[str, str] = {
    "framework": "Frameworks",
    "service": "Services & Infrastructure",
    "tool": "Tools",
    "library": "Libraries",
}
_CATEGORY_ORDER = ("framework", "service", "tool", "library")


def _sid(node_id: str) -> str:
    """Mermaid identifiers must be alnum/underscore."""
    return _SAFE.sub("_", node_id)


def _q(text: str) -> str:
    """Quote a label for Mermaid — escape embedded quotes."""
    return text.replace('"', "'")


# Boundary type to Mermaid C4 element. ``SystemDb_Ext`` renders with the
# cylinder glyph so a db dependency reads as a database rather than a generic
# box; the other kinds fall back to standard external boxes.
_IO_KIND_ELEMENT: dict[str, str] = {
    "db": "SystemDb_Ext",
    "network": "System_Ext",
    "filesystem": "Container_Ext",
    "subprocess": "Container_Ext",
    "lock": "Container_Ext",
}


def _ext_kind(cat: str, io_kind: str | None = None) -> str:
    """Map a dependency to a Mermaid C4 element type.

    Prefer the I/O boundary type when known (a db dep renders as a database
    cylinder); fall back to the coarse category when ``io_kind`` is None so
    untyped dependencies keep today's behaviour.
    """
    if io_kind is not None and io_kind in _IO_KIND_ELEMENT:
        return _IO_KIND_ELEMENT[io_kind]
    if cat == "service":
        return "System_Ext"
    return "Container_Ext"


def to_mermaid_l1(view: C4L1) -> str:
    lines: list[str] = ["C4Context", f'    title System Context — {_q(view.system.name)}', ""]

    for person in view.people:
        lines.append(
            f'    Person({_sid(person.id)}, "{_q(person.name)}", "{_q(person.description)}")'
        )

    lines.append(
        f'    System({_sid(view.system.id)}, "{_q(view.system.name)}", '
        f'"{_q(view.system.description or "System under analysis")}")'
    )

    lines.extend(_emit_externals(view.external_systems))

    if view.relations:
        lines.append("")
    for rel in view.relations:
        lines.append(_rel_line(rel.source_id, rel.target_id, rel.label))

    return "\n".join(lines) + "\n"


def to_mermaid_l2(view: C4L2, system_name: str) -> str:
    lines: list[str] = ["C4Container", f'    title Containers — {_q(system_name)}', ""]
    lines.append(f'    System_Boundary(sys, "{_q(system_name)}") {{')
    for c in view.containers:
        lines.append(_container_line(c, indent="        "))
    lines.append("    }")

    lines.extend(_emit_externals(view.external_systems))

    if view.relations:
        lines.append("")
    for rel in view.relations:
        lines.append(_rel_line(rel.source_id, rel.target_id, rel.label))

    return "\n".join(lines) + "\n"


def to_mermaid_l3(view: C4L3, system_name: str) -> str:
    lines: list[str] = [
        "C4Component",
        f'    title Components — {_q(view.container.name)} ({_q(system_name)})',
        "",
    ]
    lines.append(f'    Container_Boundary({_sid(view.container.id)}, "{_q(view.container.name)}") {{')
    for cmp in view.components:
        lines.append(
            f'        Component({_sid(cmp.id)}, "{_q(cmp.name)}", '
            f'"{cmp.file_count} files · {cmp.symbol_count} symbols", "{_q(cmp.path)}")'
        )
    lines.append("    }")

    for ext in view.external_systems:
        lines.append(_external_line(ext))

    if view.relations:
        lines.append("")
    for rel in view.relations:
        lines.append(_rel_line(rel.source_id, rel.target_id, rel.label))

    return "\n".join(lines) + "\n"


def _container_line(c: Container, indent: str = "    ") -> str:
    desc = f"{c.file_count} files · {c.symbol_count} symbols"
    return (
        f'{indent}Container({_sid(c.id)}, "{_q(c.name)}", '
        f'"{_q(c.language)}", "{_q(desc)}")'
    )


def _emit_externals(externals: list[ExternalSystemView]) -> list[str]:
    """Render external systems, grouping by category once there are many.

    Below the threshold they stay as flat boxes (today's behaviour). Above it,
    each non-empty category is wrapped in a labelled ``Boundary`` so the diagram
    reads as a handful of buckets rather than a wall of dependency boxes.
    """
    if len(externals) <= _EXTERNAL_GROUP_THRESHOLD:
        return [_external_line(ext) for ext in externals]

    by_cat: dict[str, list[ExternalSystemView]] = defaultdict(list)
    for ext in externals:
        by_cat[ext.category].append(ext)

    ordered = [c for c in _CATEGORY_ORDER if c in by_cat]
    ordered += sorted(c for c in by_cat if c not in _CATEGORY_ORDER)

    lines: list[str] = []
    for cat in ordered:
        title = _CATEGORY_TITLES.get(cat, f"{cat.title()}s")
        lines.append(f'    Boundary(extgrp_{_sid(cat)}, "{_q(title)}") {{')
        for ext in sorted(by_cat[cat], key=lambda e: e.name):
            lines.append("    " + _external_line(ext))
        lines.append("    }")
    return lines


def _external_line(ext: ExternalSystemView) -> str:
    kind = _ext_kind(ext.category, ext.io_kind)
    version = f" {ext.version}" if ext.version else ""
    return (
        f'    {kind}({_sid(ext.id)}, "{_q(ext.display_name)}", '
        f'"{_q(ext.ecosystem + version)}")'
    )


def _rel_line(source: str, target: str, label: str) -> str:
    return f'    Rel({_sid(source)}, {_sid(target)}, "{_q(label or "uses")}")'
