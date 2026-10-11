"""Kotlin import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_kotlin_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Kotlin import declarations."""
    for child in stmt_node.children:
        if child.type == "qualified_identifier":
            full = node_text(child, src)
            parts = full.split(".")
            local = parts[-1]
            if local == "*":
                return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
            return [local], [NamedBinding(local_name=local, exported_name=local, source_file=None)]
    return [], []
