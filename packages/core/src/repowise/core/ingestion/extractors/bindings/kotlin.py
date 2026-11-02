"""Kotlin import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_kotlin_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Kotlin import declarations.

    The grammar keeps ``.*`` and ``as Alias`` outside the
    ``qualified_identifier``: ``import a.b.*`` names the package ``a.b``, and
    ``import a.b.Foo as Bar`` puts ``Bar`` in a sibling ``identifier``. A
    wildcard binds no name of its own; an alias binds ``Bar`` to ``Foo``.

    Kotlin allows no newline between ``as`` and the alias, but the grammar
    recovers a dangling ``import a.b.Foo as`` by taking the next line's first
    word, so an alias on a later line is not one. Backticks stay on the alias:
    symbol names and call targets keep theirs too, so ``as `in``` matches the
    call ```in`()``.
    """
    exported = alias = None
    as_row = None
    for child in stmt_node.children:
        if child.type == "*":
            return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
        if child.type == "qualified_identifier":
            exported = node_text(child, src).rpartition(".")[2]
        elif child.type == "as":
            as_row = child.end_point[0]
        elif child.type == "identifier" and exported and child.start_point[0] == as_row:
            alias = node_text(child, src)
    if not exported:
        return [], []
    return [exported], [
        NamedBinding(local_name=alias or exported, exported_name=exported, source_file=None)
    ]
