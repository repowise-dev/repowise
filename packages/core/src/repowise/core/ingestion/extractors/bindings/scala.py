"""Scala import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_scala_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Scala import declarations."""
    names: list[str] = []
    bindings: list[NamedBinding] = []

    full_text = node_text(stmt_node, src).strip()
    if full_text.startswith("import "):
        full_text = full_text[7:].strip()

    has_selectors = False
    for child in stmt_node.children:
        if child.type == "namespace_selectors":
            has_selectors = True
            for sel_child in child.children:
                if sel_child.type == "arrow_renamed_identifier":
                    parts = node_text(sel_child, src).split("=>")
                    if len(parts) == 2:
                        exported = parts[0].strip()
                        local = parts[1].strip()
                        names.append(local)
                        bindings.append(
                            NamedBinding(
                                local_name=local, exported_name=exported, source_file=None
                            )
                        )
                elif sel_child.type == "identifier":
                    local = node_text(sel_child, src)
                    names.append(local)
                    bindings.append(
                        NamedBinding(local_name=local, exported_name=local, source_file=None)
                    )
        elif child.type == "namespace_wildcard":
            has_selectors = True
            names.append("*")
            bindings.append(NamedBinding(local_name="*", exported_name=None, source_file=None))

    if not has_selectors:
        parts = full_text.split(".")
        local = parts[-1].strip()
        if local and local != "_":
            names.append(local)
            bindings.append(NamedBinding(local_name=local, exported_name=local, source_file=None))

    return names, bindings
