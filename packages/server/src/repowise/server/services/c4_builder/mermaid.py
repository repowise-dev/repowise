"""Mermaid C4 emitters for L1 / L2 / L3 views.

Consumes the same ``C4L1`` / ``C4L2`` / ``C4L3`` dataclasses the API serves,
so there's a single source of truth for what a container or component is.

Mermaid's C4 plugin syntax: https://mermaid.js.org/syntax/c4.html
"""

from __future__ import annotations

import re

from .models import C4L1, C4L2, C4L3, Container, ExternalSystemView


_SAFE = re.compile(r"[^a-zA-Z0-9_]")


def _sid(node_id: str) -> str:
    """Mermaid identifiers must be alnum/underscore."""
    return _SAFE.sub("_", node_id)


def _q(text: str) -> str:
    """Quote a label for Mermaid — escape embedded quotes."""
    return text.replace('"', "'")


def _ext_kind(cat: str) -> str:
    """Map our category to a Mermaid C4 element type."""
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

    for ext in view.external_systems:
        kind = _ext_kind(ext.category)
        version = f" {ext.version}" if ext.version else ""
        lines.append(
            f'    {kind}({_sid(ext.id)}, "{_q(ext.display_name)}", '
            f'"{_q(ext.ecosystem + version)}")'
        )

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

    for ext in view.external_systems:
        lines.append(_external_line(ext))

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


def _external_line(ext: ExternalSystemView) -> str:
    kind = _ext_kind(ext.category)
    version = f" {ext.version}" if ext.version else ""
    return (
        f'    {kind}({_sid(ext.id)}, "{_q(ext.display_name)}", '
        f'"{_q(ext.ecosystem + version)}")'
    )


def _rel_line(source: str, target: str, label: str) -> str:
    return f'    Rel({_sid(source)}, {_sid(target)}, "{_q(label or "uses")}")'
