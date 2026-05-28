"""CommunityToolkit.Mvvm source-generator synthesis.

  - ``[ObservableProperty] private string _name;`` → property ``Name``
  - ``[RelayCommand] private void Save() { … }``  → command ``SaveCommand``

Verbatim of the original ``synthetic_symbols.py`` C# pass — refactored
into this per-source module behind the ``__init__.py`` façade. No
behaviour change.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ...models import FileInfo, Symbol
from ..helpers import node_text
from ._helpers import build_synthetic_symbol, enclosing_type_name

if TYPE_CHECKING:
    from tree_sitter import Node


_OBSERVABLE_PROPERTY = "ObservableProperty"
_RELAY_COMMAND = "RelayCommand"

_FIELD_NAME_TO_PROP_RE = re.compile(r"^_?([A-Za-z])(.*)$")

_CSHARP_TYPE_ANCESTORS = frozenset(
    {"class_declaration", "struct_declaration", "record_declaration"}
)


def _pascal_from_field(field_name: str) -> str | None:
    """Convert ``_name`` / ``m_name`` / ``name`` → ``Name``."""
    stripped = field_name.lstrip("_")
    if stripped.startswith("m_"):
        stripped = stripped[2:]
    match = _FIELD_NAME_TO_PROP_RE.match(stripped)
    if not match:
        return None
    first, rest = match.groups()
    return first.upper() + rest


def _attribute_names(attr_list_node: "Node", src: str) -> set[str]:
    names: set[str] = set()
    for child in attr_list_node.children:
        if child.type != "attribute":
            continue
        for sub in child.children:
            if sub.type in ("identifier", "qualified_name"):
                text = node_text(sub, src).strip()
                names.add(text.split(".")[-1])
                break
    return names


def _has_attribute(node: "Node", attr_name: str, src: str) -> bool:
    for child in node.children:
        if child.type == "attribute_list" and attr_name in _attribute_names(child, src):
            return True
    return False


def _first_field_name(field_node: "Node", src: str) -> str | None:
    for child in field_node.children:
        if child.type == "variable_declaration":
            for sub in child.children:
                if sub.type == "variable_declarator":
                    for leaf in sub.children:
                        if leaf.type == "identifier":
                            return node_text(leaf, src).strip()
    return None


def _first_method_name(method_node: "Node", src: str) -> str | None:
    for child in method_node.children:
        if child.type == "identifier":
            return node_text(child, src).strip()
    return None


def _maybe_observable_property(
    field_node: "Node", src: str, file_info: FileInfo
) -> Symbol | None:
    if not _has_attribute(field_node, _OBSERVABLE_PROPERTY, src):
        return None
    field_name = _first_field_name(field_node, src)
    if not field_name:
        return None
    prop_name = _pascal_from_field(field_name)
    if not prop_name or prop_name == field_name:
        return None
    parent = enclosing_type_name(field_node, src, _CSHARP_TYPE_ANCESTORS)
    return build_synthetic_symbol(
        name=prop_name,
        kind="variable",
        signature=f"public T {prop_name} {{ get; set; }}",
        start_line=field_node.start_point[0] + 1,
        end_line=field_node.end_point[0] + 1,
        file_info=file_info,
        parent_name=parent,
    )


def _maybe_relay_command(
    method_node: "Node", src: str, file_info: FileInfo
) -> Symbol | None:
    if not _has_attribute(method_node, _RELAY_COMMAND, src):
        return None
    method_name = _first_method_name(method_node, src)
    if not method_name:
        return None
    command_name = f"{method_name}Command"
    parent = enclosing_type_name(method_node, src, _CSHARP_TYPE_ANCESTORS)
    return build_synthetic_symbol(
        name=command_name,
        kind="variable",
        signature=f"public IRelayCommand {command_name} {{ get; }}",
        start_line=method_node.start_point[0] + 1,
        end_line=method_node.end_point[0] + 1,
        file_info=file_info,
        parent_name=parent,
    )


def csharp_synthetic_symbols(
    root: "Node", src: str, file_info: FileInfo
) -> list[Symbol]:
    """Emit synthetic symbols for CommunityToolkit MVVM attributes."""
    out: list[Symbol] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type == "field_declaration":
            sym = _maybe_observable_property(node, src, file_info)
            if sym is not None:
                out.append(sym)
        elif node.type == "method_declaration":
            sym = _maybe_relay_command(node, src, file_info)
            if sym is not None:
                out.append(sym)
        stack.extend(node.children)
    return out
