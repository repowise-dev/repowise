"""Swift import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_swift_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Swift import declarations."""
    for child in stmt_node.children:
        if child.type == "identifier":
            full = node_text(child, src)
            local = full.split(".")[-1]
            return [local], [
                NamedBinding(
                    local_name=local,
                    exported_name=None,
                    source_file=None,
                    is_module_alias=True,
                )
            ]
    return [], []
