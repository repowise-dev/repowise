"""PHP import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_php_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from PHP use declarations."""
    names: list[str] = []
    bindings: list[NamedBinding] = []

    for child in stmt_node.children:
        if child.type == "namespace_use_clause":
            qualified = ""
            alias = None
            saw_as = False
            for sub in child.children:
                if sub.type == "qualified_name":
                    qualified = node_text(sub, src)
                elif sub.type == "as":
                    saw_as = True
                elif sub.type == "name" and saw_as:
                    alias = node_text(sub, src)

            if not qualified:
                continue

            local = qualified.rsplit("\\", 1)[-1] if "\\" in qualified else qualified
            effective_local = alias if alias else local
            names.append(effective_local)
            bindings.append(
                NamedBinding(
                    local_name=effective_local, exported_name=qualified, source_file=None
                )
            )

    return names, bindings
