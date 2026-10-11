"""Java import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_java_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Java import declarations.

    ``import static com.foo.Bar.member`` binds ``member`` and uses its
    declaring type, so ``Bar`` joins the imported names. It gets no binding:
    a static import does not bring the type's bare name into scope.
    """
    kinds = {child.type for child in stmt_node.children}
    # ``import static a.b.C.*`` parses ``C`` as the last name, already the type.
    names_member = "static" in kinds and "asterisk" not in kinds
    for child in stmt_node.children:
        if child.type == "scoped_identifier":
            owner, _, local = node_text(child, src).rpartition(".")
            if local == "*":
                return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
            names = [local]
            if names_member and owner:
                names.append(owner.rpartition(".")[2])
            return names, [NamedBinding(local_name=local, exported_name=local, source_file=None)]
        if child.type == "asterisk":
            return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
    return [], []
