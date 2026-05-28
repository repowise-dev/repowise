"""Java record component-accessor synthesis.

A ``record Foo(T1 a, T2 b) {}`` declaration automatically synthesises:
  - a canonical constructor ``Foo(T1 a, T2 b)``
  - one accessor per component: ``a()``, ``b()``
  - ``equals(Object)``, ``hashCode()``, ``toString()``

The parser already emits a Symbol for the record itself, but the
component accessors and canonical constructor never appear in the AST —
so calls to ``point.x()`` resolve to nothing and the components read as
unused fields. This provider emits the missing names.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models import FileInfo, Symbol
from ..helpers import node_text
from ._helpers import build_synthetic_symbol

if TYPE_CHECKING:
    from tree_sitter import Node


def _record_components(record_node: "Node", src: str) -> list[tuple[str, str]]:
    """Return [(name, type_text), ...] for each component of a record header."""
    params = record_node.child_by_field_name("parameters")
    if params is None:
        for child in record_node.children:
            if child.type == "formal_parameters":
                params = child
                break
    if params is None:
        return []
    out: list[tuple[str, str]] = []
    for child in params.children:
        if child.type != "formal_parameter":
            continue
        type_text = ""
        name: str | None = None
        for sub in child.children:
            if sub.type == "identifier" and name is None:
                name = node_text(sub, src).strip()
            elif sub.type not in ("modifiers", ",", "(", ")"):
                if not type_text:
                    type_text = node_text(sub, src).strip()
        if name:
            out.append((name, type_text))
    return out


def java_record_synthetic_symbols(
    root: "Node", src: str, file_info: FileInfo
) -> list[Symbol]:
    """Emit canonical constructor + accessors + equals/hashCode/toString."""
    if "record" not in src:
        return []

    out: list[Symbol] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type != "record_declaration":
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        record_name = node_text(name_node, src).strip()
        if not record_name:
            continue
        line = node.start_point[0] + 1
        components = _record_components(node, src)

        # Canonical constructor (same name as the record).
        out.append(build_synthetic_symbol(
            name=record_name, kind="function",
            signature=f"public {record_name}("
                      + ", ".join(f"{t} {n}" for n, t in components)
                      + ")",
            start_line=line, end_line=line,
            file_info=file_info, parent_name=record_name,
        ))

        # Component accessors — same name as the component, zero args.
        for comp_name, comp_type in components:
            out.append(build_synthetic_symbol(
                name=comp_name, kind="method",
                signature=f"public {comp_type or 'Object'} {comp_name}()",
                start_line=line, end_line=line,
                file_info=file_info, parent_name=record_name,
            ))

        # Object-contract methods every record gets.
        out.append(build_synthetic_symbol(
            name="equals", kind="method",
            signature="public boolean equals(Object)",
            start_line=line, end_line=line,
            file_info=file_info, parent_name=record_name,
        ))
        out.append(build_synthetic_symbol(
            name="hashCode", kind="method",
            signature="public int hashCode()",
            start_line=line, end_line=line,
            file_info=file_info, parent_name=record_name,
        ))
        out.append(build_synthetic_symbol(
            name="toString", kind="method",
            signature="public String toString()",
            start_line=line, end_line=line,
            file_info=file_info, parent_name=record_name,
        ))
    return out
