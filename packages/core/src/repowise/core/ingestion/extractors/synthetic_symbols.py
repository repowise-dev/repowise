"""Per-language synthetic-symbol passes.

Some frameworks rely on source generators that emit symbols at compile
time. Those generated symbols don't appear in the AST of the user's
source file, but they ARE referenced by name from other code (XAML
bindings, code-behind, etc.). Without representing them in the symbol
table, every binding to such a name looks like an unresolved reference
and the user-visible symbol that "backs" it looks orphaned.

This module synthesises those names directly from the user-authored
attributes. No filesystem coupling to ``obj/Generated/*.g.cs``.

Supported today (CommunityToolkit.Mvvm):
  - ``[ObservableProperty] private string _name;`` → property ``Name``
  - ``[RelayCommand] private void Save() { … }`` → method ``SaveCommand``

The dispatcher is keyed by language and returns extra ``Symbol``
instances that the parser appends to its main symbol list. Adding a
new framework's generator support is one function plus one entry in
``_SYNTHETIC_EXTRACTORS``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..models import FileInfo, Symbol
from .helpers import node_text

if TYPE_CHECKING:
    from tree_sitter import Node


# Match the *bare* attribute name inside ``[Name]`` or ``[Name(args)]``.
# The grammar nests attributes under ``attribute_list`` / ``attribute``
# nodes; we look for the bare name text rather than relying on field
# names so the pass is robust across grammar versions.
_OBSERVABLE_PROPERTY = "ObservableProperty"
_RELAY_COMMAND = "RelayCommand"

_FIELD_NAME_TO_PROP_RE = re.compile(r"^_?([A-Za-z])(.*)$")


def _pascal_from_field(field_name: str) -> str | None:
    """Convert ``_name`` / ``m_name`` / ``name`` → ``Name``.

    Returns ``None`` when the result would be empty or non-identifier.
    """
    stripped = field_name.lstrip("_")
    if stripped.startswith("m_"):
        stripped = stripped[2:]
    match = _FIELD_NAME_TO_PROP_RE.match(stripped)
    if not match:
        return None
    first, rest = match.groups()
    return first.upper() + rest


def _attribute_names(attr_list_node: Node, src: str) -> set[str]:
    """Return the set of bare attribute names declared in an ``attribute_list``.

    ``[ObservableProperty]`` → ``{"ObservableProperty"}``.
    ``[RelayCommand(CanExecute=nameof(CanSave))]`` → ``{"RelayCommand"}``.
    """
    names: set[str] = set()
    for child in attr_list_node.children:
        if child.type != "attribute":
            continue
        # The first ``identifier`` / ``qualified_name`` child is the
        # attribute's name. Strip any namespace prefix.
        for sub in child.children:
            if sub.type in ("identifier", "qualified_name"):
                text = node_text(sub, src).strip()
                names.add(text.split(".")[-1])
                break
    return names


def _csharp_synthetic_symbols(
    root: Node, src: str, file_info: FileInfo
) -> list[Symbol]:
    """Emit synthetic symbols for CommunityToolkit MVVM attributes.

    Walks the file once looking for ``field_declaration`` and
    ``method_declaration`` nodes; for each, scans preceding
    ``attribute_list`` children for the trigger attribute.
    """
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


def _maybe_observable_property(
    field_node: Node, src: str, file_info: FileInfo
) -> Symbol | None:
    """If *field_node* is ``[ObservableProperty] private T _name;``, synthesise ``Name``."""
    if not _has_attribute(field_node, _OBSERVABLE_PROPERTY, src):
        return None
    # Locate the variable_declarator → identifier for the field name.
    field_name = _first_field_name(field_node, src)
    if not field_name:
        return None
    prop_name = _pascal_from_field(field_name)
    if not prop_name or prop_name == field_name:
        return None
    parent = _enclosing_type_name(field_node, src)
    start_line = field_node.start_point[0] + 1
    end_line = field_node.end_point[0] + 1
    return _build_synthetic_symbol(
        name=prop_name,
        kind="variable",  # auto-properties surface as ``variable`` everywhere else
        signature=f"public T {prop_name} {{ get; set; }}",
        start_line=start_line,
        end_line=end_line,
        file_info=file_info,
        parent_name=parent,
    )


def _maybe_relay_command(
    method_node: Node, src: str, file_info: FileInfo
) -> Symbol | None:
    """If *method_node* is ``[RelayCommand] void Save() { … }``, synthesise ``SaveCommand``."""
    if not _has_attribute(method_node, _RELAY_COMMAND, src):
        return None
    method_name = _first_method_name(method_node, src)
    if not method_name:
        return None
    command_name = f"{method_name}Command"
    parent = _enclosing_type_name(method_node, src)
    start_line = method_node.start_point[0] + 1
    end_line = method_node.end_point[0] + 1
    return _build_synthetic_symbol(
        name=command_name,
        kind="variable",
        signature=f"public IRelayCommand {command_name} {{ get; }}",
        start_line=start_line,
        end_line=end_line,
        file_info=file_info,
        parent_name=parent,
    )


def _has_attribute(node: Node, attr_name: str, src: str) -> bool:
    for child in node.children:
        if child.type == "attribute_list" and attr_name in _attribute_names(child, src):
            return True
    return False


def _first_field_name(field_node: Node, src: str) -> str | None:
    for child in field_node.children:
        if child.type == "variable_declaration":
            for sub in child.children:
                if sub.type == "variable_declarator":
                    for leaf in sub.children:
                        if leaf.type == "identifier":
                            return node_text(leaf, src).strip()
    return None


def _first_method_name(method_node: Node, src: str) -> str | None:
    for child in method_node.children:
        if child.type == "identifier":
            return node_text(child, src).strip()
    return None


def _enclosing_type_name(node: Node, src: str) -> str | None:
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type in (
            "class_declaration",
            "struct_declaration",
            "record_declaration",
        ):
            name_node = ancestor.child_by_field_name("name")
            if name_node is not None:
                return node_text(name_node, src).strip()
        ancestor = ancestor.parent
    return None


def _build_synthetic_symbol(
    *,
    name: str,
    kind: str,
    signature: str,
    start_line: int,
    end_line: int,
    file_info: FileInfo,
    parent_name: str | None,
) -> Symbol:
    sym_id = (
        f"{file_info.path}::{parent_name}::{name}"
        if parent_name
        else f"{file_info.path}::{name}"
    )
    qualified = (
        f"{file_info.path}.{parent_name}.{name}"
        if parent_name
        else f"{file_info.path}.{name}"
    )
    return Symbol(
        id=sym_id,
        name=name,
        qualified_name=qualified,
        kind=kind,  # type: ignore[arg-type]
        signature=signature,
        start_line=start_line,
        end_line=end_line,
        docstring=None,
        decorators=[],
        visibility="public",
        is_async=False,
        language=file_info.language,
        parent_name=parent_name,
    )


_SYNTHETIC_EXTRACTORS: dict[str, Callable[[Node, str, FileInfo], list[Symbol]]] = {
    "csharp": _csharp_synthetic_symbols,
}


def extract_synthetic_symbols(
    root: Node, src: str, file_info: FileInfo
) -> list[Symbol]:
    """Dispatch to the language-appropriate synthetic-symbol extractor.

    Returns an empty list for languages with no registered extractor.
    """
    fn = _SYNTHETIC_EXTRACTORS.get(file_info.language)
    if fn is None:
        return []
    return fn(root, src, file_info)
