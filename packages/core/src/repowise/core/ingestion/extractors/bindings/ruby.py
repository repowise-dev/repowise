"""Ruby import-binding extraction."""

from __future__ import annotations

from pathlib import PurePosixPath

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_ruby_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Ruby require/require_relative calls."""
    method_node = None
    for child in stmt_node.children:
        if child.type == "identifier":
            method_node = child
            break
    method_name = node_text(method_node, src) if method_node else ""
    if method_name not in ("require", "require_relative"):
        return [], []

    for child in stmt_node.children:
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type == "string":
                    for sub in arg.children:
                        if sub.type == "string_content":
                            path = node_text(sub, src)
                            stem = PurePosixPath(path).stem
                            return [stem], [
                                NamedBinding(
                                    local_name=stem,
                                    exported_name=None,
                                    source_file=path,
                                    is_module_alias=True,
                                )
                            ]
    return [], []
