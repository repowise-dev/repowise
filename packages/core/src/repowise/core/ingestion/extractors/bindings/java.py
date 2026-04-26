"""Java import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_java_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Java import declarations."""
    for child in stmt_node.children:
        if child.type == "scoped_identifier":
            full = node_text(child, src)
            local = full.rsplit(".", 1)[-1]
            if local == "*":
                return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
            return [local], [NamedBinding(local_name=local, exported_name=local, source_file=None)]
        if child.type == "asterisk":
            return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
    return [], []
