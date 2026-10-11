"""C / C++ include-binding extraction."""

from __future__ import annotations

from pathlib import PurePosixPath

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_cpp_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from C/C++ ``#include`` directives."""
    for child in stmt_node.children:
        if child.type in ("system_lib_string", "string_literal"):
            raw = node_text(child, src).strip().strip('<>"')
            if raw:
                stem = PurePosixPath(raw).stem
                return [stem], [
                    NamedBinding(
                        local_name=stem,
                        exported_name=None,
                        source_file=raw,
                        is_module_alias=True,
                    )
                ]
    return [], []
