"""Elixir directive expansion: one module path per named module.

``alias Foo.{Bar, Baz}`` is a single statement naming two modules, and a
single ``@import.module`` capture cannot carry both. The grammar parses the
brace group as a ``dot`` whose ``left`` is the base ``alias`` and whose
``right`` is a ``tuple`` of member aliases, so the expansion is structural
here where the regex tier does it with a brace-group pattern.
"""

from __future__ import annotations

from tree_sitter import Node

from ..helpers import node_text


def elixir_import_modules(module_node: Node, src: str) -> list[str]:
    """Return every module path named by one alias/import/require/use.

    Returns an empty list for a form with no Elixir module to resolve --
    ``import :math`` (an Erlang atom module) and ``alias __MODULE__.Foo``
    (a self-reference), matching what the regex tier already skips.
    """
    if module_node.type == "alias":
        text = node_text(module_node, src).strip()
        return [text] if text else []
    if module_node.type != "dot":
        return []
    left = module_node.child_by_field_name("left")
    right = module_node.child_by_field_name("right")
    if left is None or right is None or left.type != "alias" or right.type != "tuple":
        return []
    base = node_text(left, src).strip()
    if not base:
        return []
    members = [
        node_text(child, src).strip() for child in right.named_children if child.type == "alias"
    ]
    return [f"{base}.{member}" for member in members if member]
